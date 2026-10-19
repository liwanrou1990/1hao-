"""固定收益目标策略的滚动样本外验证：参数在训练窗按“稳定性目标”选择，绩效只在随后的测试窗统计。"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from .config import TargetModelConfig
from .strategy import TargetSeekingStrategy, run_strategy_day


@dataclass
class StabilityStats:
    n_days: int
    attainment_rate: float
    losing_day_rate: float
    mean_daily_return: float
    std_daily_return: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    worst_day_return: float
    avg_trades_per_day: float
    partial_fill_days: int
    unverifiable_days: int
    score: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def stability_stats(days: list, capital_usd: float, weights: dict) -> StabilityStats:
    if not days:
        return StabilityStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -1e9)
    r = np.array([d.realized_net_return for d in days])
    pnl = np.array([d.realized_net_usd for d in days])
    cum = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
    att = float(np.mean([d.target_met for d in days]))
    lose = float(np.mean(r < 0))
    sd = float(r.std(ddof=0))
    score = (weights.get("w_attainment", 1.0) * att - weights.get("w_losing_days", 0.7) * lose
             - weights.get("w_drawdown", 20.0) * dd / capital_usd - weights.get("w_volatility", 10.0) * sd)
    return StabilityStats(len(days), att, lose, float(r.mean()), sd, dd, dd / capital_usd, float(r.min()),
                          float(np.mean([len(d.trades) for d in days])), int(sum(d.partial_fills > 0 for d in days)),
                          int(sum(d.unverifiable for d in days)), float(score))


@dataclass
class TargetWFFold:
    fold: int
    train: tuple[date, date]
    test: tuple[date, date]
    params: dict
    train_stats: StabilityStats
    test_days: list = field(default_factory=list)


@dataclass
class TargetWFResult:
    symbol: str
    capital_usd: float
    target_return: float
    folds: list[TargetWFFold]
    oos_days: list
    oos_stats: StabilityStats
    in_sample_stats_mean: dict
    note: str = "参数在训练窗按稳定性目标（达标率、亏损日比例、回撤、波动）选择；oos_* 只含测试窗；严禁用训练窗绩效代表样本外"

    def oos_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{"day": d.day, "net_usd": d.realized_net_usd, "net_return": d.realized_net_return, "target_met": d.target_met,
                              "n_trades": len(d.trades), "stopped": d.stopped_reason, "max_dd_usd": d.max_drawdown_usd,
                              "partial_fills": d.partial_fills, "unverifiable": d.unverifiable} for d in self.oos_days])


def _grid(cfg: TargetModelConfig):
    g = cfg.strategy_grid or {"z_entry": [1.5], "z_window": [30], "take_profit_net_pct": [0.5], "stop_loss_pct": [0.8], "position_fraction": [0.5]}
    keys = list(g)
    for combo in itertools.product(*[g[k] for k in keys]):
        yield TargetSeekingStrategy(**dict(zip(keys, combo)))


def _run_days(bars_by_day: dict, cost, capital_usd, target_return, cfg, strat):
    return [run_strategy_day(b, cost, capital_usd, target_return, cfg, strat) for b in bars_by_day.values()]


def walk_forward_target(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_return: float, cfg: TargetModelConfig,
                        train_days: int = 20, test_days: int = 5, embargo_days: int = 1, min_train_days: int = 10) -> TargetWFResult:
    if train_days < min_train_days:
        raise ValueError("训练窗过短")
    sym = str(bars["symbol"].iloc[0])
    day = pd.to_datetime(bars["ts_ny"]).dt.date
    days = sorted(day.unique())
    by_day = {d: g.reset_index(drop=True) for d, g in bars.groupby(day)}
    folds: list[TargetWFFold] = []
    oos: list = []
    i, k = 0, 0
    w = cfg.stability_objective
    while i + train_days + embargo_days + test_days <= len(days):
        tr = days[i:i + train_days]
        te = days[i + train_days + embargo_days:i + train_days + embargo_days + test_days]
        best, best_stats = None, None
        for strat in _grid(cfg):
            st = stability_stats(_run_days({d: by_day[d] for d in tr}, cost, capital_usd, target_return, cfg, strat), capital_usd, w)
            if best_stats is None or st.score > best_stats.score:
                best, best_stats = strat, st
        test_res = _run_days({d: by_day[d] for d in te}, cost, capital_usd, target_return, cfg, best)
        folds.append(TargetWFFold(k + 1, (tr[0], tr[-1]), (te[0], te[-1]), best.params, best_stats, test_res))
        oos += test_res
        i += test_days
        k += 1
    is_mean = {}
    if folds:
        keys = ["attainment_rate", "losing_day_rate", "mean_daily_return", "std_daily_return", "max_drawdown_pct"]
        is_mean = {k2: float(np.mean([getattr(f.train_stats, k2) for f in folds])) for k2 in keys}
    return TargetWFResult(sym, capital_usd, target_return, folds, oos, stability_stats(oos, capital_usd, w), is_mean)
