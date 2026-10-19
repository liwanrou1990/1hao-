"""技术指标（全部因果：只用 rolling/ewm/shift(正数)）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    m = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": m, "upper": m + k * sd, "lower": m - k * sd, "width": (2 * k * sd) / m})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """按 America/New_York 交易日重置的 VWAP（只用当日已完成K线）。"""
    day = pd.to_datetime(df["ts_ny"]).dt.date if "ts_ny" in df else df["ts"].dt.tz_convert("America/New_York").dt.date
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0)
    pv = (tp * vol).groupby(day).cumsum()
    v = vol.groupby(day).cumsum()
    out = pv / v.replace(0, np.nan)
    return out.fillna(tp)


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n, min_periods=n).max()  # 不含当前K线，避免用当根高点


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n, min_periods=n).min()


def rel_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def session_minute_index(df: pd.DataFrame) -> pd.Series:
    """当日第几根K线（从 0 开始），用于禁止接近收盘开仓。"""
    day = pd.to_datetime(df["ts_ny"]).dt.date
    return df.groupby(day).cumcount()


def bars_remaining_in_session(df: pd.DataFrame, session_len: int = 390) -> pd.Series:
    return session_len - 1 - session_minute_index(df)
