"""流水线编排：数据接入 → 双模式复盘 → 规则/ML 回测 → 容量/达标 → 日报。所有结果缓存到 DuckDB。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from .backtest.report import evaluate_symbol, tier_attainment_table
from .backtest.stress import run_stress
from .calendar_utils import previous_trading_day, trading_days
from .capital.capacity import capacity_table
from .capital.tiers import load_tiers
from .config import get_settings, research_symbols
from .costs.model import CostModel, load_cost_profile
from .data.quality import apply_quality_flags, coverage_report, research_ready
from .data.registry import DataPlan, build_data_plan
from .data.schema import Venue
from .data.simulated import regime_for_day
from .data.store import Store
from .hindsight.optimal_path import OptimalPathResult, optimal_path
from .hindsight.portfolio import PortfolioResult, portfolio_optimal
from .ml.predict import ai_signals_for_day
from .ml.train import WalkForwardOutput, walk_forward_train
from .replay.compare import ABComparison, run_ab
from .reports.charts import render_ab_chart_html
from .strategies.filters import CostAwareStrategy
from .strategies.rules import all_strategies, make_strategy


@dataclass
class IngestSummary:
    symbol: str
    venue: str
    days_requested: int
    days_ingested: int
    bars: int
    source: str
    coverage: list[dict]


class Pipeline:
    def __init__(self, store: Store | None = None, plan: DataPlan | None = None, cost_profile: str | None = None,
                 capital_usd: float = 10_000.0, probe: bool = True):
        self.settings = get_settings()
        self.store = store or Store()
        self.plan = plan or build_data_plan(do_probe=probe)
        self.cost_profile_name = cost_profile or "token_dex"
        self.profile = load_cost_profile(self.cost_profile_name)
        self.cost = CostModel(self.profile)
        self.capital_usd = float(capital_usd)
        self.tiers = load_tiers()
        self._ml_cache: dict[tuple, WalkForwardOutput] = {}
        self._bt_cache: dict[tuple, dict] = {}
        self._ab_cache: dict[tuple, ABComparison] = {}
        self._hs_cache: dict[tuple, object] = {}
        self.participation_rate = float(load_yaml_participation())

    # ------------------------------------------------------------------ 数据
    def symbols(self, tradable_only: bool = False) -> list[str]:
        return [s["ticker"] for s in research_symbols(tradable_only)]

    def ingest(self, symbols: list[str] | None, start: date, end: date, with_token: bool = True) -> list[IngestSummary]:
        out = []
        days = trading_days(start, end)
        for sym in symbols or self.symbols():
            for venue, src in ((Venue.NATIVE, self.plan.native_source), (Venue.TOKEN, self.plan.token_source)):
                if venue == Venue.TOKEN and not with_token:
                    continue
                have = set(self.store.available_days(sym, venue))
                n_bars, cov, src_name = 0, [], ""
                for d in days:
                    if d in have:
                        continue
                    try:
                        if getattr(src, "is_simulated", False):
                            bars = src.fetch_minute_bars(sym, d, venue=venue)
                        else:
                            bars = src.fetch_minute_bars(sym, d)
                    except (PermissionError, LookupError) as e:
                        self.plan.blockers.append(f"{sym} {venue} {d}: {e}")
                        break
                    bars = apply_quality_flags(bars)
                    bars = research_ready(bars)
                    n_bars += self.store.upsert_bars(bars)
                    cov.append(coverage_report(bars, d))
                    src_name = str(bars["source"].iloc[0]) if not bars.empty else src_name
                out.append(IngestSummary(sym, venue, len(days), len(set(self.store.available_days(sym, venue)) & set(days)),
                                         n_bars, src_name or getattr(src, "name", "?"), cov))
        return out

    def bars(self, symbol: str, start: date | None = None, end: date | None = None, venue: str = Venue.NATIVE) -> pd.DataFrame:
        return self.store.load_bars(symbol, start, end, venue)

    def day_bars(self, symbol: str, day: date, venue: str = Venue.NATIVE) -> pd.DataFrame:
        return self.store.load_bars(symbol, day, day, venue)

    def latest_day(self, symbol: str = "NVDA") -> date | None:
        d = self.store.available_days(symbol)
        return d[-1] if d else None

    def rankings(self, day: date) -> pd.DataFrame:
        rows = []
        for sym in self.symbols():
            b = self.day_bars(sym, day)
            if b.empty:
                continue
            tb = self.day_bars(sym, day, Venue.TOKEN)
            rows.append({"symbol": sym, "open": float(b["open"].iloc[0]), "close": float(b["close"].iloc[-1]),
                         "high": float(b["high"].max()), "low": float(b["low"].min()),
                         "change_pct": float(b["close"].iloc[-1] / b["open"].iloc[0] - 1) * 100,
                         "range_pct": float(b["high"].max() / b["low"].min() - 1) * 100,
                         "volume": float(b["volume"].sum()), "amount_usd": float(b["amount"].sum()),
                         "token_close": float(tb["close"].iloc[-1]) if not tb.empty else None,
                         "token_basis_bps": float((tb["close"].iloc[-1] / b["close"].iloc[-1] - 1) * 1e4) if not tb.empty else None,
                         "source": str(b["source"].iloc[0]), "regime_sim": regime_for_day(sym, day) if self.plan.is_simulated else None,
                         "tradable": sym in self.symbols(tradable_only=True)})
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("change_pct", ascending=False).reset_index(drop=True)
            df.index = df.index + 1
            df.insert(0, "rank", df.index)
        return df

    # ------------------------------------------------------------------ 复盘
    def strategies(self, cost_aware: bool = True) -> list:
        base = all_strategies()
        if not cost_aware:
            return base
        return base + [CostAwareStrategy(make_strategy(k), self.cost, self.capital_usd) for k in ("vwap", "ema_trend", "breakout", "mean_reversion")]

    def replay_day(self, symbol: str, day: date, capital_usd: float | None = None, render: bool = True) -> ABComparison:
        cap = capital_usd or self.capital_usd
        key = (symbol, day, cap, self.cost_profile_name)
        if key in self._ab_cache:
            return self._ab_cache[key]
        bars = self.day_bars(symbol, day)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据，请先 ingest")
        cm = CostModel(self.profile)
        ab = run_ab(bars, self.strategies(), cm, cap, self.cost_profile_name)
        self._ab_cache[key] = ab
        self.store.save_json("replay_results", run_id=uuid.uuid4().hex, symbol=symbol, day=day, mode="AB",
                             payload=_json_safe(ab.to_dict()), created_at=datetime.now(timezone.utc))
        if render:
            out = self.settings.resolve_path(self.settings.report_dir) / "charts" / f"{symbol}_{day}_{self.cost_profile_name}.html"
            render_ab_chart_html(bars, ab, out_path=out)
        return ab

    # ------------------------------------------------------------------ 回测/ML
    def backtest(self, symbol: str, train_days: int = 20, test_days: int = 5, strategy_keys: list[str] | None = None) -> dict:
        key = (symbol, train_days, test_days, tuple(strategy_keys or ()), self.capital_usd, self.cost_profile_name)
        if key in self._bt_cache:
            return self._bt_cache[key]
        bars = self.bars(symbol)
        if bars.empty:
            raise LookupError(f"{symbol} 无数据")
        res = evaluate_symbol(bars, CostModel(self.profile), self.capital_usd, self.cost_profile_name, strategy_keys, train_days, test_days)
        res["n_days"] = len(set(pd.to_datetime(bars["ts_ny"]).dt.date))
        res["data_is_simulated"] = self.plan.is_simulated
        self._bt_cache[key] = res
        self.store.save_json("backtest_runs", run_id=uuid.uuid4().hex, name=f"rules:{symbol}",
                             payload=_json_safe({k: v for k, v in res.items() if not k.startswith("_")}), created_at=datetime.now(timezone.utc))
        return res

    def train_ml(self, symbol: str, train_days: int = 40, test_days: int = 5, horizons=(5, 15, 30, 60)) -> WalkForwardOutput:
        key = (symbol, train_days, test_days, horizons, self.capital_usd, self.cost_profile_name)
        if key in self._ml_cache:
            return self._ml_cache[key]
        bars = self.bars(symbol)
        wf = walk_forward_train(bars, CostModel(self.profile), self.capital_usd, train_days, test_days, horizons=horizons,
                                regime_fn=regime_for_day if self.plan.is_simulated else None)
        self._ml_cache[key] = wf
        return wf

    def ai_signals(self, symbol: str, day: date) -> pd.DataFrame:
        wf = self.train_ml(symbol)
        if not wf.final_models:
            return pd.DataFrame()
        return ai_signals_for_day(self.day_bars(symbol, day), wf.final_models)

    def stress(self, symbol: str, strategy_key: str = "vwap", days: int = 10) -> pd.DataFrame:
        bars = self.bars(symbol)
        ds = sorted(set(pd.to_datetime(bars["ts_ny"]).dt.date))[-days:]
        sub = bars[pd.to_datetime(bars["ts_ny"]).dt.date.isin(ds)]
        return run_stress(sub, make_strategy(strategy_key), self.profile, self.capital_usd)

    # ------------------------------------------------------------------ 本金/容量
    def capacity(self, symbol: str | None = None) -> pd.DataFrame:
        adv = None
        if symbol:
            b = self.bars(symbol)
            if not b.empty:
                adv = float(b.groupby(pd.to_datetime(b["ts_ny"]).dt.date)["amount"].sum().mean())
        return capacity_table(self.tiers, self.profile, adv_usd=adv)

    def tier_attainment(self, symbol: str, strategy_key: str | None = None) -> pd.DataFrame:
        res = self.backtest(symbol)
        key = strategy_key or res["best_oos_strategy"]
        ev = next((e for e in res["_evals"] if e.strategy_key == key), None)
        if ev is None:
            return pd.DataFrame()
        tbl = tier_attainment_table(ev.oos_daily["net_return"], self.tiers)
        tbl.insert(0, "strategy", key)
        tbl.insert(0, "symbol", symbol)
        return tbl

    # ------------------------------------------------------------------ 事后最优路径（核心）
    def hindsight_bars(self, symbol: str, day: date, venue: str = Venue.TOKEN) -> pd.DataFrame:
        """事后最优研究使用代币成交价（token venue）；若无代币数据则明确回退原生并标注。"""
        b = self.day_bars(symbol, day, venue)
        if b.empty and venue == Venue.TOKEN:
            b = self.day_bars(symbol, day, Venue.NATIVE)
            if not b.empty:
                b = b.copy()
                b.attrs["fallback_native"] = True
        return b

    def hindsight_symbol(self, symbol: str, day: date, capital_usd: float, target_return: float | None = None,
                         venue: str = Venue.TOKEN) -> OptimalPathResult:
        key = ("hs", symbol, day, round(capital_usd, 2), target_return, self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.hindsight_bars(symbol, day, venue)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        res = optimal_path(bars, CostModel(self.profile), capital_usd, symbol=symbol, participation_rate=self.participation_rate,
                           target_return=target_return, cost_profile=self.cost_profile_name)
        if bars.attrs.get("fallback_native"):
            res.notes.insert(0, "警告：无代币成交K线，本结果使用原生股票价格，不能视为代币成交价")
            res.unverifiable = True
        self._hs_cache[key] = res
        return res

    def hindsight_tiers(self, symbol: str, day: date, points: tuple[str, ...] | None = None, venue: str = Venue.TOKEN) -> list[dict]:
        out = []
        for t in self.tiers.tiers:
            for pt, cap in t.representative_capitals(points or self.tiers.representative_points).items():
                r = self.hindsight_symbol(symbol, day, cap, target_return=t.target_daily_pct[0] / 100, venue=venue)
                out.append({"tier": t.id, "point": pt, "capital_usd": cap, "target_daily_pct": list(t.target_daily_pct),
                            "period_days": t.period_days, "result": r})
        return out

    def hindsight_portfolio(self, day: date, capital_usd: float, target_return: float | None = None,
                            symbols: list[str] | None = None, venue: str = Venue.TOKEN) -> PortfolioResult:
        syms = symbols or self.symbols(tradable_only=True)
        key = ("hsp", day, round(capital_usd, 2), target_return, tuple(syms), self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = {s: self.hindsight_bars(s, day, venue) for s in syms}
        bars = {s: b for s, b in bars.items() if not b.empty}
        res = portfolio_optimal(bars, CostModel(self.profile), capital_usd, cost_profile=self.cost_profile_name,
                                participation_rate=self.participation_rate, target_return=target_return)
        self._hs_cache[key] = res
        return res

    def hindsight_summary(self, day: date, symbols: list[str] | None = None, venue: str = Venue.TOKEN) -> pd.DataFrame:
        rows = []
        for sym in symbols or self.symbols():
            try:
                for row in self.hindsight_tiers(sym, day, venue=venue):
                    r: OptimalPathResult = row["result"]
                    rows.append({"symbol": sym, "tier": row["tier"], "point": row["point"], "capital_usd": row["capital_usd"],
                                 "target_pct": row["target_daily_pct"][0], "max_net_return_pct": r.max_net_return * 100,
                                 "max_net_profit_usd": r.max_net_profit_usd, "n_trades": r.n_trades, "target_met": r.target_met,
                                 "min_trades_for_target": r.min_trades_for_target, "max_deployed_usd": r.max_deployed_usd,
                                 "liquidity_capped": r.liquidity_capped_any, "unverifiable": r.unverifiable,
                                 "tradable": sym in self.symbols(tradable_only=True), "source": r.source})
            except LookupError:
                continue
        return pd.DataFrame(rows)

    def status(self) -> dict:
        return {"data_mode": self.plan.mode, "banner": self.plan.banner(), "blockers": self.plan.blockers,
                "probes": [p.__dict__ for p in self.plan.probes], "cost_profile": self.cost_profile_name,
                "cost": self.cost.describe(), "capital_usd": self.capital_usd, "tier_issues": self.tiers.issues,
                "symbols": research_symbols(), "store": self.store.sources_summary().to_dict(orient="records"),
                "execution": {"mode": "research_only", "live_trading": False}}


def load_yaml_participation() -> float:
    from .config import load_yaml
    return float(load_yaml("costs.yaml").get("participation_rate", 0.1))


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def default_window(n_days: int = 70, end: date | None = None) -> tuple[date, date]:
    end = end or previous_trading_day(date.today())
    days = trading_days(date(end.year - 1, end.month, end.day), end)
    return days[-n_days], end
