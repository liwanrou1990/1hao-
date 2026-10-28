"""数据质量：缺口、重复、异常收益、覆盖年限。所有结论写入 data_status，不自动“修补”价格。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import TF_SECONDS


def continuity_report(df: pd.DataFrame, tf: str) -> dict:
    if df is None or df.empty:
        return {"n_bars": 0, "first": None, "last": None, "years": 0.0, "gaps": [], "n_gap_bars": 0, "duplicates": 0, "outliers": 0}
    sec = TF_SECONDS[tf]
    ts = df["ts"].sort_values()
    diffs = ts.diff().dt.total_seconds().dropna()
    gap_idx = diffs[diffs > sec * 1.5].index
    gaps = []
    for i in gap_idx:
        prev_ts = ts.loc[:i].iloc[-2]
        gaps.append({"after": prev_ts.isoformat(), "before": ts.loc[i].isoformat(), "missing_bars": int(round(diffs.loc[i] / sec)) - 1})
    r = np.log(df["close"]).diff()
    sd = r.std()
    outliers = int((r.abs() > 8 * sd).sum()) if sd and np.isfinite(sd) else 0
    years = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / (365.25 * 86400)
    return {"n_bars": int(len(df)), "first": ts.iloc[0].isoformat(), "last": ts.iloc[-1].isoformat(), "years": round(float(years), 2),
            "gaps": gaps[:50], "n_gap_bars": int(sum(g["missing_bars"] for g in gaps)), "duplicates": int(df["ts"].duplicated().sum()),
            "outliers": outliers, "sources": df["source"].value_counts().to_dict()}


def cross_check(bars_1d: pd.DataFrame, ref: pd.DataFrame) -> dict:
    """主源日收盘 vs 参考源日收盘：中位数/95 分位相对偏差。CoinGecko 日价为 UTC 0 点快照，与 K 线收盘略有差异。"""
    if bars_1d.empty or ref is None or ref.empty:
        return {"available": False}
    a = bars_1d[["ts", "close"]].copy()
    a["date"] = a["ts"].dt.floor("D") + pd.Timedelta(days=1)           # 收盘价对应次日 0 点快照
    m = a.merge(ref[["date", "close_ref"]], on="date", how="inner")
    if m.empty:
        return {"available": False}
    d = ((m["close"] - m["close_ref"]) / m["close_ref"]).abs()
    return {"available": True, "n": int(len(m)), "median_abs_pct": round(float(d.median() * 100), 3),
            "p95_abs_pct": round(float(d.quantile(0.95) * 100), 3), "max_abs_pct": round(float(d.max() * 100), 2),
            "ref_first": ref["date"].min().date().isoformat(), "ref_last": ref["date"].max().date().isoformat()}
