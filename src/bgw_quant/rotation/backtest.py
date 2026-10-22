"""多日时间顺序样本外回测 + A/B 对照 + 六类策略排名（可断点续跑，结果缓存到独立 DuckDB）。

时间顺序规则（无未来信息）：
  * 测试日 d 的参数只由 d 之前 train_days 个交易日的因果运行结果选择（目标函数：达标率、亏损日比例、回撤、波动）；
  * “固定单只股票/加密”的事前选择版本（A*/B*）同样只用 d 之前的样本外结果挑选资产；
  * 同一 (策略, 档位, 日, 参数) 的运行结果与其服务的测试日无关，因此可缓存复用。
A/B 使用当日全部信息，只作为“历史上可以赚到的利润”的上界对照，与 C 分开列示。
"""
from __future__ import annotations

import itertools
import json
import multiprocessing as mp
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..calendar_utils import trading_days
from ..config import get_settings
from .engine import RotDayRun, RotParams, load_rotation_config, run_rotation_day
from .hindsight import universe_ab
from .strategies import StrategySpec
from .universe import AssetDay

DDL = """
CREATE TABLE IF NOT EXISTS rot_runs (
  cache_key VARCHAR PRIMARY KEY, strategy VARCHAR, tier VARCHAR, capital_usd DOUBLE, target_usd DOUBLE, day DATE,
  params_json VARCHAR, cost_profile VARCHAR, summary_json VARCHAR, trades_json VARCHAR, created_at TIMESTAMP DEFAULT now());
CREATE TABLE IF NOT EXISTS ab_runs (
  cache_key VARCHAR PRIMARY KEY, strategy VARCHAR, tier VARCHAR, capital_usd DOUBLE, target_usd DOUBLE, day DATE,
  cost_profile VARCHAR, ab_json VARCHAR, created_at TIMESTAMP DEFAULT now());
"""

# fork 子进程共享（只读）
_ASSETS: dict[tuple[str, str], AssetDay] = {}
_CFG = None
_ROT_CFG: dict = {}
_SPECS: dict[str, StrategySpec] = {}


def _summary_of(run: RotDayRun) -> dict:
    d = run.to_dict()
    d.pop("trades", None)
    d["switches"] = [s if isinstance(s, dict) else s.__dict__ for s in d.get("switches", [])]
    return d


def _c_worker(task: tuple) -> tuple[tuple, dict, list[dict]]:
    spec_key, tier, capital, target, day, params_d = task
    spec = _SPECS[spec_key]
    assets = [_ASSETS[(s, day)] for s in spec.universe if (s, day) in _ASSETS]
    if not assets:
        return task, {"error": "资产池无数据"}, []
    if not spec.rotation and len(assets) > 1:
        for a in assets:
            a.weight = 1.0 / len(assets)
    run = run_rotation_day(assets, spec, capital, target, _CFG, RotParams(**params_d), day, _ROT_CFG)
    return task, _summary_of(run), [t.to_dict() for t in run.trades]


def _ab_worker(task: tuple) -> tuple[tuple, dict]:
    spec_key, tier, capital, target, day, part = task
    spec = _SPECS[spec_key]
    assets = [_ASSETS[(s, day)] for s in spec.universe if (s, day) in _ASSETS]
    if not assets:
        return task, {"error": "资产池无数据"}
    ab = universe_ab(assets, spec, capital, target, day, part, b_cache=_AB_CACHE)
    d = ab.to_dict()
    d["theoretical_trades"] = d["theoretical_trades"][:200]
    return task, d


_AB_CACHE: dict = {}


def grid_params(rc: dict) -> list[RotParams]:
    g = rc.get("grid") or {}
    keys = [k for k in RotParams().__dict__ if k in g]
    out = []
    for combo in itertools.product(*[g[k] for k in keys]):
        out.append(RotParams(**dict(zip(keys, combo))))
    return out or [RotParams()]


