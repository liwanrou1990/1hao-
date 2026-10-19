"""数据质量标记与校验。"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..calendar_utils import NY, is_trading_day, regular_minute_index
from .schema import QualityFlag, Venue


def apply_quality_flags(bars: pd.DataFrame, expected_freq_min: int = 1, outlier_z: float = 8.0) -> pd.DataFrame:
    """补充质量位：缺口、重复、异常收益、零成交量、未完成K线、非交易日原生K线。"""
    if bars.empty:
        return bars
    df = bars.copy()
    df["quality"] = df["quality"].fillna(0).astype(int)
    dup = df.duplicated(subset=["symbol", "venue", "ts"], keep="last")
    if dup.any():
        df.loc[dup, "quality"] |= int(QualityFlag.DUPLICATE)
        df = df[~dup]
    df = df.sort_values("ts").reset_index(drop=True)
    gap = df["ts"].diff() > pd.Timedelta(minutes=expected_freq_min)
    df.loc[gap, "quality"] |= int(QualityFlag.GAP_BEFORE)
    ret = np.log(df["close"]).diff()
    sd = ret.std(ddof=1)
    if sd and np.isfinite(sd) and sd > 0:
        df.loc[(ret.abs() > outlier_z * sd), "quality"] |= int(QualityFlag.OUTLIER_RETURN)
    if "volume" in df:
        df.loc[(df["volume"].fillna(0) <= 0) & df["volume"].notna(), "quality"] |= int(QualityFlag.ZERO_VOLUME)
    df.loc[~df["is_complete"].astype(bool), "quality"] |= int(QualityFlag.INCOMPLETE)
    native = df["venue"] == Venue.NATIVE
    if native.any():
        ny_dates = df.loc[native, "ts"].dt.tz_convert(NY).dt.date
        bad = ~ny_dates.map(is_trading_day)
        df.loc[bad[bad].index, "quality"] |= int(QualityFlag.NON_TRADING_DAY)
    return df


def research_ready(bars: pd.DataFrame) -> pd.DataFrame:
    """研究用数据：剔除未完成K线、非交易日原生K线。"""
    if bars.empty:
        return bars
    q = bars["quality"].astype(int)
    mask = (q & int(QualityFlag.INCOMPLETE) == 0) & (q & int(QualityFlag.NON_TRADING_DAY) == 0)
    return bars[mask].reset_index(drop=True)


def coverage_report(bars: pd.DataFrame, day: date) -> dict:
    expected = len(regular_minute_index(day)) if is_trading_day(day) else 0
    q = bars["quality"].astype(int) if not bars.empty else pd.Series(dtype=int)
    return {
        "day": day.isoformat(),
        "is_trading_day": is_trading_day(day),
        "expected_bars": expected,
        "actual_bars": int(len(bars)),
        "coverage": round(len(bars) / expected, 4) if expected else None,
        "gaps": int((q & int(QualityFlag.GAP_BEFORE) > 0).sum()) if len(q) else 0,
        "outliers": int((q & int(QualityFlag.OUTLIER_RETURN) > 0).sum()) if len(q) else 0,
        "simulated": bool((q & int(QualityFlag.SIMULATED) > 0).any()) if len(q) else False,
        "sources": sorted(bars["source"].unique().tolist()) if not bars.empty else [],
    }


def describe_flags(q: int) -> list[str]:
    return [f.name for f in QualityFlag if f != QualityFlag.OK and q & int(f)]
