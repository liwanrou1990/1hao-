"""4H 市场结构：HH / HL / LH / LL 标注与趋势状态（up / down / range），只用已确认摆动点。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .swings import Swing, confirmed_up_to


@dataclass
class StructureState:
    trend: str                  # up / down / range / undefined
    labels: list[tuple[int, str]]   # (idx, "HH"/"HL"/"LH"/"LL"/"EQH"/"EQL")
    last_high: float | None
    last_low: float | None
    detail: str

    def to_dict(self) -> dict:
        return {"trend": self.trend, "last_high": self.last_high, "last_low": self.last_low, "recent_labels": [l for _, l in self.labels[-6:]], "detail": self.detail}


def label_swings(swings: list[Swing], atr_value: float, range_atr: float) -> list[tuple[int, str]]:
    tol = range_atr * atr_value
    labels: list[tuple[int, str]] = []
    prev_h = prev_l = None
    for s in swings:
        if s.kind == "H":
            if prev_h is None:
                lab = "H"
            elif s.price > prev_h + tol:
                lab = "HH"
            elif s.price < prev_h - tol:
                lab = "LH"
            else:
                lab = "EQH"
            prev_h = s.price
        else:
            if prev_l is None:
                lab = "L"
            elif s.price > prev_l + tol:
                lab = "HL"
            elif s.price < prev_l - tol:
                lab = "LL"
            else:
                lab = "EQL"
            prev_l = s.price
        labels.append((s.idx, lab))
    return labels


def structure_state(swings: list[Swing], last_closed_idx: int, atr_value: float, range_atr: float, lookback_swings: int = 12) -> StructureState:
    usable = confirmed_up_to(swings, last_closed_idx)
    if len(usable) < 4 or not np.isfinite(atr_value):
        return StructureState("undefined", [], None, None, "已确认摆动点不足 4 个")
    usable = usable[-lookback_swings:]
    labels = label_swings(usable, atr_value, range_atr)
    hs = [l for _, l in labels if l in ("HH", "LH", "EQH")]
    ls = [l for _, l in labels if l in ("HL", "LL", "EQL")]
    last_h = next((s.price for s in reversed(usable) if s.kind == "H"), None)
    last_l = next((s.price for s in reversed(usable) if s.kind == "L"), None)
    if not hs or not ls:
        return StructureState("undefined", labels, last_h, last_l, "缺少可比较的高/低点")
    lh, ll = hs[-1], ls[-1]
    if lh == "HH" and ll == "HL":
        trend, d = "up", "最近摆动高为 HH、摆动低为 HL"
    elif lh == "LH" and ll == "LL":
        trend, d = "down", "最近摆动高为 LH、摆动低为 LL"
    else:
        trend, d = "range", f"最近结构 {lh}/{ll}，不构成单边趋势"
    return StructureState(trend, labels, last_h, last_l, d)
