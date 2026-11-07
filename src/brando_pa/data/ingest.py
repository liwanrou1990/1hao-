"""K 线采集：对每个 symbol × 周期（1w/1d/4h/1h/15m）拉取并 upsert；写 data_status.json（来源、范围、缺口、过期判断）。
绝不伪造数据：缺口与不足如实记录。1w 若下载失败则由 1d 合成（标注 resampled_from_1d）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from spot90.atomic import atomic_write_json
from spot90.data.quality import continuity_report as _spot90_continuity

from ..config import cfg, own_data_dir
from ..schema import resample_bars, tf_seconds
from .sources import StaticKlinesAdapter, bars_are_stale, make_adapter
from .store import BarStore

ALL_TFS = ["1w", "1d", "4h", "1h", "15m"]


def continuity_report(df: pd.DataFrame, tf: str) -> dict:
    """复用 spot90 的连续性报告（它的 TF_SECONDS 不含 15m/1w，这里按周期秒数自行计算）。"""
    if df is None or df.empty:
        return {"n_bars": 0, "first": None, "last": None, "years": 0.0, "gaps": [], "n_gap_bars": 0, "duplicates": 0, "outliers": 0}
    if tf in ("1d", "4h", "1h"):
        return _spot90_continuity(df, tf)
    sec = tf_seconds(tf)
    ts = df["ts"].sort_values()
    diffs = ts.diff().dt.total_seconds().dropna()
    gap_idx = diffs[diffs > sec * 1.5].index
    gaps = [{"after": ts.loc[:i].iloc[-2].isoformat(), "before": ts.loc[i].isoformat(), "missing_bars": int(round(diffs.loc[i] / sec)) - 1} for i in gap_idx]
    years = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / (365.25 * 86400)
    return {"n_bars": int(len(df)), "first": ts.iloc[0].isoformat(), "last": ts.iloc[-1].isoformat(), "years": round(float(years), 2),
            "gaps": gaps[:50], "n_gap_bars": int(sum(g["missing_bars"] for g in gaps)), "duplicates": int(df["ts"].duplicated().sum()),
            "outliers": 0, "sources": df["source"].value_counts().to_dict()}


def run_ingest(symbols: list[str] | None = None, tfs: list[str] | None = None, store: BarStore | None = None, adapter=None) -> dict:
    c = cfg()
    symbols = symbols or c["symbols"]
    tfs = tfs or ALL_TFS
    store = store or BarStore()
    adapter = adapter or make_adapter()
    status = {"run_ts": datetime.now(timezone.utc).isoformat(), "adapter": adapter.name, "probe": adapter.probe(), "symbols": {}}
    sk = adapter if isinstance(adapter, StaticKlinesAdapter) else StaticKlinesAdapter()
    for sym in symbols:
        status["symbols"][sym] = {}
        for tf in tfs:
            entry: dict = {}
            try:
                df = adapter.fetch_klines(sym, tf)
                if df.empty and adapter is not sk:
                    df = sk.fetch_klines(sym, tf); entry["fallback"] = "static_klines"
            except Exception as e:  # noqa: BLE001
                df = pd.DataFrame(); entry["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            if df.empty and tf == "1w":
                d1 = store.load(sym, "1d")
                if not d1.empty:
                    df = resample_bars(d1, "1d", "1w"); entry["fallback"] = "resampled_from_1d"
            if df.empty:
                existing = store.load(sym, tf)
                entry["result"] = "no_new_data_kept_existing" if not existing.empty else "no_data"
                entry["continuity"] = continuity_report(existing, tf)
            else:
                entry["upsert"] = store.upsert(df)
                entry["result"] = "updated"
                entry["continuity"] = continuity_report(store.load(sym, tf), tf)
            stale, why = bars_are_stale(store.load(sym, tf), tf)
            entry["stale"], entry["freshness"] = bool(stale), why
            status["symbols"][sym][tf] = entry
    atomic_write_json(own_data_dir() / "data_status.json", status, indent=2, default=str)
    return status


def load_status() -> dict | None:
    import json
    p = own_data_dir() / "data_status.json"
    return json.loads(p.read_text()) if p.exists() else None
