"""TrendRegimeEngine：日线趋势状态 BULL / BEAR / RANGE / UNCERTAIN，只用已收盘日线；4h 结构（HH/HL/LH/LL）作中期变化；1h 作交易机会。

日线规则（按日线收盘 t 计算，对执行 K 线按“日线收盘时刻 ≤ 决策时刻”对齐，由 MarketContext.htf['1d']['idx'] 保证）：
  sma50, sma200, slope50 = sma50[t]/sma50[t−L] − 1, atr(14), hi60 = 过去 60 日最高收盘（不含当日）, lo60 = 过去 60 日最低收盘（不含当日）
  raw：
    BULL  ：close > sma200 且 sma50 > sma200 + band 且 slope50 > slope_min
    BEAR  ：close < sma200 且 sma50 < sma200 − band 且 slope50 < −slope_min
    RANGE ：|sma50 − sma200| ≤ band（均线纠缠），或 close 在 sma50/sma200 之间且 slope 接近 0
    UNCERTAIN：其余（例如 close > sma200 但 sma50 斜率为负 / 结构与均线矛盾）
  结构修正：raw=BULL 但 close 创 60 日新低 → UNCERTAIN；raw=BEAR 但 close 创 60 日新高 → UNCERTAIN。
  确认：状态切换需连续 confirm_days 个日线收盘给出同一新状态，否则维持旧状态（滞后，防抖）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.indicators import atr

STATES = ("BULL", "BEAR", "RANGE", "UNCERTAIN")


def daily_regime(d1: pd.DataFrame, rc: dict) -> pd.DataFrame:
    c = d1["close"].astype(float)
    f, s = int(rc["sma_fast"]), int(rc["sma_slow"])
    sma_f = c.rolling(f).mean(); sma_s = c.rolling(s).mean()
    L = int(rc["slope_lookback_days"])
    slope = sma_f / sma_f.shift(L) - 1
    a = atr(d1, int(rc["atr_n"]))
    band = float(rc["band_atr"]) * a
    lb = int(rc["structure_lookback_days"])
    hi = c.shift(1).rolling(lb).max(); lo = c.shift(1).rolling(lb).min()
    smin = float(rc["slope_min"])
    raw = np.full(len(d1), "UNCERTAIN", dtype=object)
    ok = sma_s.notna() & a.notna() & slope.notna()
    bull = ok & (c > sma_s) & (sma_f > sma_s + band) & (slope > smin)
    bear = ok & (c < sma_s) & (sma_f < sma_s - band) & (slope < -smin)
    rng = ok & ((sma_f - sma_s).abs() <= band)
    raw[rng.to_numpy()] = "RANGE"; raw[bull.to_numpy()] = "BULL"; raw[bear.to_numpy()] = "BEAR"
    new_low = (c < lo).to_numpy(); new_high = (c > hi).to_numpy()
    raw[(raw == "BULL") & new_low] = "UNCERTAIN"; raw[(raw == "BEAR") & new_high] = "UNCERTAIN"
    raw[~ok.to_numpy()] = "UNCERTAIN"
    # 确认滞后
    n = int(rc.get("confirm_days", 1)); state = []; cur = "UNCERTAIN"; streak = 0; last_raw = None
    for r in raw:
        if r == last_raw:
            streak += 1
        else:
            streak = 1; last_raw = r
        if r != cur and streak >= n:
            cur = r
        state.append(cur)
    out = pd.DataFrame({"ts": d1["ts"], "close": c, "sma50": sma_f, "sma200": sma_s, "slope50": slope, "atr": a, "hi60": hi, "lo60": lo, "raw": raw, "state": state})
    out["new_60d_high"] = new_high; out["new_60d_low"] = new_low
    return out


class TrendRegimeEngine:
    """把日线状态、4h 结构对齐到执行周期索引（PIT）。"""

    def __init__(self, mc, rc: dict):
        self.mc = mc; self.rc = rc
        self.daily = daily_regime(mc.bars["1d"], rc)
        self._state = self.daily["state"].to_numpy(); self._atr = self.daily["atr"].to_numpy(float); self._sma50 = self.daily["sma50"].to_numpy(float); self._sma200 = self.daily["sma200"].to_numpy(float)
        self._lo60 = self.daily["lo60"].to_numpy(float); self._close = self.daily["close"].to_numpy(float)
        self.idx1d = mc.htf["1d"]["idx"]

    def at(self, i: int) -> dict:
        j = int(self.idx1d[i])
        if j < 0:
            return {"state": "UNCERTAIN", "j": -1}
        st = self.mc.structure_at(i)
        return {"state": str(self._state[j]), "j": j, "atr_1d": float(self._atr[j]) if np.isfinite(self._atr[j]) else None, "sma50": float(self._sma50[j]) if np.isfinite(self._sma50[j]) else None,
                "sma200": float(self._sma200[j]) if np.isfinite(self._sma200[j]) else None, "lo60": float(self._lo60[j]) if np.isfinite(self._lo60[j]) else None, "close_1d": float(self._close[j]),
                "trend_4h": st.trend if st else "undefined"}

    def bars_in_state(self, i: int, state: str) -> int:
        j = int(self.idx1d[i]); k = 0
        while j - k >= 0 and self._state[j - k] == state:
            k += 1
        return k

    def days_not_in(self, i: int, state: str) -> int:
        """最近连续多少个已收盘日线不处于 state。"""
        j = int(self.idx1d[i]); k = 0
        while j - k >= 0 and self._state[j - k] != state:
            k += 1
        return k