def objective(rows: list[dict], capital_usd: float, w: dict) -> float:
    if not rows:
        return -1e9
    r = np.array([x["realized_net_return"] for x in rows])
    pnl = np.array([x["realized_net_usd"] for x in rows])
    cum = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(cum) - cum).max())
    att, lose, sd = float(np.mean([x["target_met"] for x in rows])), float(np.mean(r < 0)), float(r.std(ddof=0))
    return (w.get("w_attainment", 1.0) * att - w.get("w_losing_days", 0.7) * lose - w.get("w_drawdown", 20.0) * dd / capital_usd
            - w.get("w_volatility", 10.0) * sd + 1e-3 * float(r.mean()) * 100)   # 微小的均值项打破平局


@dataclass
class BacktestPlan:
    test_days: list[date]
    train_days: int
    tiers: list[tuple[str, float, float]]         # (tier_id, capital, target)
    specs: list[StrategySpec]
    grid: list[RotParams]
    cost_profile: str


class RotationBacktest:
    def __init__(self, pipeline, end_day: date | None = None, test_days: int | None = None, train_days: int | None = None,
                 points: tuple[str, ...] = ("mid",), specs: list[StrategySpec] | None = None, cache_path: str | None = None, workers: int = 4):
        self.pl = pipeline
        rc = load_rotation_config()
        self.rc = rc
        self.test_n = int(test_days or rc.get("test_days", 10))
        self.train_n = int(train_days or rc.get("train_days", 5))
        end_day = end_day or pipeline.latest_day() or date.today()
        all_days = trading_days(end_day - pd.Timedelta(days=90).to_pytimedelta(), end_day)
        self.days = all_days[-(self.test_n + self.train_n):]
        self.test_days = self.days[self.train_n:]
        self.specs = specs or pipeline.rotation_specs()
        self.grid = grid_params(rc)
        tiers = []
        for t in pipeline.tiers.tiers:
            for pt, cap_usd in t.representative_capitals(points).items():
                lo, hi = t.target_daily_pct
                pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(pipeline.simple_cfg.target_mode, lo)
                tiers.append((t.id + ("" if pt == "mid" else f"-{pt}"), float(cap_usd), float(pct / 100 * cap_usd)))
        self.tiers = tiers
        self.workers = workers
        s = get_settings()
        p = Path(cache_path) if cache_path else s.resolve_path("data_store/rotation_cache.duckdb")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(p))
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                self.con.execute(stmt)
        self.plan = BacktestPlan(self.test_days, self.train_n, tiers, self.specs, self.grid, pipeline.cost_profile_name)

    # ------------------------------------------------------------------ 数据
    def prepare(self) -> dict:
        global _ASSETS, _CFG, _ROT_CFG, _SPECS
        syms = sorted({s for sp in self.specs for s in sp.universe})
        missing = []
        for d in self.days:
            for s in syms:
                a = self.pl.asset_day(s, d)
                if a is None:
                    missing.append((s, str(d)))
                else:
                    _ASSETS[(s, str(d))] = a
        _CFG, _ROT_CFG, _SPECS = self.pl.engine_cfg, self.rc, {sp.key: sp for sp in self.specs}
        return {"days": [str(d) for d in self.days], "symbols": syms, "missing": missing, "n_assets_loaded": len(_ASSETS)}

    # ------------------------------------------------------------------ 缓存
    @staticmethod
    def _ckey(spec_key, tier, day, params: RotParams, profile) -> str:
        return f"{spec_key}|{tier}|{day}|{json.dumps(params.to_dict(), sort_keys=True)}|{profile}"

    def _cached_runs(self) -> dict[str, dict]:
        df = self.con.execute("SELECT cache_key, summary_json FROM rot_runs WHERE cost_profile = ?", [self.plan.cost_profile]).df()
        return {k: json.loads(v) for k, v in zip(df["cache_key"], df["summary_json"])}

    def run_c(self, progress=None) -> int:
        have = self._cached_runs()
        tasks = []
        for sp in self.specs:
            for tier, cap, tgt in self.tiers:
                for d in self.days:
                    for p in self.grid:
                        if self._ckey(sp.key, tier, str(d), p, self.plan.cost_profile) not in have:
                            tasks.append((sp.key, tier, cap, tgt, str(d), p.to_dict()))
        if not tasks:
            return 0
        done = 0
        ctx = mp.get_context("fork")
        with ctx.Pool(self.workers) as pool:
            for task, summ, trades in pool.imap_unordered(_c_worker, tasks, chunksize=4):
                spec_key, tier, cap, tgt, d, pd_ = task
                key = self._ckey(spec_key, tier, d, RotParams(**pd_), self.plan.cost_profile)
                self.con.execute("INSERT OR REPLACE INTO rot_runs VALUES (?,?,?,?,?,?,?,?,?,?, now())",
                                 [key, spec_key, tier, cap, tgt, d, json.dumps(pd_, sort_keys=True), self.plan.cost_profile,
                                  json.dumps(summ, ensure_ascii=False, default=str), json.dumps(trades, ensure_ascii=False, default=str)])
                done += 1
                if progress and done % 50 == 0:
                    progress(done, len(tasks))
        return done

    def run_ab(self, progress=None) -> int:
        have = set(self.con.execute("SELECT cache_key FROM ab_runs WHERE cost_profile = ?", [self.plan.cost_profile]).df()["cache_key"])
        part = float(self.pl.engine_cfg.execution.get("participation_rate", 0.1))
        tasks = []
        for d in self.test_days:
            for tier, cap, tgt in self.tiers:
                for sp in self.specs:
                    key = f"{sp.key}|{tier}|{d}|{self.plan.cost_profile}"
                    if key not in have:
                        tasks.append((sp.key, tier, cap, tgt, str(d), part))
        if not tasks:
            return 0
        # 同一 (日, 档位) 的任务放到同一个 worker 以复用单资产 B 缓存：按 (day, tier) 分组后顺序执行
        groups: dict[tuple, list] = {}
        for t in tasks:
            groups.setdefault((t[4], t[1]), []).append(t)
        done = 0
        ctx = mp.get_context("fork")
        with ctx.Pool(self.workers) as pool:
            for results in pool.imap_unordered(_ab_group_worker, list(groups.values())):
                for task, d in results:
                    spec_key, tier, cap, tgt, day, _ = task
                    key = f"{spec_key}|{tier}|{day}|{self.plan.cost_profile}"
                    self.con.execute("INSERT OR REPLACE INTO ab_runs VALUES (?,?,?,?,?,?,?,?, now())",
                                     [key, spec_key, tier, cap, tgt, day, self.plan.cost_profile, json.dumps(d, ensure_ascii=False, default=str)])
                    done += 1
                if progress:
                    progress(done, len(tasks))
        return done

    # ------------------------------------------------------------------ 样本外选择与汇总
    def select_params(self, runs: dict[str, dict], spec_key: str, tier: str, cap: float, test_day: date) -> tuple[RotParams, float]:
        idx = self.days.index(test_day)
        train = self.days[max(0, idx - self.train_n): idx]
        w = self.rc.get("objective") or {}
        best, best_s = self.grid[0], -np.inf
        for p in self.grid:
            rows = [runs[k] for d in train if (k := self._ckey(spec_key, tier, str(d), p, self.plan.cost_profile)) in runs and "error" not in runs[k]]
            s = objective(rows, cap, w)
            if s > best_s:
                best, best_s = p, s
        return best, best_s

    def oos_table(self) -> pd.DataFrame:
        runs = self._cached_runs()
        ab_df = self.con.execute("SELECT strategy, tier, day, ab_json FROM ab_runs WHERE cost_profile = ?", [self.plan.cost_profile]).df()
        ab = {(r.strategy, r.tier, pd.Timestamp(r.day).date().isoformat()): json.loads(r.ab_json) for r in ab_df.itertuples()}
        rows = []
        w = self.rc.get("objective") or {}
        for sp in self.specs:
            for tier, cap, tgt in self.tiers:
                for d in self.test_days:
                    p, train_score = self.select_params(runs, sp.key, tier, cap, d)
                    k = self._ckey(sp.key, tier, str(d), p, self.plan.cost_profile)
                    r = runs.get(k)
                    if not r or "error" in r:
                        continue
                    a = ab.get((sp.key, tier, str(d)), {})
                    pr = sorted((r.get("pause_reasons") or {}).items(), key=lambda kv: -kv[1])[:3]
                    rows.append({"strategy": sp.key, "family": sp.family, "name": sp.name, "tier": tier, "capital_usd": cap, "target_usd": tgt, "day": str(d),
                                 "params": json.dumps(p.to_dict(), sort_keys=True), "train_score": train_score,
                                 "A_theoretical_usd": a.get("theoretical_net_usd"), "B_executable_usd": a.get("executable_net_usd"),
                                 "A_met": a.get("a_met"), "B_met": a.get("b_met"), "B_alloc": json.dumps(a.get("executable_alloc") or {}, ensure_ascii=False),
                                 "C_net_usd": r["realized_net_usd"], "C_net_pct": r["realized_net_return"] * 100, "C_met": r["target_met"],
                                 "C_hit_ts": r.get("target_hit_ts"), "n_trades": r["n_trades"], "n_long": r["n_long"], "n_short": r["n_short"],
                                 "n_switches": r["n_switches"], "path": "→".join(r.get("path") or []), "win_rate": r.get("win_rate"),
                                 "max_dd_usd": r["max_drawdown_usd"], "max_consec_losses": r["max_consecutive_losses"],
                                 "peak_deployed_usd": r["peak_deployed_usd"], "avg_deployed_usd": r["avg_deployed_usd"], "flat_ratio": r["flat_ratio"],
                                 "exec_cost_usd": r["execution_cost_usd"], "funding_cost_usd": r["funding_cost_usd"],
                                 "post_target_trades": r["post_target_trades"], "post_target_net_usd": r["post_target_net_usd"], "post_target_stop": r["post_target_stop"],
                                 "stop_reason": r.get("stop_reason"), "top_pauses": "；".join(f"{k}×{v}" for k, v in pr),
                                 "unverifiable": r["unverifiable"], "simulated": r["data_is_simulated"],
                                 "reason": self._reason(a, r, tgt)})
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        # 事前选择版本：A*/B* —— 用 d 之前（训练窗）各固定资产的 OOS 表现挑选资产（因果）
        extra = []
        for fam, label in (("A", "A*:事前选股"), ("B", "B*:事前选币")):
            sub = df[df["family"] == fam]
            if sub.empty:
                continue
            for tier in sub["tier"].unique():
                st = sub[sub["tier"] == tier]
                for i, d in enumerate(self.test_days):
                    prev = [str(x) for x in self.test_days[max(0, i - self.train_n): i]]
                    if not prev:
                        continue
                    hist = st[st["day"].isin(prev)]
                    if hist.empty:
                        continue
                    sc = hist.groupby("strategy").apply(lambda g: objective([{"realized_net_return": x / 100, "realized_net_usd": y, "target_met": z}
                                                                            for x, y, z in zip(g["C_net_pct"], g["C_net_usd"], g["C_met"])], float(g["capital_usd"].iloc[0]), w), include_groups=False)
                    pick = sc.idxmax()
                    row = st[(st["strategy"] == pick) & (st["day"] == str(d))]
                    if row.empty:
                        continue
                    rr = row.iloc[0].to_dict()
                    rr.update({"strategy": label, "family": fam + "*", "name": f"{label}（当日选中 {pick.split(':')[1]}）", "path": f"[事前选中 {pick.split(':')[1]}] " + str(rr["path"])})
                    extra.append(rr)
        if extra:
            df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
        return df

    @staticmethod
    def _reason(a: dict, r: dict, target: float) -> str:
        if r["target_met"]:
            return "达标"
        A, B = a.get("theoretical_net_usd"), a.get("executable_net_usd")
        if A is not None and A < target:
            return "目标高于当日理论上限（行情本身不足）"
        if B is not None and B < target:
            return "流动性/成本约束下不可达（B 未达标）"
        if r.get("stop_reason"):
            return f"因果策略不足：{r['stop_reason']}"
        pr = sorted((r.get("pause_reasons") or {}).items(), key=lambda kv: -kv[1])[:2]
        return "因果策略不足（无预测优势）：" + "；".join(f"{k}×{v}" for k, v in pr)

    def ranking(self, oos: pd.DataFrame) -> pd.DataFrame:
        if oos.empty:
            return oos
        g = oos.groupby(["strategy", "family", "name", "tier", "capital_usd", "target_usd"])
        rk = g.agg(days=("day", "count"), attainment=("C_met", "mean"), mean_net_usd=("C_net_usd", "mean"), mean_net_pct=("C_net_pct", "mean"),
                   std_net_pct=("C_net_pct", "std"), losing_day_rate=("C_net_usd", lambda s: float((s < 0).mean())), worst_day_pct=("C_net_pct", "min"),
                   mean_trades=("n_trades", "mean"), mean_switches=("n_switches", "mean"), mean_dd_usd=("max_dd_usd", "mean"),
                   max_consec_losses=("max_consec_losses", "max"), mean_exec_cost=("exec_cost_usd", "mean"), flat_ratio=("flat_ratio", "mean"),
                   A_attainment=("A_met", "mean"), B_attainment=("B_met", "mean"), A_mean_usd=("A_theoretical_usd", "mean"), B_mean_usd=("B_executable_usd", "mean"),
                   unverifiable_days=("unverifiable", "sum")).reset_index()
        # 多日累计回撤（按日累计净利润）
        dd = {}
        for key, grp in g:
            cum = grp.sort_values("day")["C_net_usd"].cumsum().to_numpy()
            dd[key] = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
        rk["multi_day_dd_usd"] = [dd[tuple(r[c] for c in ["strategy", "family", "name", "tier", "capital_usd", "target_usd"])] for _, r in rk.iterrows()]
        rk["std_net_pct"] = rk["std_net_pct"].fillna(0.0)
        rk["stability"] = (rk["mean_net_pct"] / rk["std_net_pct"].replace(0, np.nan)).fillna(0.0)
        w = self.rc.get("objective") or {}
        rk["score"] = (w.get("w_attainment", 1.0) * rk["attainment"] - w.get("w_losing_days", 0.7) * rk["losing_day_rate"]
                       - w.get("w_drawdown", 20.0) * rk["multi_day_dd_usd"] / rk["capital_usd"] - w.get("w_volatility", 10.0) * rk["std_net_pct"] / 100)
        rk["rank_in_tier"] = rk.groupby("tier")["score"].rank(ascending=False, method="first").astype(int)
        return rk.sort_values(["tier", "rank_in_tier"]).reset_index(drop=True)

    def trades_frame(self, oos: pd.DataFrame) -> pd.DataFrame:
        if oos.empty:
            return pd.DataFrame()
        keys = [(r.strategy, r.tier, r.day, r.params) for r in oos.itertuples() if not str(r.strategy).startswith(("A*", "B*"))]
        out = []
        for spec_key, tier, d, params in keys:
            k = self._ckey(spec_key, tier, d, RotParams(**json.loads(params)), self.plan.cost_profile)
            df = self.con.execute("SELECT trades_json FROM rot_runs WHERE cache_key = ?", [k]).df()
            if df.empty:
                continue
            for t in json.loads(df["trades_json"].iloc[0]):
                t.pop("open_children", None); t.pop("close_children", None)
                out.append({"strategy": spec_key, "tier": tier, "day": d, **t})
        return pd.DataFrame(out)

    def run_detail(self, spec_key: str, tier: str, day: str, params: dict) -> tuple[dict, list[dict]]:
        k = self._ckey(spec_key, tier, day, RotParams(**params), self.plan.cost_profile)
        df = self.con.execute("SELECT summary_json, trades_json FROM rot_runs WHERE cache_key = ?", [k]).df()
        if df.empty:
            raise LookupError(k)
        return json.loads(df["summary_json"].iloc[0]), json.loads(df["trades_json"].iloc[0])


def _ab_group_worker(tasks: list[tuple]) -> list[tuple[tuple, dict]]:
    global _AB_CACHE
    _AB_CACHE = {}
    return [_ab_worker(t) for t in tasks]
