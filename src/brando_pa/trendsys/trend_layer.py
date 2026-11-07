"""趋势层：只用已收盘日线的 SMA200 状态。

策略 A（对照）：日线收盘 > SMA200 → ON，否则 OFF（confirm_days = 1，与第五阶段 sma200_benchmark 完全一致）。
策略 B：状态切换需连续 confirm_days 个已收盘日线在 SMA200 的另一侧（上穿、下穿对称）。
SMA 未形成（不足 sma 根）→ 无信号 = OFF。初始状态 OFF，需满足确认条数后才 ON。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trend_signal(d1: pd.DataFrame, sma: int = 200, confirm_days: int = 1) -> pd.DataFrame:
    c = d1["close"].astype(float).reset_index(drop=True)
    s = c.rolling(int(sma)).mean()
    above = (c > s).to_numpy() & np.isfinite(s.to_numpy())
    valid = np.isfinite(s.to_numpy())
    on = np.zeros(len(c), bool); state = False; run = 0
    for k in range(len(c)):
        if not valid[k]:
            on[k] = False; run = 0; continue
        if above[k] != state:
            run += 1
            if run >= int(confirm_days):
                state = above[k]; run = 0
        else:
            run = 0
        on[k] = state
    out = pd.DataFrame({"ts": pd.to_datetime(d1["ts"], utc=True).reset_index(drop=True), "close": c, "sma": s, "above": above, "on": on})
    out["switch"] = out["on"] != out["on"].shift(fill_value=False)
    return out


class TrendLayer:
    """对执行周期 K 线 i，用 mc.htf['1d']['idx'][i]（收盘时刻 ≤ 决策时刻的最后一根日线）取状态；无未来数据。"""

    def __init__(self, mc, sma: int = 200, confirm_days: int = 1):
        self.mc = mc; self.sma = int(sma); self.confirm_days = int(confirm_days)
        self.daily = trend_signal(mc.bars["1d"], sma, confirm_days)
        self._on = self.daily["on"].to_numpy(); self._above = self.daily["above"].to_numpy()
        self._close = self.daily["close"].to_numpy(float); self._sma = self.daily["sma"].to_numpy(float)
        self.idx1d = mc.htf["1d"]["idx"]

    def at(self, i: int) -> dict:
        j = int(self.idx1d[i])
        if j < 0:
            return {"j": -1, "on": False, "above": False, "close_1d": None, "sma": None, "daily_drop": 0.0}
        drop = float(self._close[j] / self._close[j - 1] - 1) if j >= 1 and self._close[j - 1] > 0 else 0.0
        return {"j": j, "on": bool(self._on[j]), "above": bool(self._above[j]), "close_1d": float(self._close[j]), "sma": float(self._sma[j]) if np.isfinite(self._sma[j]) else None, "daily_drop": drop, "ts_1d": self.daily["ts"].iloc[j]}
