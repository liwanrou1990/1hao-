"""规则策略的 walk-forward 样本外验证：参数在训练窗口内选（样本内），只在随后的测试窗口评估（样本外）。

严禁把样本内调参结果当成样本外绩效：本模块返回的 oos_daily 只包含测试窗口。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..costs.model import CostModel
from ..strategies.rules import RULE_STRATEGIES
from .engine import daily_frame, run_daily

PARAM_GRIDS: dict[str, dict[str, list]] = {
    "vwap": {"band_bps": [0.0, 5.0, 10.0]},
    "ema_trend": {"fast": [5, 9], "slow": [21, 34]},
    "macd": {"fast": [8, 12], "slow": [21, 26]},
    "rsi": {"lower": [25, 30], "upper": [50, 55]},
    "bollinger": {"n": [20, 30], "k": [2.0, 2.5]},
    "atr_channel": {"k": [1.0, 1.5, 2.0]},
    "breakout": {"n": [20, 30, 60]},
    "mean_reversion": {"z_entry": [-1.5, -2.0, -2.5]},
    "volume_filter": {"rel_vol": [1.2, 1.5]},
}


@dataclass
class WFFold:
    fold: int
    train: tuple[date, date]
    test: tuple[date, date]
    chosen_params: dict
    in_sample_mean: float
    oos_daily: pd.DataFrame


@dataclass
class WFResult:
    strategy_key: str
    folds: list[WFFold]
    oos_daily: pd.DataFrame
    is_daily_mean_by_fold: list[float] = field(default_factory=list)
    note: str = "oos_daily 仅含测试窗口；chosen_params 为训练窗口内选择，不得用训练窗口绩效代表样本外"

    @property
    def in_sample_vs_oos(self) -> dict:
        return {"in_sample_mean_daily": float(pd.Series(self.is_daily_mean_by_fold).mean()) if self.is_daily_mean_by_fold else None,
                "oos_mean_daily": float(self.oos_daily["net_return"].mean()) if not self.oos_daily.empty else None}


def _grid(key: str):
    g = PARAM_GRIDS.get(key, {})
    keys = list(g)
    for combo in itertools.product(*[g[k] for k in keys]) if keys else [()]:
        yield dict(zip(keys, combo))


def walk_forward_rule(bars: pd.DataFrame, strategy_key: str, cost: CostModel, capital_usd: float,
                      train_days: int = 20, test_days: int = 5, embargo_days: int = 1, min_train_days: int = 10) -> WFResult:
    if train_days < min_train_days:
        raise ValueError("训练窗口过短")
    cls = RULE_STRATEGIES[strategy_key]
    day = pd.to_datetime(bars["ts_ny"]).dt.date
    days = sorted(day.unique())
    folds: list[WFFold] = []
    is_means = []
    i = 0
    k = 0
    while i + train_days + embargo_days + test_days <= len(days):
        tr, te = days[i:i + train_days], days[i + train_days + embargo_days:i + train_days + embargo_days + test_days]
        tr_bars, te_bars = bars[day.isin(tr)], bars[day.isin(te)]
        best_p, best_m = None, -1e9
        for p in _grid(strategy_key):
            m = daily_frame(run_daily(tr_bars, cls, cost, capital_usd, with_hindsight=False, params=p))["net_return"].mean()
            if m > best_m:
                best_p, best_m = p, m
        oos = daily_frame(run_daily(te_bars, cls, cost, capital_usd, with_hindsight=True, params=best_p))
        oos["fold"] = k + 1
        folds.append(WFFold(k + 1, (tr[0], tr[-1]), (te[0], te[-1]), best_p, float(best_m), oos))
        is_means.append(float(best_m))
        i += test_days
        k += 1
    oos_all = pd.concat([f.oos_daily for f in folds]) if folds else pd.DataFrame(columns=["net_return"])
    return WFResult(strategy_key, folds, oos_all, is_means)
