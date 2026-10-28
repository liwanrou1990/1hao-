"""日线技术指标（全部因果：rolling/ewm/shift 正数）。复用现有 bgw_quant.strategies.indicators 的 RSI/MACD/ATR 实现。"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from bgw_quant.strategies import indicators as _ind
except ImportError:  # 独立部署时的最小实现
    _ind = None


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    if _ind:
        return _ind.rsi(close, n)
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(close: pd.Series) -> pd.DataFrame:
    if _ind:
        return _ind.macd(close)
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    sig = line.ewm(span=9, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    if _ind:
        return _ind.atr(df, n)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在日线上附加 MA20/50/90/200、RSI14、MACD、ATR14、实现波动率、历史收益与回撤。"""
    out = df.copy().reset_index(drop=True)
    c = out["close"]
    for n in (20, 50, 90, 200):
        out[f"ma{n}"] = c.rolling(n, min_periods=n).mean()
    out["rsi14"] = rsi(c, 14)
    m = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    out["atr14"] = atr(out, 14)
    lr = np.log(c).diff()
    for n in (20, 60, 90):
        out[f"rv{n}"] = lr.rolling(n, min_periods=n).std() * np.sqrt(365)
    for n in (7, 30, 90, 180, 365):
        out[f"ret_{n}d"] = c / c.shift(n) - 1
    peak = c.cummax()
    out["dd_from_ath"] = c / peak - 1
    out["dd_from_365d_high"] = c / c.rolling(365, min_periods=90).max() - 1
    return out
