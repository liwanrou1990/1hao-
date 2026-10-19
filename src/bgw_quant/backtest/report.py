"""把多标的、多策略、多档位的回测汇总成结构化结果，供 API/日报使用。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..capital.capacity import target_attainment
from ..capital.tiers import TierConfig
from ..costs.model import CostModel
from ..strategies.rules import RULE_STRATEGIES
from .benchmarks import benchmark_daily
from .metrics import performance_metrics
from .walkforward import walk_forward_rule


@dataclass
class StrategyEvaluation:
    symbol: str
    strategy_key: str
    capital_usd: float
    cost_profile: str
    oos_metrics: dict
    in_sample_vs_oos: dict
    oos_daily: pd.DataFrame
    folds: list[dict] = field(default_factory=list)


def evaluate_symbol(bars: pd.DataFrame, cost: CostModel, capital_usd: float, cost_profile: str,
                    strategy_keys: list[str] | None = None, train_days: int = 20, test_days: int = 5) -> dict:
    sym = str(bars["symbol"].iloc[0])
    evals: list[StrategyEvaluation] = []
    for key in strategy_keys or list(RULE_STRATEGIES):
        wf = walk_forward_rule(bars, key, cost, capital_usd, train_days=train_days, test_days=test_days)
        if wf.oos_daily.empty:
            continue
        trades: list[dict] = []
        m = performance_metrics(wf.oos_daily["net_return"], trades=None, capital_usd=capital_usd,
                                total_cost_usd=float(wf.oos_daily["cost_usd"].sum()),
                                hindsight_daily=wf.oos_daily["hindsight_net"])
        m["n_trades"] = int(wf.oos_daily["n_trades"].sum())
        m["trades_per_day"] = float(wf.oos_daily["n_trades"].mean())
        m["unverifiable_days"] = int(wf.oos_daily["unverifiable"].sum())
        evals.append(StrategyEvaluation(sym, key, capital_usd, cost_profile, m, wf.in_sample_vs_oos, wf.oos_daily,
                                        [{"fold": f.fold, "train": [str(f.train[0]), str(f.train[1])], "test": [str(f.test[0]), str(f.test[1])],
                                          "params": f.chosen_params, "in_sample_mean_daily": f.in_sample_mean,
                                          "oos_mean_daily": float(f.oos_daily["net_return"].mean())} for f in wf.folds]))
    bench = benchmark_daily(bars, cost, capital_usd)
    bench_metrics = {c: performance_metrics(bench[c]) for c in bench.columns}
    return {"symbol": sym, "capital_usd": capital_usd, "cost_profile": cost_profile,
            "strategies": {e.strategy_key: {"oos_metrics": e.oos_metrics, "in_sample_vs_oos": e.in_sample_vs_oos, "folds": e.folds}
                           for e in evals},
            "benchmarks": bench_metrics,
            "best_oos_strategy": max(evals, key=lambda e: e.oos_metrics.get("mean_daily_net_return", -1)).strategy_key if evals else None,
            "_evals": evals, "_bench": bench}


def tier_attainment_table(oos_daily: pd.Series, cfg: TierConfig) -> pd.DataFrame:
    rows = [target_attainment(oos_daily, t) for t in cfg.tiers]
    return pd.DataFrame(rows)
