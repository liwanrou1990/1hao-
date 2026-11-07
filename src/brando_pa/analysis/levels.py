"""关键价位（区域）识别——全部量化、全部基于已确认摆动点与已收盘 K 线。

区域 = 一组价格相近（< cluster_atr×ATR）的已确认摆动点聚类；半宽 = max(zone_width_atr×ATR, zone_min_width_pct×价格)。
统计：触及次数（K 线进入区域且收盘未越过；两次触及间隔 ≥ touch_gap_bars）、有效突破次数（收盘越过边界 ≥ break_buffer×ATR，
并在随后 break_confirm_bars 根收盘仍在区域外）、支撑/阻力转换（曾被有效突破且现价在另一侧）。
事件分类 classify_bar：none / touch / break_up / break_down / wick_up / wick_down（影线越过但收盘回到原侧 = 噪声/假突破）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .swings import Swing, confirmed_up_to


@dataclass
class Zone:
    tf: str
    lo: float
    hi: float
    center: float
    swing_prices: list[float]
    swing_kinds: list[str]
    first_idx: int
    touches: int = 0
    breaks: int = 0
    flipped: bool = False
    last_touch_idx: int | None = None
    strength: float = 0.0
    tfs: list[str] = field(default_factory=list)
    zone_id: str = ""

    def role(self, price: float) -> str:
        if price > self.hi:
            return "support"
        if price < self.lo:
            return "resistance"
        return "inside"

    def to_dict(self) -> dict:
        return {"zone_id": self.zone_id, "tf": self.tf, "tfs": self.tfs or [self.tf], "lo": self.lo, "hi": self.hi, "center": self.center, "touches": self.touches,
                "breaks": self.breaks, "flipped": self.flipped, "strength": round(self.strength, 3), "n_swings": len(self.swing_prices)}


def cluster_swings(swings: list[Swing], atr_value: float, cluster_atr: float) -> list[list[Swing]]:
    if not swings:
        return []
    s = sorted(swings, key=lambda x: x.price)
    groups: list[list[Swing]] = [[s[0]]]
    for sw in s[1:]:
        ref = sum(g.price for g in groups[-1]) / len(groups[-1])
        if abs(sw.price - ref) <= cluster_atr * atr_value:
            groups[-1].append(sw)
        else:
            groups.append([sw])
    return groups


def _count_touches_breaks(high: np.ndarray, low: np.ndarray, close: np.ndarray, start: int, end: int, lo: float, hi: float, buf: float, gap: int, confirm: int) -> tuple[int, int, int | None, bool]:
    """在 [start, end] 索引范围内统计触及与有效突破（向量化；语义与逐根循环版本一致）。返回 (touches, breaks, last_touch_idx, flipped)。
    side：收盘在区域上方(+1)/下方(-1)/内部(0)；side_prev：之前最近一次非零 side（前向填充）。
    触及：K 线进入区域且（收盘在内部 或 收盘仍在原侧），两次触及间隔 ≥ gap。
    有效突破：收盘从一侧穿到另一侧，且随后 confirm 根收盘仍在新一侧（需在范围内可验证）。"""
    c = close[start:end + 1]; h = high[start:end + 1]; l = low[start:end + 1]
    n = len(c)
    if n == 0:
        return 0, 0, None, False
    side = np.where(c > hi + buf, 1, np.where(c < lo - buf, -1, 0))
    # side_prev[i] = 最近一次 i 之前的非零 side（无则 0）
    nz = side != 0
    idx = np.where(nz, np.arange(n), -1)
    last_nz = np.maximum.accumulate(idx)
    prev_pos = np.concatenate(([-1], last_nz[:-1]))
    side_prev = np.where(prev_pos >= 0, side[np.clip(prev_pos, 0, n - 1)], 0)
    entered = (l <= hi) & (h >= lo)
    touch_mask = entered & ((side == 0) | ((side_prev != 0) & (side == side_prev)))
    touches = 0; last_touch = None; last_t = -10 ** 9
    for i in np.flatnonzero(touch_mask):
        if i - last_t >= gap:
            touches += 1; last_touch = int(i) + start
        last_t = int(i)
    breaks = 0
    trans = np.flatnonzero((side != 0) & (side_prev != 0) & (side != side_prev))
    for i in trans:
        if i + confirm > n - 1:
            continue
        seg = side[i + 1: i + 1 + confirm]
        if np.all(seg == side[i]):
            breaks += 1
    return touches, breaks, last_touch, breaks > 0


def build_zones(bars: pd.DataFrame, swings: list[Swing], last_closed_idx: int, atr_arr: np.ndarray, tf: str, lv: dict) -> list[Zone]:
    """以 last_closed_idx（该周期最后一根已收盘 K 线）为决策点构建区域。"""
    if last_closed_idx < 0 or last_closed_idx >= len(bars):
        return []
    a = atr_arr[last_closed_idx]
    if not np.isfinite(a) or a <= 0:
        return []
    lb = int(lv["lookback_bars"].get(tf, 400))
    start = max(0, last_closed_idx - lb)
    usable = [s for s in confirmed_up_to(swings, last_closed_idx) if s.idx >= start]
    if not usable:
        return []
    high, low, close = bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    price = close[last_closed_idx]
    half_atr = float(lv["zone_width_atr"].get(tf, 0.3)) * a
    buf = float(lv["break_buffer_atr"]) * a
    zones: list[Zone] = []
    for grp in cluster_swings(usable, a, float(lv["cluster_atr"])):
        _ps = sorted(g.price for g in grp); _m = len(_ps) // 2
        center = float(_ps[_m] if len(_ps) % 2 else 0.5 * (_ps[_m - 1] + _ps[_m]))
        half = min(max(half_atr, float(lv["zone_min_width_pct"]) * center), float(lv.get("zone_max_width_pct", 0.01)) * center)
        z = Zone(tf=tf, lo=center - half, hi=center + half, center=center, swing_prices=[g.price for g in grp], swing_kinds=[g.kind for g in grp],
                 first_idx=min(g.idx for g in grp), tfs=[tf])
        t, b, lt, fl = _count_touches_breaks(high, low, close, z.first_idx, last_closed_idx, z.lo, z.hi, buf, int(lv["touch_gap_bars"]), int(lv["break_confirm_bars"]))
        z.touches, z.breaks, z.last_touch_idx = max(t, len(grp)), b, lt
        z.flipped = fl and z.role(price) != ("resistance" if grp[0].kind == "H" else "support")
        recency = 1.0 + 0.5 * (1.0 - (last_closed_idx - (lt if lt is not None else z.first_idx)) / max(lb, 1))
        z.strength = float(lv["tf_weight"].get(tf, 1.0)) * (z.touches + 0.5 * len(grp) + (0.5 if z.flipped else 0.0)) * recency
        z.zone_id = f"{tf}:{center:.2f}"
        if z.touches >= int(lv["min_touches"]):
            zones.append(z)
    # 每侧最多 N 个（按强度）
    sup = sorted([z for z in zones if z.role(price) == "support"], key=lambda z: -z.strength)[: int(lv["max_zones_each_side"])]
    res = sorted([z for z in zones if z.role(price) == "resistance"], key=lambda z: -z.strength)[: int(lv["max_zones_each_side"])]
    inside = [z for z in zones if z.role(price) == "inside"]
    return sorted(sup + res + inside, key=lambda z: z.center)


def merge_zones(zone_lists: list[list[Zone]]) -> list[Zone]:
    """跨周期合并重叠区域：高周期权重优先，触及次数相加，tfs 合并。"""
    all_z = sorted([z for zl in zone_lists for z in zl], key=lambda z: z.center)
    out: list[Zone] = []
    for z in all_z:
        if out and (out[-1].lo <= z.center <= out[-1].hi or z.lo <= out[-1].center <= z.hi):   # 中心落入对方区域才合并，避免链式黏连
            m = out[-1]
            keep, other = (m, z) if m.strength >= z.strength else (z, m)
            merged = Zone(tf=keep.tf, lo=min(m.lo, z.lo), hi=max(m.hi, z.hi), center=keep.center, swing_prices=m.swing_prices + z.swing_prices,
                          swing_kinds=m.swing_kinds + z.swing_kinds, first_idx=keep.first_idx, touches=m.touches + z.touches, breaks=max(m.breaks, z.breaks),
                          flipped=m.flipped or z.flipped, last_touch_idx=keep.last_touch_idx, strength=m.strength + z.strength, tfs=sorted(set((m.tfs or [m.tf]) + (z.tfs or [z.tf]))),
                          zone_id=keep.zone_id)
            out[-1] = merged
        else:
            out.append(z)
    return out


def classify_bar(o: float, h: float, l: float, c: float, prev_close: float, zone: Zone, buf: float) -> str:
    """单根 K 线相对区域的事件：none / touch / break_up / break_down / wick_up / wick_down。
    prev_close 决定原侧；buf = break_buffer_atr × ATR。"""
    entered = (l <= zone.hi) and (h >= zone.lo)
    if c > zone.hi + buf and prev_close <= zone.hi + buf:
        return "break_up"
    if c < zone.lo - buf and prev_close >= zone.lo - buf:
        return "break_down"
    if prev_close <= zone.hi and h > zone.hi + buf and c <= zone.hi:
        return "wick_up"
    if prev_close >= zone.lo and l < zone.lo - buf and c >= zone.lo:
        return "wick_down"
    if entered:
        return "touch"
    return "none"


def nearest_zone(zones: list[Zone], price: float, direction: str) -> Zone | None:
    """direction='up' 取上方最近阻力；'down' 取下方最近支撑。"""
    if direction == "up":
        cands = [z for z in zones if z.lo > price]
        return min(cands, key=lambda z: z.lo) if cands else None
    cands = [z for z in zones if z.hi < price]
    return max(cands, key=lambda z: z.hi) if cands else None
