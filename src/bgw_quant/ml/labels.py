"""标签：未来 h 分钟扣费后收益。

进场：第 i+1 根开盘 × (1+c)；离场：第 i+h 根收盘 × (1−c)（h 根后仍在同一交易日，否则标签缺失）。
c = 单边比例成本 + 冲击；另扣 Gas（按名义本金折算）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..costs.model import CostModel

HORIZONS = (5, 15, 30, 60)


def forward_net_return(df: pd.DataFrame, h: int, cost: CostModel, capital_usd: float) -> pd.Series:
    df = df.reset_index(drop=True)
    c = cost.proportional_side_cost() + cost.impact_fraction(capital_usd)
    gas_frac = 2 * cost.p.gas_usd * cost.n_child_orders(capital_usd) / capital_usd
    entry = df["open"].shift(-1) * (1 + c)
    exit_ = df["close"].shift(-h) * (1 - c)
    ret = exit_ / entry - 1 - gas_frac
    day = pd.to_datetime(df["ts_ny"]).dt.date
    same_day = day.shift(-h) == day
    ret = ret.where(same_day)
    return ret


def build_labels(df: pd.DataFrame, cost: CostModel, capital_usd: float, horizons=HORIZONS) -> pd.DataFrame:
    out = pd.DataFrame(index=df.reset_index(drop=True).index)
    for h in horizons:
        r = forward_net_return(df, h, cost, capital_usd)
        out[f"ret_{h}"] = r
        out[f"up_{h}"] = (r > 0).astype(float).where(r.notna())
    return out
