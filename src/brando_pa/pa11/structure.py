"""结构识别（全部“当时已确认”）：ATR、分形摆动点（确认滞后 k 根）、HH/HL 序列、周线趋势、日线 SMA200 方向过滤。
所有函数返回与输入等长的数组/表，第 i 行只依赖 ≤ i 的数据；摆动点在第 i+k 根收盘后才出现（confirmed_at 索引）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float); pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def swings(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """分形摆动点：第 i 根为摆动高点 ⇔ high[i] 严格大于左右各 k 根的 high（低点对称）。confirmed_at = i + k（该根收盘后可用）。"""
    h = df["high"].to_numpy(float); l = df["low"].to_numpy(float); n = len(df); rows = []
    for i in range(k, n - k):
        if h[i] > h[i - k:i].max() and h[i] > h[i + 1:i + k + 1].max():
            rows.append({"idx": i, "kind": "H", "price": h[i], "confirmed_at": i + k})
        if l[i] < l[i - k:i].min() and l[i] < l[i + 1:i + k + 1].min():
            rows.append({"idx": i, "kind": "L", "price": l[i], "confirmed_at": i + k})
    return pd.DataFrame(rows, columns=["idx", "kind", "price", "confirmed_at"])


def confirmed_swings_until(sw: pd.DataFrame, i: int) -> pd.DataFrame:
    return sw[sw["confirmed_at"] <= i]


def structure_state(sw: pd.DataFrame, i: int) -> dict:
    """第 i 根收盘后可见的最近结构：最近两个已确认高点/低点与 HH/HL 判定。"""
    s = confirmed_swings_until(sw, i); hs = s[s.kind == "H"].tail(2); ls = s[s.kind == "L"].tail(2)
    hh = len(hs) == 2 and hs.price.iloc[1] > hs.price.iloc[0]; hl = len(ls) == 2 and ls.price.iloc[1] > ls.price.iloc[0]
    lh = len(hs) == 2 and hs.price.iloc[1] < hs.price.iloc[0]; ll = len(ls) == 2 and ls.price.iloc[1] < ls.price.iloc[0]
    return {"hh": bool(hh), "hl": bool(hl), "lh": bool(lh), "ll": bool(ll), "last_high": float(hs.price.iloc[-1]) if len(hs) else None, "last_low": float(ls.price.iloc[-1]) if len(ls) else None,
            "last_high_idx": int(hs.idx.iloc[-1]) if len(hs) else None, "last_low_idx": int(ls.idx.iloc[-1]) if len(ls) else None, "prev_low": float(ls.price.iloc[0]) if len(ls) == 2 else None}


def weekly_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """日线 → 周线（UTC 周一 00:00 开始，标签 = 周开始；周线在该周最后一个日线收盘后才算收盘）。"""
    d = daily.set_index(pd.DatetimeIndex(pd.to_datetime(daily["ts"], utc=True)))
    w = d.resample("W-MON", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    w["week_end"] = w.index + pd.Timedelta(days=7) - pd.Timedelta(days=1)                 # 该周最后一个日线日期
    return w.reset_index().rename(columns={"index": "ts"})


class WeeklyTrendDetector:
    """UP：最近两个已确认周线摆动低点递增、最近两个已确认摆动高点递增、周收盘 > 周 SMA30；DOWN 对称；否则 RANGE。
    只用已完整收盘的周线：第 t 日可见的周线 = week_end < t 的那些。"""

    def __init__(self, daily: pd.DataFrame, k: int = 2, sma: int = 30):
        self.w = weekly_bars(daily); self.w["sma"] = self.w["close"].rolling(sma).mean(); self.sw = swings(self.w, k); self.k = k
        self.daily_ts = pd.to_datetime(daily["ts"], utc=True).to_numpy(); self.week_end = self.w["week_end"].to_numpy()

    def at(self, i: int) -> dict:
        t = self.daily_ts[i]; j = int(np.searchsorted(self.week_end, t, side="left")) - 1      # 最后一根已收盘周线
        if j < 1:
            return {"trend": "RANGE", "weekly_idx": j, "reason": "周线不足"}
        st = structure_state(self.sw, j); c = float(self.w["close"].iloc[j]); sma = self.w["sma"].iloc[j]
        above = bool(np.isfinite(sma) and c > sma)
        if st["hh"] and st["hl"] and above:
            tr = "UP"
        elif st["lh"] and st["ll"] and not above:
            tr = "DOWN"
        else:
            tr = "RANGE"
        return {"trend": tr, "weekly_idx": j, "hh": st["hh"], "hl": st["hl"], "above_sma30w": above, "weekly_close": c, "weekly_sma30": float(sma) if np.isfinite(sma) else None}


class DailyStructureDetector:
    def __init__(self, daily: pd.DataFrame, k: int = 3, sma: int = 200, slope_lookback: int = 20):
        self.d = daily.reset_index(drop=True); self.sw = swings(self.d, k); self.k = k
        self.sma = self.d["close"].rolling(sma).mean().to_numpy(); self.slope_lb = slope_lookback; self.atr = atr(self.d).to_numpy(); self.close = self.d["close"].to_numpy(float)
        self.vol = self.d["volume"].to_numpy(float); self.vol_ma20 = self.d["volume"].rolling(20).mean().to_numpy()

    def at(self, i: int) -> dict:
        st = structure_state(self.sw, i); s = self.sma[i]; s0 = self.sma[i - self.slope_lb] if i >= self.slope_lb else np.nan
        ok = bool(np.isfinite(s) and np.isfinite(s0) and self.close[i] > s and s > s0)
        return st | {"sma200": float(s) if np.isfinite(s) else None, "sma200_rising": bool(np.isfinite(s) and np.isfinite(s0) and s > s0), "above_sma200": bool(np.isfinite(s) and self.close[i] > s), "sma_filter_ok": ok, "atr": float(self.atr[i]) if np.isfinite(self.atr[i]) else None}
