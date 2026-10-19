"""成本感知过滤器：把任意规则策略包装成“只在预期波动足以覆盖往返成本时才进场”，并加冷却期防止反复进出。

因果性：只使用已完成K线的 ATR 与当前成本模型参数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from . import indicators as ind


class CostAwareStrategy:
    def __init__(self, inner, cost: CostModel, capital_usd: float, expected_hold_bars: int = 30,
                 edge_mult: float = 1.0, cooldown_bars: int = 10, atr_n: int = 14):
        self.inner = inner
        self.cost = cost
        self.capital_usd = capital_usd
        self.expected_hold_bars = expected_hold_bars
        self.edge_mult = edge_mult
        self.cooldown_bars = cooldown_bars
        self.atr_n = atr_n
        self.name = f"{inner.name}+成本门"
        self.params = {**getattr(inner, "params", {}), "expected_hold_bars": expected_hold_bars, "edge_mult": edge_mult,
                       "cooldown_bars": cooldown_bars}
        self.break_even = cost.break_even_move(capital_usd)

    def compute(self, df: pd.DataFrame) -> pd.Series:
        raw = self.inner.compute(df).fillna(0).astype(int).to_numpy()
        atr_rel = (ind.atr(df, self.atr_n) / df["close"]).to_numpy()
        # 预期可捕获波动 ≈ ATR × sqrt(持有K线数) × edge_mult，必须超过盈亏平衡
        expected_move = atr_rel * np.sqrt(self.expected_hold_bars) * self.edge_mult
        gate = expected_move > self.break_even
        out = np.zeros(len(raw), dtype=int)
        state, last_exit = 0, -10**9
        for i in range(len(raw)):
            if state == 0:
                if raw[i] == 1 and gate[i] and (i - last_exit) > self.cooldown_bars:
                    state = 1
            else:
                if raw[i] == 0:
                    state = 0
                    last_exit = i
            out[i] = state
        return pd.Series(out, index=df.index)

    def explain(self, df, i):
        return f"{self.inner.name}；成本门：盈亏平衡 {self.break_even:.2%}，冷却 {self.cooldown_bars} 根"
