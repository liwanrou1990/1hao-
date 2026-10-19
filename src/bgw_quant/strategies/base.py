"""策略协议：输入“截至当前已完成K线”，输出每根K线收盘时的目标仓位（0 空仓 / 1 满仓）。

反未来函数约定：compute(df) 必须是因果的 —— 对任意前缀 df[:k]，compute(df[:k]) == compute(df)[:k]。
tests/test_phase2_replay.py 用随机截断对每个策略做前缀一致性检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


class Strategy(Protocol):
    name: str
    params: dict

    def compute(self, df: pd.DataFrame) -> pd.Series:
        """返回与 df 等长的目标仓位序列（int 0/1），索引与 df 一致。"""
        ...

    def explain(self, df: pd.DataFrame, i: int) -> str:
        ...


@dataclass
class StrategySpec:
    name: str
    params: dict = field(default_factory=dict)
    description: str = ""
