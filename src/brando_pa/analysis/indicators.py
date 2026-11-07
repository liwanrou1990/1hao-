"""基础指标（只用已收盘 K 线；全部为滚动窗口，不含未来值）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """简单滚动均值 ATR（前 n-1 根为 NaN；调用方须在 ATR 可用后才产生信号）。"""
    return true_range(df).rolling(n, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff(); dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0); minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    atr_n = tr.rolling(n, min_periods=n).sum()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n, min_periods=n).sum() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n, min_periods=n).sum() / atr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(n, min_periods=n).mean()


def realized_vol(close: pd.Series, n: int = 20, periods_per_year: float = 365.0) -> pd.Series:
    r = np.log(close).diff()
    return r.rolling(n, min_periods=n).std() * np.sqrt(periods_per_year)


def rel_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """当前根成交量 / 之前 n 根均量（不含当前根，避免自我放大）。"""
    base = volume.shift(1).rolling(n, min_periods=n).mean()
    return volume / base.replace(0, np.nan)
