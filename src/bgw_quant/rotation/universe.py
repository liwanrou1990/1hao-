"""资产池与时间对齐：把股票（09:30–16:00 NY）与加密货币（24 小时）K 线放到同一条分钟网格上。

网格 = America/New_York 自然日 [00:00, 24:00) 的每一分钟（1440 格）。股票只在常规时段有 K 线，其余格为缺失；
引擎只能在资产“当前有 K 线”的格上对该资产决策/成交，不会在股票休市时段交易股票。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..config import asset_class_of
from ..costs.model import CostModel, CostProfile
from ..decision.capability import ProductCapability

GRID_MINUTES = 1440


def day_grid_utc(day: date) -> pd.DatetimeIndex:
    from ..data.crypto import crypto_day_bounds_utc

    start, end = crypto_day_bounds_utc(day)
    return pd.date_range(start, end - pd.Timedelta(minutes=1), freq="1min", tz="UTC")


@dataclass
class AssetDay:
    symbol: str
    asset_class: str
    bars: pd.DataFrame                  # 该资产当日 K 线（reset_index）
    cost: CostModel
    cap: ProductCapability
    grid_of_bar: np.ndarray             # bar idx → grid 位置
    bar_at_grid: np.ndarray             # grid 位置 → bar idx（-1 = 无 K 线）
    bars_per_day: int                   # 390（股票）/ 1440（加密）用于波动率年化
    source: str = ""
    simulated: bool = False
    weight: float = 1.0                 # 组合策略的固定权重（轮动策略为 1.0）

    @property
    def n(self) -> int:
        return len(self.bars)

    @property
    def first_grid(self) -> int:
        return int(self.grid_of_bar[0]) if self.n else -1

    @property
    def last_grid(self) -> int:
        return int(self.grid_of_bar[-1]) if self.n else -1


def make_asset_day(symbol: str, bars: pd.DataFrame, profile: CostProfile, cap: ProductCapability, day: date, weight: float = 1.0) -> AssetDay:
    bars = bars.reset_index(drop=True)
    grid = day_grid_utc(day)
    ts = pd.to_datetime(bars["ts"], utc=True)
    pos = ((ts - grid[0]) // pd.Timedelta(minutes=1)).to_numpy().astype(int)
    keep = (pos >= 0) & (pos < GRID_MINUTES)
    bars, pos = bars[keep].reset_index(drop=True), pos[keep]
    bar_at = np.full(GRID_MINUTES, -1, dtype=int)
    bar_at[pos] = np.arange(len(pos))
    ac = asset_class_of(symbol)
    src = str(bars["source"].iloc[0]) if len(bars) else ""
    sim = bool((bars["quality"].astype(int) & 128).any()) if len(bars) else False
    return AssetDay(symbol, ac, bars, CostModel(profile), cap, pos, bar_at, 1440 if ac == "crypto" else 390, src, sim, weight)
