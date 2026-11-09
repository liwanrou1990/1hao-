"""支撑 / 阻力区（当时已确认）：用 ≤ i 已确认的日线与周线摆动点聚类（簇内价差 ≤ 0.5 ATR），区域向外扩 0.25 ATR，强度 = 触碰数（周线点计 2），
只保留最近 lookback 日内形成的摆动点；阻力被日收盘有效突破后标记 flip（转支撑）。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .structure import confirmed_swings_until


@dataclass
class Zone:
    low: float
    high: float
    strength: int
    kind: str                      # "support" | "resistance"（相对当前价）
    last_touch_idx: int
    flipped: bool = False
    members: list = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


class SupportResistanceZones:
    def __init__(self, daily_swings: pd.DataFrame, weekly_swings: pd.DataFrame | None, weekly_to_daily_idx, atr: np.ndarray, lookback: int = 400, cluster_atr: float = 0.5, pad_atr: float = 0.25):
        self.dsw = daily_swings; self.wsw = weekly_swings; self.w2d = weekly_to_daily_idx; self.atr = atr; self.lookback = lookback; self.cluster_atr = cluster_atr; self.pad_atr = pad_atr
        self._cache: dict[int, list] = {}

    def _points(self, i: int) -> pd.DataFrame:
        d = confirmed_swings_until(self.dsw, i); d = d[d["idx"] >= i - self.lookback].assign(weight=1)
        if self.wsw is not None and len(self.wsw):
            w = self.wsw.copy(); w["didx"] = [self.w2d(int(x)) for x in w["idx"]]; w["dconf"] = [self.w2d(int(x)) for x in w["confirmed_at"]]
            w = w[(w["dconf"] <= i) & (w["didx"] >= i - self.lookback)]; w = pd.DataFrame({"idx": w["didx"], "kind": w["kind"], "price": w["price"], "confirmed_at": w["dconf"], "weight": 2})
            d = pd.concat([d, w], ignore_index=True)
        return d.sort_values("price").reset_index(drop=True)

    def zones_at(self, i: int, close: float) -> list[Zone]:
        if i in self._cache:
            return self._cache[i]
        a = float(self.atr[i]) if np.isfinite(self.atr[i]) else None; pts = self._points(i)
        if a is None or pts.empty:
            self._cache[i] = []; return []
        zones = []; cur = []
        for _, p in pts.iterrows():
            if cur and p["price"] - cur[-1]["price"] > self.cluster_atr * a:
                zones.append(cur); cur = []
            cur.append(p.to_dict())
        if cur:
            zones.append(cur)
        out = []
        for m in zones:
            lo = min(x["price"] for x in m) - self.pad_atr * a; hi = max(x["price"] for x in m) + self.pad_atr * a; strength = int(sum(x["weight"] for x in m))
            out.append(Zone(lo, hi, strength, "support" if hi < close else ("resistance" if lo > close else "inside"), int(max(x["idx"] for x in m)), members=[(int(x["idx"]), x["kind"], float(x["price"])) for x in m]))
        self._cache[i] = out; return out

    @staticmethod
    def nearest_support(zones: list[Zone], price: float) -> Zone | None:
        c = [z for z in zones if z.high < price]; return max(c, key=lambda z: z.high) if c else None

    @staticmethod
    def resistances_above(zones: list[Zone], price: float) -> list[Zone]:
        return sorted([z for z in zones if z.low > price], key=lambda z: z.low)

    @staticmethod
    def zone_containing(zones: list[Zone], price: float, tol: float = 0.0) -> Zone | None:
        for z in zones:
            if z.low - tol <= price <= z.high + tol:
                return z
        return None
