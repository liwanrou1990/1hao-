"""已确认摆动高/低点（分形法）。

摆动高 i：high[i] 严格大于左侧 k 根最高，且 ≥ 右侧 k 根最高；摆动低对称。
确认时刻 = 第 i+k 根 K 线收盘。任何时点 t 只能使用 confirmed_idx ≤ 当时最后一根已收盘 K 线索引 的摆动点——这是杜绝未来函数的核心约束。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Swing:
    idx: int            # 摆动点所在 K 线索引
    kind: str           # "H" / "L"
    price: float
    confirmed_idx: int  # 在该索引的 K 线收盘后才可用
    ts: pd.Timestamp


def _roll_max_left(a: np.ndarray, k: int) -> np.ndarray:
    """左侧 k 根（不含自身）最大值；前 k 根为 +inf（不可能成为摆动点）。"""
    out = np.full(len(a), np.inf)
    s = pd.Series(a)
    m = s.shift(1).rolling(k, min_periods=k).max().to_numpy()
    out[k:] = m[k:]
    return out


def _roll_max_right(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(a), np.inf)
    s = pd.Series(a[::-1])
    m = s.shift(1).rolling(k, min_periods=k).max().to_numpy()[::-1]
    out[: len(a) - k] = m[: len(a) - k]
    return out


def find_swings(df: pd.DataFrame, k: int) -> list[Swing]:
    if df is None or len(df) < 2 * k + 1:
        return []
    high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
    ts = df["ts"].to_numpy()
    is_h = (high > _roll_max_left(high, k)) & (high >= _roll_max_right(high, k))
    is_l = (-low > _roll_max_left(-low, k)) & (-low >= _roll_max_right(-low, k))
    out: list[Swing] = []
    for i in np.flatnonzero(is_h):
        out.append(Swing(int(i), "H", float(high[i]), int(i + k), pd.Timestamp(ts[i])))
    for i in np.flatnonzero(is_l):
        out.append(Swing(int(i), "L", float(low[i]), int(i + k), pd.Timestamp(ts[i])))
    out.sort(key=lambda s: (s.idx, s.kind))
    return out


def confirmed_up_to(swings: list[Swing], last_closed_idx: int) -> list[Swing]:
    """只返回在 last_closed_idx 收盘时已确认的摆动点。"""
    return [s for s in swings if s.confirmed_idx <= last_closed_idx]
