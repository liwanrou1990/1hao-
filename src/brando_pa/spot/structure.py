"""低周期执行区域：由执行周期（1h）**已确认**摆动高低点得到本地支撑/阻力（单价位 + 小容差），不使用高周期区域宽度。
只依赖当时已出现并已确认的 K 线（confirmed_idx ≤ i）。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analysis.swings import Swing


@dataclass
class LocalLevel:
    price: float
    kind: str              # "R"（阻力：摆动高）/ "S"（支撑：摆动低）
    idx: int               # 最近一次构成该价位的摆动点索引
    confirmed_idx: int
    touches: int           # 容差内聚类的摆动点数
    broken: bool = False   # 自形成以来是否已被收盘有效越过（阻力被向上收破 / 支撑被向下收破）

    def to_dict(self) -> dict:
        return {"price": self.price, "kind": self.kind, "idx": self.idx, "touches": self.touches, "broken": self.broken}


def local_levels(swings: list[Swing], i: int, close: np.ndarray, atr_i: float, lc: dict) -> tuple[list[LocalLevel], list[LocalLevel]]:
    """返回 (阻力列表[价格升序，位于当前收盘之上], 支撑列表[价格降序，位于当前收盘之下])。
    swings 必须只含 confirmed_idx ≤ i 的摆动点；lookback_bars 内；min_level_age_bars 过滤刚确认的点；容差聚类；
    标记 broken：形成后到 i 之间是否有收盘越过（阻力：close > price + tol；支撑：close < price − tol）。"""
    if i < 0 or not np.isfinite(atr_i) or atr_i <= 0:
        return [], []
    lb = int(lc["lookback_bars"]); tol = float(lc["level_tol_atr"]) * atr_i; age = int(lc["min_level_age_bars"])
    px = float(close[i])
    cand = [s for s in swings if s.confirmed_idx <= i - age and s.idx >= i - lb]
    res: list[LocalLevel] = []; sup: list[LocalLevel] = []
    for kind, lst, pool in (("R", res, [s for s in cand if s.kind == "H"]), ("S", sup, [s for s in cand if s.kind == "L"])):
        pool = sorted(pool, key=lambda s: s.price)
        groups: list[list[Swing]] = []
        for s in pool:
            if groups and abs(s.price - groups[-1][-1].price) <= tol:
                groups[-1].append(s)
            else:
                groups.append([s])
        for g in groups:
            price = float(np.mean([s.price for s in g])); last = max(g, key=lambda s: s.idx)
            seg = close[last.idx + 1: i]                       # 不含当前根：当前根是否突破由策略判断
            broken = bool(np.any(seg > price + tol)) if kind == "R" else bool(np.any(seg < price - tol))
            lst.append(LocalLevel(price, kind, last.idx, last.confirmed_idx, len(g), broken))
    n = int(lc["max_levels_each_side"])
    res = sorted([l for l in res if l.price > px], key=lambda l: l.price)[:n]
    sup = sorted([l for l in sup if l.price < px], key=lambda l: -l.price)[:n]
    return res, sup


def all_levels(swings: list[Swing], i: int, close: np.ndarray, atr_i: float, lc: dict) -> list[LocalLevel]:
    """不按当前价格分侧的全部本地价位（供策略跟踪“被突破的阻力”等）。"""
    if i < 0 or not np.isfinite(atr_i) or atr_i <= 0:
        return []
    lb = int(lc["lookback_bars"]); tol = float(lc["level_tol_atr"]) * atr_i; age = int(lc["min_level_age_bars"])
    cand = [s for s in swings if s.confirmed_idx <= i - age and s.idx >= i - lb]
    out: list[LocalLevel] = []
    for kind in ("R", "S"):
        pool = sorted([s for s in cand if s.kind == ("H" if kind == "R" else "L")], key=lambda s: s.price)
        groups: list[list[Swing]] = []
        for s in pool:
            if groups and abs(s.price - groups[-1][-1].price) <= tol:
                groups[-1].append(s)
            else:
                groups.append([s])
        for g in groups:
            price = float(np.mean([s.price for s in g])); last = max(g, key=lambda s: s.idx)
            seg = close[last.idx + 1: i]
            broken = bool(np.any(seg > price + tol)) if kind == "R" else bool(np.any(seg < price - tol))
            out.append(LocalLevel(price, kind, last.idx, last.confirmed_idx, len(g), broken))
    return out


def last_confirmed_swing_low(swings: list[Swing], i: int, after_idx: int | None = None) -> float | None:
    """最近一个已确认（confirmed_idx ≤ i）的摆动低点价格；after_idx 给定时只取 idx > after_idx 的。"""
    lows = [s for s in swings if s.kind == "L" and s.confirmed_idx <= i and (after_idx is None or s.idx > after_idx)]
    return lows[-1].price if lows else None
