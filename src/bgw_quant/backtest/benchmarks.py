"""基准：买入持有、无交易、简单规则（默认参数 EMA 趋势）。"""
from __future__ import annotations

import pandas as pd

from ..costs.model import CostModel
from ..strategies.rules import make_strategy
from .engine import daily_frame, run_daily


def benchmark_daily(bars: pd.DataFrame, cost: CostModel, capital_usd: float) -> pd.DataFrame:
    runs = run_daily(bars, make_strategy("ema_trend"), cost, capital_usd, with_hindsight=True)
    df = daily_frame(runs)
    out = pd.DataFrame(index=df.index)
    out["buy_hold"] = df["buy_hold_net"]
    out["no_trade"] = 0.0
    out["simple_rule_ema"] = df["net_return"]
    out["hindsight_optimal"] = df["hindsight_net"]
    return out
