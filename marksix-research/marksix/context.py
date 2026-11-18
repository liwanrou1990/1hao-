"""研究上下文：把开奖序列整理成数组，供形态、模型与回测共用。

约定：下标 t 表示第 t 期（按开奖时间排序，0 起）。在 t 期结束后触发规则，预测对象为 t+1 期特别号码。
映射数组（zm/zc/zok）按开奖日期决定，与开奖结果无关；预测 t+1 期时可使用 t+1 期的日期及其映射
（开奖日程事先公布），但绝不使用 t+1 期的任何开奖结果。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .zodiac import ZodiacMap


@dataclass
class Ctx:
    s: np.ndarray        # (L,) 特别号码 1..49
    draw_ids: np.ndarray
    dates: np.ndarray
    zm: np.ndarray       # (L,49) 每期映射：号码→生肖索引，缺失为 -1
    zc: np.ndarray       # (L,12) 每期每个生肖对应号码数量
    zok: np.ndarray      # (L,) 是否有映射
    map_version: str | None

    @property
    def L(self) -> int:
        return len(self.s)

    @property
    def z(self) -> np.ndarray:
        """特别号码对应的生肖（缺映射为 -1）。"""
        z = self.zm[np.arange(self.L), self.s - 1].copy()
        z[~self.zok] = -1
        return z

    def with_specials(self, s_new: np.ndarray) -> "Ctx":
        return Ctx(np.asarray(s_new, dtype=int), self.draw_ids, self.dates, self.zm, self.zc, self.zok,
                   self.map_version)


def build_ctx(df: pd.DataFrame, zmap: ZodiacMap | None) -> Ctx:
    s = df["special"].to_numpy(dtype=int)
    L = len(s)
    if zmap is not None:
        zm, zok = zmap.matrix(df["date"])
        ver = zmap.version
    else:
        zm, zok, ver = np.full((L, 49), -1, dtype=int), np.zeros(L, dtype=bool), None
    zc = np.zeros((L, 12), dtype=int)
    for i in np.where(zok)[0]:
        zc[i] = np.bincount(zm[i], minlength=12)
    return Ctx(s, df["draw_id"].to_numpy(), df["date"].to_numpy(), zm, zc, zok, ver)
