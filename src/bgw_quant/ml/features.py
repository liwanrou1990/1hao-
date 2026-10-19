"""ML 特征（全部因果）：仅用截至当根已完成K线的信息。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategies import indicators as ind

FEATURE_COLUMNS: list[str] = []


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    c = df["close"]
    f = pd.DataFrame(index=df.index)
    logc = np.log(c)
    for k in (1, 3, 5, 15, 30):
        f[f"ret_{k}"] = logc.diff(k)
    a = ind.atr(df, 14)
    f["atr_rel"] = a / c
    f["rv_30"] = logc.diff().rolling(30, min_periods=30).std()
    f["rv_ratio"] = f["rv_30"] / logc.diff().rolling(120, min_periods=60).std()
    for n in (9, 21, 50):
        f[f"ema{n}_dist"] = (c - ind.ema(c, n)) / (a.replace(0, np.nan))
    f["ema_fast_slow"] = (ind.ema(c, 9) - ind.ema(c, 21)) / a.replace(0, np.nan)
    m = ind.macd(c)
    f["macd_hist"] = m["hist"] / c
    f["rsi14"] = ind.rsi(c, 14) / 100
    b = ind.bollinger(c, 20)
    f["bb_pos"] = (c - b["lower"]) / (b["upper"] - b["lower"]).replace(0, np.nan)
    f["bb_width"] = b["width"]
    v = ind.session_vwap(df)
    f["vwap_dist"] = (c - v) / a.replace(0, np.nan)
    if df["volume"].notna().any():
        f["rel_vol"] = ind.rel_volume(df["volume"], 20)
        f["vol_z"] = (df["volume"] - df["volume"].rolling(60, min_periods=30).mean()) / df["volume"].rolling(60, min_periods=30).std()
    else:
        f["rel_vol"] = np.nan
        f["vol_z"] = np.nan
    f["hi_dist"] = (ind.rolling_high(df["high"], 30) - c) / a.replace(0, np.nan)
    f["lo_dist"] = (c - ind.rolling_low(df["low"], 30)) / a.replace(0, np.nan)
    f["range_pos"] = (c - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    mi = ind.session_minute_index(df)
    f["minute_of_day"] = mi / 390
    f["bars_remaining"] = ind.bars_remaining_in_session(df) / 390
    # 当日开盘以来收益
    day = pd.to_datetime(df["ts_ny"]).dt.date
    day_open = df.groupby(day)["open"].transform("first")
    f["ret_from_open"] = np.log(c / day_open)
    global FEATURE_COLUMNS
    FEATURE_COLUMNS = list(f.columns)
    return f.replace([np.inf, -np.inf], np.nan)
