"""流水线编排：数据接入 → 双模式复盘 → 规则/ML 回测 → 容量/达标 → 日报。所有结果缓存到 DuckDB。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .backtest.report import evaluate_symbol, tier_attainment_table
from .backtest.stress import run_stress
from .calendar_utils import previous_trading_day, trading_days
from .capital.capacity import capacity_table
from .capital.tiers import load_tiers
from .config import asset_class_of, get_settings, research_symbols
from .costs.model import CostModel, load_cost_profile, profile_for_asset
from .data.quality import apply_quality_flags, coverage_report, research_ready
from .data.registry import DataPlan, build_data_plan
from .data.schema import Venue
from .data.simulated import regime_for_day
from .data.store import Store
from .hindsight.optimal_path import OptimalPathResult, optimal_path
from .hindsight.portfolio import PortfolioResult, portfolio_optimal
from .simple_sim.engine import SimpleSimResult, load_simple_config, simulate_day
from .simple_sim.multi import MultiResult, auto_search, failure_analysis, theoretical_optimum
from .decision.analysis import classify as engine_classify
from .hindsight.feasibility import FeasibilityResult, feasibility
from .decision.capability import ProductCapability, capability_for
from .decision.engine import DayRun, EngineParams, load_engine_config, run_engine_day
from .decision.optimize import TierOptimization, optimize_tier
from .rotation.engine import RotDayRun, RotParams, load_rotation_config, run_rotation_day
from .rotation.strategies import StrategySpec, build_specs
from .rotation.universe import AssetDay, make_asset_day
from .target_model.config import load_target_config, target_for_capital
from .target_model.strategy import StrategyDayResult, TargetSeekingStrategy, run_strategy_day
from .target_model.teaching import TargetPathResult, teaching_path
from .target_model.walkforward import TargetWFResult, walk_forward_target
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
        self.target_cfg = load_target_config()
        self.simple_cfg = load_simple_config()
        self.engine_cfg = load_engine_config()
        self._caps: dict[str, ProductCapability] = {}
        self._profile_cache: dict[str, object] = {self.cost_profile_name: self.profile}
        try:
            from .config import load_yaml as _ly
            self.playbook_cfg = _ly("playbook.yaml")
        except FileNotFoundError:
            self.playbook_cfg = {}
        self.participation_rate = float(load_yaml_participation())

    # ------------------------------------------------------------------ 数据
    def symbols(self, tradable_only: bool = False, asset_class: str | None = "stock") -> list[str]:
        return [s["ticker"] for s in research_symbols(tradable_only, asset_class)]

    def crypto_symbols(self, tradable_only: bool = False) -> list[str]:
        return self.symbols(tradable_only, "crypto")

    def all_symbols(self, tradable_only: bool = False) -> list[str]:
        return self.symbols(tradable_only, None)

    def profile_for(self, symbol: str):
        """按资产类别返回成本档（加密货币映射到 crypto_spot 档）。"""
        name = profile_for_asset(self.cost_profile_name, asset_class_of(symbol))
        if name not in self._profile_cache:
            self._profile_cache[name] = load_cost_profile(name)
        return self._profile_cache[name]

    def ingest_crypto(self, symbols: list[str] | None, start: date, end: date) -> list[IngestSummary]:
        """接入加密货币 24 小时 1 分钟 K 线（自然日，America/New_York）。live=Bitget 公开现货；否则 SIMULATED。"""
        out = []
        src = self.plan.crypto_source
        days = [d.date() for d in pd.date_range(start, end, freq="D")]
        for sym in symbols or self.crypto_symbols():
            have = set(self.store.available_days(sym, Venue.TOKEN, "1m"))
            n_bars, cov = 0, []
            for d in days:
                if d in have:
                    continue
                try:
                    bars = src.fetch_crypto_day(sym, d) if getattr(src, "is_simulated", False) else src.fetch_day(sym, d, "1m")
                except Exception as e:  # noqa: BLE001
                    self.plan.blockers.append(f"{sym} crypto {d}: {e}")
                    continue
                bars = apply_quality_flags(bars)
                bars = research_ready(bars)
                n_bars += self.store.upsert_bars(bars)
                if hasattr(src, "fetch_trades"):                  # live：逐笔成交 → token_trades（逐分钟流动性来源）
                    try:
                        tx = src.fetch_trades(sym, d)
                        if not tx.empty:
                            self.store.con.register("new_tx", tx)
                            self.store.con.execute("DELETE FROM token_trades WHERE symbol=? AND CAST(ts_ny AS DATE)=?", [sym, d])
                            self.store.con.execute("INSERT INTO token_trades SELECT * FROM new_tx")
                            self.store.con.unregister("new_tx")
                    except Exception as e:  # noqa: BLE001
                        self.plan.blockers.append(f"{sym} fills {d}: {e}")
                cov.append({"day": d.isoformat(), "bars": int(len(bars)), "simulated": bool(getattr(src, "is_simulated", False))})
            out.append(IngestSummary(sym, "crypto", len(days), len(cov) + len(have & set(days)), n_bars, getattr(src, "name", "?"), cov))
        return out

    def ingest(self, symbols: list[str] | None, start: date, end: date, with_token: bool = True,
               periods: tuple[str, ...] = ("1m",)) -> list[IngestSummary]:
        """接入历史K线。live 模式：代币 venue 来自 Bitget 官方 kline（多周期、分页），原生 venue 来自 Massive（可选）。"""
        out = []
        days = trading_days(start, end)
        for sym in symbols or self.symbols():
            for venue, src in ((Venue.NATIVE, self.plan.native_source), (Venue.TOKEN, self.plan.token_source)):
                if src is None or (venue == Venue.TOKEN and not with_token):
                    continue
                for per in (periods if venue == Venue.TOKEN else ("1m",)):
                    have = set(self.store.available_days(sym, venue, per))
                    n_bars, cov, src_name = 0, [], ""
                    for d in days:
                        if d in have:
                            continue
                        try:
                            if getattr(src, "is_simulated", False):
                                if per != "1m":
                                    continue
                                bars = src.fetch_minute_bars(sym, d, venue=venue)
                            elif venue == Venue.TOKEN:
                                bars = src.fetch_bars(sym, d, per)
                            else:
                                bars = src.fetch_minute_bars(sym, d)
                        except (PermissionError, LookupError) as e:
                            self.plan.blockers.append(f"{sym} {venue} {per} {d}: {e}")
                            break
                        except Exception as e:  # noqa: BLE001  —— API 错误如实记录，不中断其他标的
                            self.plan.blockers.append(f"{sym} {venue} {per} {d}: API 错误 {e}")
                            continue
                        bars = apply_quality_flags(bars)
                        bars = research_ready(bars)
                        n_bars += self.store.upsert_bars(bars)
                        if venue == Venue.TOKEN and per == "1m" and hasattr(src, "fetch_transactions"):
                            try:
                                tx = src.fetch_transactions(sym, d)
                                if not tx.empty:
                                    self.store.con.register("new_tx", tx)
                                    self.store.con.execute("DELETE FROM token_trades WHERE symbol=? AND CAST(ts_ny AS DATE)=?", [sym, d])
                                    self.store.con.execute("INSERT INTO token_trades SELECT * FROM new_tx")
                                    self.store.con.unregister("new_tx")
                            except Exception as e:  # noqa: BLE001
                                self.plan.blockers.append(f"{sym} 逐笔成交 {d}: {e}")
                        cov.append(coverage_report(bars, d))
                        src_name = str(bars["source"].iloc[0]) if not bars.empty else src_name
                    out.append(IngestSummary(sym, f"{venue}:{per}", len(days), len(set(self.store.available_days(sym, venue, per)) & set(days)),
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
            tb = self.day_bars(sym, day, Venue.TOKEN)
            if b.empty and not tb.empty:
                b = tb                      # live 模式无原生基准时用代币K线排名（来源列会显示）
            if b.empty:
                continue
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

    # ------------------------------------------------------------------ 固定收益目标 · 多次交易模型
    def target_for(self, capital_usd: float, target_pct: float | None = None) -> tuple[float | None, str | None, str]:
        if target_pct is not None:
            return target_pct / 100, None, f"显式指定目标 {target_pct:.3f}%"
        return target_for_capital(capital_usd, self.tiers, self.target_cfg.target_mode, self.target_cfg.overlap_rule)

    def target_teaching(self, symbol: str, day: date, capital_usd: float, target_pct: float | None = None,
                        venue: str = Venue.TOKEN) -> TargetPathResult:
        tgt, tid, note = self.target_for(capital_usd, target_pct)
        if tgt is None:
            raise ValueError(note)
        key = ("tt", symbol, day, round(capital_usd, 2), tgt, self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.hindsight_bars(symbol, day, venue)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        res = teaching_path(bars, CostModel(self.profile), capital_usd, tgt, self.target_cfg, symbol=symbol, cost_profile=self.cost_profile_name)
        res.notes.insert(0, note)
        if bars.attrs.get("fallback_native"):
            res.notes.insert(0, "警告：无代币成交K线，本结果使用原生股票价格")
            res.unverifiable = True
        self._hs_cache[key] = res
        return res

    def target_strategy_day(self, symbol: str, day: date, capital_usd: float, params: dict | None = None,
                            target_pct: float | None = None, venue: str = Venue.TOKEN) -> StrategyDayResult:
        tgt, _, _ = self.target_for(capital_usd, target_pct)
        bars = self.hindsight_bars(symbol, day, venue)
        strat = TargetSeekingStrategy(**params) if params else TargetSeekingStrategy()
        return run_strategy_day(bars, CostModel(self.profile), capital_usd, tgt or 0.01, self.target_cfg, strat, symbol=symbol)

    def target_walkforward(self, symbol: str, capital_usd: float, target_pct: float | None = None, train_days: int = 20,
                           test_days: int = 5, venue: str = Venue.TOKEN) -> TargetWFResult:
        tgt, _, _ = self.target_for(capital_usd, target_pct)
        if tgt is None:
            raise ValueError("本金无对应档位，请显式指定目标")
        key = ("twf", symbol, round(capital_usd, 2), tgt, train_days, test_days, self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.bars(symbol, venue=venue)
        if bars.empty:
            bars = self.bars(symbol, venue=Venue.NATIVE)
        res = walk_forward_target(bars, CostModel(self.profile), capital_usd, tgt, self.target_cfg, train_days, test_days)
        self._hs_cache[key] = res
        return res

    def target_capitals(self) -> list[dict]:
        """验证本金集合：七档中位 + 配置的重点验证规模（去重，按金额排序）。"""
        caps = {}
        for t in self.tiers.tiers:
            caps[round(t.representative_capitals(("mid",))["mid"], 2)] = t.id
        for c in self.target_cfg.validation_capitals:
            caps.setdefault(float(c), "验证点")
        return [{"capital_usd": c, "label": lbl} for c, lbl in sorted(caps.items())]

    def target_summary(self, day: date, symbols: list[str] | None = None, capitals: list[float] | None = None) -> pd.DataFrame:
        rows = []
        caps = capitals or [c["capital_usd"] for c in self.target_capitals()]
        for sym in symbols or self.symbols():
            for cap in caps:
                try:
                    r = self.target_teaching(sym, day, cap)
                except (LookupError, ValueError) as e:
                    rows.append({"symbol": sym, "capital_usd": cap, "error": str(e)})
                    continue
                rows.append({"symbol": sym, "capital_usd": cap, "target_pct": r.target_return * 100, "target_usd": r.target_usd,
                             "realized_net_usd": r.realized_net_usd, "realized_pct": r.realized_net_return * 100, "n_trades": r.n_buys,
                             "target_met": r.target_met, "target_hit_ts": r.target_hit_ts, "capital_utilization": r.capital_utilization,
                             "max_dd_usd": r.max_drawdown_usd, "partial_fills": r.partial_fills, "unverifiable": r.unverifiable,
                             "reasons": "；".join(r.reasons_not_met), "tradable": sym in self.symbols(tradable_only=True), "source": r.source})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ 简化版：多空多次交易 · 累计净利润达标（事后教学）
    def simple_day(self, symbol: str, day: date, capital_usd: float, target_usd: float | None = None, tier: str | None = None,
                   venue: str = Venue.TOKEN) -> SimpleSimResult:
        if target_usd is None:
            tgt, tier_id, note = target_for_capital(capital_usd, self.tiers, self.simple_cfg.target_mode, "first")
            if tgt is None:
                raise ValueError(note)
            target_usd, tier = tgt * capital_usd, tier or tier_id
        key = ("simple", symbol, day, round(capital_usd, 2), round(target_usd, 2), self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.hindsight_bars(symbol, day, venue)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        res = simulate_day(bars, CostModel(self.profile), capital_usd, target_usd, self.simple_cfg, symbol=symbol, tier=tier)
        if bars.attrs.get("fallback_native"):
            res.notes.insert(0, "警告：无代币成交K线，本结果使用原生股票价格")
            res.unverifiable = True
        self._hs_cache[key] = res
        return res

    def simple_tiers(self, symbol: str, day: date, points: tuple[str, ...] = ("mid",), venue: str = Venue.TOKEN) -> list[SimpleSimResult]:
        out = []
        for t in self.tiers.tiers:
            for pt, cap in t.representative_capitals(points).items():
                lo, hi = t.target_daily_pct
                pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(self.simple_cfg.target_mode, lo)
                try:
                    out.append(self.simple_day(symbol, day, cap, pct / 100 * cap, tier=f"{t.id}" + ("" if pt == "mid" else f"-{pt}"), venue=venue))
                except LookupError:
                    break
        return out

    # ------------------------------------------------------------------ 档位对比：理论最优(A) vs 可执行模拟(B) + 失败原因
    def compare_tier(self, symbol: str, day: date, capital_usd: float, target_usd: float, tier: str | None = None, venue: str = Venue.TOKEN) -> dict:
        key = ("cmp", symbol, day, round(capital_usd, 2), round(target_usd, 2), self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.hindsight_bars(symbol, day, venue)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        cm = CostModel(self.profile)
        best, tried = auto_search(bars, cm, capital_usd, target_usd, self.simple_cfg, symbol=symbol, tier=tier)
        theo = theoretical_optimum(bars, self.profile, capital_usd, target_usd, symbol=symbol)
        fa = failure_analysis(bars, cm, capital_usd, target_usd, self.simple_cfg, theo, best)
        if bars.attrs.get("fallback_native"):
            best.notes.insert(0, "警告：无代币成交K线，使用原生股票价格")
            best.unverifiable = True
        out = {"symbol": symbol, "day": str(day), "tier": tier, "capital_usd": capital_usd, "target_usd": target_usd,
               "theoretical": theo, "executable": best, "tried": tried, "analysis": fa, "bars": bars}
        self._hs_cache[key] = out
        return out

    def compare_tiers(self, symbol: str, day: date, points: tuple[str, ...] = ("mid",), venue: str = Venue.TOKEN) -> list[dict]:
        out = []
        for t in self.tiers.tiers:
            for pt, cap in t.representative_capitals(points).items():
                lo, hi = t.target_daily_pct
                pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(self.simple_cfg.target_mode, lo)
                try:
                    out.append(self.compare_tier(symbol, day, cap, pct / 100 * cap, tier=t.id + ("" if pt == "mid" else f"-{pt}"), venue=venue))
                except LookupError:
                    break
        return out

    def compare_summary(self, day: date, symbols: list[str] | None = None) -> pd.DataFrame:
        rows = []
        for sym in symbols or self.symbols():
            for c in self.compare_tiers(sym, day):
                th, ex, fa = c["theoretical"], c["executable"], c["analysis"]
                rows.append({"symbol": sym, "tier": c["tier"], "capital_usd": c["capital_usd"], "target_usd": c["target_usd"],
                             "theoretical_usd": th.max_net_profit_usd, "theoretical_trades": th.n_trades, "theoretical_met": th.target_met,
                             "executable_usd": ex.realized_net_usd, "executable_return_pct": ex.realized_net_return * 100,
                             "executable_trades": ex.n_trades, "n_long": ex.n_long, "n_short": ex.n_short, "max_open": ex.params.max_open,
                             "min_trade_net_pct": ex.params.min_trade_net_pct, "tranche_usd": fa["tranche_usd"],
                             "peak_utilization": ex.capital_utilization, "avg_utilization": ex.avg_utilization,
                             "liquidity_used_ratio": ex.liquidity_used_ratio, "trading_cost_usd": ex.trading_cost_usd,
                             "capital_cost_usd": ex.capital_cost_usd, "target_met": ex.target_met, "target_hit_ts": ex.target_hit_ts,
                             "round_trip_cost_pct": fa["round_trip_cost_pct"], "move_p90_pct": fa["move_p90_pct"],
                             "gap_to_target_usd": fa["gap_to_target_usd"], "unverifiable": ex.unverifiable,
                             "reasons": "；".join(fa["reasons"]), "tradable": sym in self.symbols(tradable_only=True)})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ 统一收益决策引擎（因果）
    def capability(self, symbol: str) -> ProductCapability:
        if symbol not in self._caps:
            api_asset = None
            src = self.plan.token_source
            if not self.plan.is_simulated and hasattr(src, "verify_assets"):
                try:
                    api_asset = src.verify_assets([symbol])[0]
                except Exception as e:  # noqa: BLE001
                    self.plan.blockers.append(f"{symbol} 能力核对失败: {e}")
            self._caps[symbol] = capability_for(symbol, api_asset)
        return self._caps[symbol]

    def engine_tier(self, symbol: str, day: date, capital_usd: float, target_usd: float, tier: str | None = None,
                    train_days: int = 20, test_days: int = 5, venue: str = Venue.TOKEN) -> dict:
        key = ("eng", symbol, day, round(capital_usd, 2), round(target_usd, 2), self.cost_profile_name, train_days, test_days, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars_day = self.hindsight_bars(symbol, day, venue)
        if bars_day.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        cap = self.capability(symbol)
        cm = CostModel(self.profile)
        hist = self.bars(symbol, venue=venue)
        if hist.empty:
            hist = self.bars(symbol, venue=Venue.NATIVE)
        # 滚动优化只用昨日之前的交易日（昨日复盘不得使用昨日信息）
        hist = hist[pd.to_datetime(hist["ts_ny"]).dt.date < day]
        opt = optimize_tier(hist, cm, capital_usd, target_usd, self.engine_cfg, cap, train_days, test_days) if len(set(pd.to_datetime(hist["ts_ny"]).dt.date)) >= train_days + test_days + 1 else None
        params = opt.final_params if opt else EngineParams()
        run = run_engine_day(bars_day, cm, capital_usd, target_usd, self.engine_cfg, params, cap, symbol)
        theo = theoretical_optimum(bars_day, self.profile, capital_usd, target_usd, symbol=symbol)
        cls = engine_classify(bars_day, cm, cap, run, theo.max_net_profit_usd, target_usd, float(self.engine_cfg.execution.get("participation_rate", 0.1)),
                              int(self.engine_cfg.execution.get("batch_bars", 5)), opt.oos_stats.attainment_rate if opt else None)
        out = {"symbol": symbol, "day": str(day), "tier": tier, "capital_usd": capital_usd, "target_usd": target_usd, "capability": cap,
               "run": run, "theoretical": theo, "optimization": opt, "classification": cls, "bars": bars_day}
        self._hs_cache[key] = out
        return out

    def engine_tiers(self, symbol: str, day: date, points: tuple[str, ...] = ("mid",), **kw) -> list[dict]:
        out = []
        for t in self.tiers.tiers:
            for pt, cap_usd in t.representative_capitals(points).items():
                lo, hi = t.target_daily_pct
                pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(self.simple_cfg.target_mode, lo)
                try:
                    out.append(self.engine_tier(symbol, day, cap_usd, pct / 100 * cap_usd, tier=t.id + ("" if pt == "mid" else f"-{pt}"), **kw))
                except LookupError:
                    break
        return out

    def engine_summary(self, day: date, symbols: list[str] | None = None) -> pd.DataFrame:
        rows = []
        for sym in symbols or self.symbols():
            for c in self.engine_tiers(sym, day):
                r, th, cls, opt, cap = c["run"], c["theoretical"], c["classification"], c["optimization"], c["capability"]
                rows.append({"symbol": sym, "tier": c["tier"], "capital_usd": c["capital_usd"], "target_usd": c["target_usd"],
                             "directions": "/".join(cap.directions) or "无", "verified_by_api": cap.verified_by_api,
                             "theoretical_usd": th.max_net_profit_usd, "net_usd": r.realized_net_usd, "net_return_pct": r.realized_net_return * 100,
                             "n_trades": r.n_trades, "n_long": r.n_long, "n_short": r.n_short, "win_rate": r.win_rate,
                             "max_dd_usd": r.max_drawdown_usd, "avg_utilization": r.avg_deployed_usd / c["capital_usd"], "exec_cost_usd": r.execution_cost_usd,
                             "funding_cost_usd": r.funding_cost_usd, "target_met": r.target_met, "params": r.params.to_dict(),
                             "oos_attainment": opt.oos_stats.attainment_rate if opt else None, "oos_mean_daily_return": opt.oos_stats.mean_daily_return if opt else None,
                             "oos_max_dd_pct": opt.oos_stats.max_drawdown_pct if opt else None, "oos_losing_day_rate": opt.oos_stats.losing_day_rate if opt else None,
                             "oos_days": opt.oos_stats.n_days if opt else 0, "tags": "、".join(cls["tags"]), "details": "；".join(cls["details"]),
                             "unverifiable": r.unverifiable, "tradable": sym in self.symbols(tradable_only=True)})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ 历史可行性（事后：A 理论最大 / B 执行约束下最大可实现）
    def tx_amount_per_bar(self, symbol: str, day: date) -> np.ndarray | None:
        """若存储了逐笔成交（trades 表），按分钟聚合成交额作为流动性来源。"""
        try:
            df = self.store.query("SELECT ts_ny, value FROM token_trades WHERE symbol=? AND CAST(ts_ny AS DATE)=?", [symbol, day])
        except Exception:  # noqa: BLE001
            return None
        if df.empty:
            return None
        bars = self.hindsight_bars(symbol, day)
        minute = pd.to_datetime(df["ts_ny"]).dt.floor("min")
        agg = df.groupby(minute)["value"].sum()
        idx = pd.to_datetime(bars["ts_ny"]).dt.floor("min")
        return agg.reindex(idx).fillna(0.0).to_numpy(float)

    def feasibility_tier(self, symbol: str, day: date, capital_usd: float, target_usd: float, tier: str | None = None, venue: str = Venue.TOKEN) -> FeasibilityResult:
        key = ("feas", symbol, day, round(capital_usd, 2), round(target_usd, 2), self.cost_profile_name, venue)
        if key in self._hs_cache:
            return self._hs_cache[key]
        bars = self.hindsight_bars(symbol, day, venue)
        if bars.empty:
            raise LookupError(f"{symbol} {day} 无K线数据")
        cap = self.capability(symbol)
        r = feasibility(bars, self.profile_for(symbol), capital_usd, target_usd, allow_short=cap.short, borrow_annual=cap.short_borrow_rate_annual or 0.05,
                        participation_rate=float(self.engine_cfg.execution.get("participation_rate", 0.1)),
                        tx_amount_per_bar=self.tx_amount_per_bar(symbol, day),
                        exec_cfg={"capital_cost_annual": float(self.engine_cfg.execution.get("capital_cost_annual", 0.05))}, symbol=symbol, tier=tier)
        if cap.verification_status != "verified":
            r.limits.insert(0, "品种未经官方 API 核实（当前环境无法核实）：以下为流水线验证结果")
        if bars.attrs.get("fallback_native"):
            r.limits.insert(0, "警告：无代币成交K线，使用原生股票价格")
        self._hs_cache[key] = r
        return r

    def feasibility_tiers(self, symbol: str, day: date, points: tuple[str, ...] = ("mid",), venue: str = Venue.TOKEN) -> list[FeasibilityResult]:
        out = []
        for t in self.tiers.tiers:
            for pt, cap_usd in t.representative_capitals(points).items():
                lo, hi = t.target_daily_pct
                pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(self.simple_cfg.target_mode, lo)
                try:
                    out.append(self.feasibility_tier(symbol, day, cap_usd, pct / 100 * cap_usd, tier=t.id + ("" if pt == "mid" else f"-{pt}"), venue=venue))
                except LookupError:
                    break
        return out

    # ------------------------------------------------------------------ 跨市场轮动（C 因果模型）
    def data_provenance(self, day: date, symbols: list[str]) -> dict:
        """各资产当日数据来源与真实性（source、是否 SIMULATED、K 线数、逐笔成交数）。"""
        out = {}
        for s in symbols:
            b = self.hindsight_bars(s, day)
            if b.empty:
                out[s] = {"available": False}
                continue
            sim = bool((b["quality"].astype(int) & 128).any())
            try:
                ntx = int(self.store.query("SELECT COUNT(*) AS n FROM token_trades WHERE symbol=? AND CAST(ts_ny AS DATE)=?", [s, day])["n"].iloc[0])
            except Exception:  # noqa: BLE001
                ntx = 0
            out[s] = {"available": True, "source": str(b["source"].iloc[0]), "simulated": sim, "bars": int(len(b)), "trades": ntx}
        return out

    def require_real(self, day: date, symbols: list[str]) -> None:
        """严格模式：任一资产当日数据为 SIMULATED 或缺失 → 抛错，绝不用模拟数据冒充真实行情。"""
        prov = self.data_provenance(day, symbols)
        bad = [s for s, v in prov.items() if (not v.get("available")) or v.get("simulated")]
        if bad:
            raise RuntimeError(f"{day} 以下资产没有真实行情（缺失或 SIMULATED）：{', '.join(bad)}。网络放行 api.bitget.com / bopenapi.bgwapi.io 后执行 ingest。阻塞：{'; '.join(self.plan.blockers[:3])}")

    def asset_day(self, symbol: str, day: date, weight: float = 1.0) -> AssetDay | None:
        bars = self.hindsight_bars(symbol, day)
        if bars.empty:
            return None
        return make_asset_day(symbol, bars, self.profile_for(symbol), self.capability(symbol), day, weight)

    def rotation_specs(self, stocks: list[str] | None = None, cryptos: list[str] | None = None) -> list[StrategySpec]:
        rc = load_rotation_config()
        return build_specs(stocks or self.symbols(tradable_only=True), cryptos or self.crypto_symbols(tradable_only=True),
                           int(rc.get("max_assets_rotation", 2)))

    def rotation_day(self, spec: StrategySpec, day: date, capital_usd: float, target_usd: float, params: RotParams | None = None) -> RotDayRun:
        assets = []
        syms = list(spec.universe)
        w = 1.0 / len(syms) if (not spec.rotation and len(syms) > 1) else 1.0
        for s in syms:
            a = self.asset_day(s, day, w)
            if a is not None:
                assets.append(a)
        if not assets:
            raise LookupError(f"{spec.key} {day} 资产池无数据")
        if not spec.rotation and len(assets) > 1:        # 组合均分：缺数据的资产不占权重
            for a in assets:
                a.weight = 1.0 / len(assets)
        return run_rotation_day(assets, spec, capital_usd, target_usd, self.engine_cfg, params or RotParams(), str(day), load_rotation_config())

    # ------------------------------------------------------------------ 历史盈利路径模板（事后）
    def playbook_tier(self, day: date, capital_usd: float, target_usd: float, symbols: list[str] | None = None, tier: str | None = None, mode: str = "cross"):
        from .playbook.optimizer import optimize_playbook
        syms = symbols or (self.symbols(tradable_only=True) + self.crypto_symbols(tradable_only=True))
        if mode.startswith("single:"):
            syms = [mode.split(":", 1)[1]]
        assets = [a for a in (self.asset_day(s, day) for s in syms) if a is not None and a.cap.directions]
        if not assets:
            raise LookupError(f"{day} 无可交易资产数据")
        tx = {a.symbol: self.tx_amount_per_bar(a.symbol, day) for a in assets}
        return optimize_playbook(assets, capital_usd, target_usd, day.isoformat(), mode=mode, tier=tier, participation=self.participation_rate,
                                 tx_amount={k: v for k, v in tx.items() if v is not None}, weights=self.playbook_cfg.get("weights"),
                                 max_hold_min=int(self.playbook_cfg.get("max_hold_min", 480)), k_grid=tuple(self.playbook_cfg.get("k_grid", (1, 2, 4, 8, 16, 32, 64, 128, 256))))

    def playbook_tiers(self, day: date, symbols: list[str] | None = None, mode: str = "cross") -> list:
        out = []
        for t in self.tiers.tiers:
            cap = t.representative_capitals(("mid",))["mid"]
            lo, hi = t.target_daily_pct
            pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(self.simple_cfg.target_mode, lo)
            out.append(self.playbook_tier(day, cap, pct / 100 * cap, symbols, t.id, mode))
        return out

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
