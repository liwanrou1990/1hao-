"""按档位滚动优化与样本外验证：参数只在训练窗按稳定性目标选择，绩效只报测试窗；昨日复盘使用“截至前一折”选出的参数。"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from .capability import ProductCapability
from .engine import DayRun, EngineConfig, EngineParams, run_engine_day


@dataclass
class OOSStats:
    n_days: int
    attainment_rate: float
    losing_day_rate: float
    mean_daily_return: float
    std_daily_return: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    worst_day_return: float
    win_rate: float | None
    avg_trades_per_day: float
    avg_long: float
    avg_short: float
    avg_utilization: float
    exec_cost_usd: float
    funding_cost_usd: float
    partial_fill_days: int
    unverifiable_days: int
    score: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def stats_of(days: list[DayRun], capital_usd: float, w: dict) -> OOSStats:
    if not days:
        return OOSStats(0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, 0, 0, 0, 0, 0, 0, -1e9)
    r = np.array([d.realized_net_return for d in days])
    pnl = np.array([d.realized_net_usd for d in days])
    cum = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(cum) - cum).max())
    att, lose, sd = float(np.mean([d.target_met for d in days])), float(np.mean(r < 0)), float(r.std(ddof=0))
    trades = [t for d in days for t in d.trades]
    wr = (sum(t.net_pnl_usd > 0 for t in trades) / len(trades)) if trades else None
    score = (w.get("w_attainment", 1.0) * att - w.get("w_losing_days", 0.7) * lose - w.get("w_drawdown", 20.0) * dd / capital_usd
             - w.get("w_volatility", 10.0) * sd)
    return OOSStats(len(days), att, lose, float(r.mean()), sd, dd, dd / capital_usd, float(r.min()), wr,
                    float(np.mean([d.n_trades for d in days])), float(np.mean([d.n_long for d in days])), float(np.mean([d.n_short for d in days])),
                    float(np.mean([d.avg_deployed_usd / capital_usd for d in days])), float(sum(d.execution_cost_usd for d in days)),
                    float(sum(d.funding_cost_usd for d in days)), int(sum(d.partial_fills > 0 for d in days)), int(sum(d.unverifiable for d in days)), float(score))


@dataclass
class Fold:
    fold: int
    train: tuple[date, date]
    test: tuple[date, date]
    params: EngineParams
    train_stats: OOSStats
    test_days: list[DayRun] = field(default_factory=list)


@dataclass
class TierOptimization:
    symbol: str
    capital_usd: float
    target_usd: float
    folds: list[Fold]
    oos_days: list[DayRun]
    oos_stats: OOSStats
    in_sample_mean: dict
    final_params: EngineParams          # 最近一折在训练窗选出的参数（用于“昨日复盘”，不含昨日信息）
    note: str = "参数仅在训练窗按稳定性目标选择；oos_* 只含测试窗；昨日复盘使用最近一折参数，不含昨日信息"

    def oos_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{"day": d.day, "net_usd": d.realized_net_usd, "net_return": d.realized_net_return, "target_met": d.target_met,
                              "n_trades": d.n_trades, "n_long": d.n_long, "n_short": d.n_short, "win_rate": d.win_rate, "max_dd_usd": d.max_drawdown_usd,
                              "util": d.avg_deployed_usd / d.capital_usd if d.capital_usd else 0, "stop": d.stop_reason, "unverifiable": d.unverifiable}
                             for d in self.oos_days])


def grid_params(cfg: EngineConfig):
    g = cfg.grid or {}
    keys = [k for k in ("z_entry", "tp_atr_mult", "max_open", "trend_thresh", "edge_min_ratio") if k in g]
    for combo in itertools.product(*[g[k] for k in keys]):
        yield EngineParams(**dict(zip(keys, combo)))


def optimize_tier(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: EngineConfig, cap: ProductCapability,
                  train_days: int = 20, test_days: int = 5, embargo_days: int = 1, min_train_days: int = 10) -> TierOptimization:
    if train_days < min_train_days:
        raise ValueError("训练窗过短")
    sym = str(bars["symbol"].iloc[0])
    day = pd.to_datetime(bars["ts_ny"]).dt.date
    days = sorted(day.unique())
    by_day = {d: g.reset_index(drop=True) for d, g in bars.groupby(day)}
    w = cfg.objective
    folds: list[Fold] = []
    oos: list[DayRun] = []
    i, k = 0, 0
    best_params = EngineParams()
    while i + train_days + embargo_days + test_days <= len(days):
        tr = days[i:i + train_days]
        te = days[i + train_days + embargo_days:i + train_days + embargo_days + test_days]
        best, best_st = None, None
        for p in grid_params(cfg):
            st = stats_of([run_engine_day(by_day[d], cost, capital_usd, target_usd, cfg, p, cap, sym) for d in tr], capital_usd, w)
            if best_st is None or st.score > best_st.score:
                best, best_st = p, st
        test_runs = [run_engine_day(by_day[d], cost, capital_usd, target_usd, cfg, best, cap, sym) for d in te]
        folds.append(Fold(k + 1, (tr[0], tr[-1]), (te[0], te[-1]), best, best_st, test_runs))
        oos += test_runs
        best_params = best
        i += test_days
        k += 1
    is_mean = {}
    if folds:
        for key in ("attainment_rate", "losing_day_rate", "mean_daily_return", "max_drawdown_pct"):
            is_mean[key] = float(np.mean([getattr(f.train_stats, key) for f in folds]))
    return TierOptimization(sym, capital_usd, target_usd, folds, oos, stats_of(oos, capital_usd, w), is_mean, best_params)
