"""数据采集编排：Bitget（首选）→ static-klines（备用）→ 本地已有数据；DefiLlama 交叉校验。
每次运行写 data_status.json（来源、范围、年限、缺口、拼接、校验），绝不用模拟数据填充。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import cfg, data_dir
from .bitget_v3 import BitgetSpotHistoryClient, BitgetUnreachable
from .llama import LlamaClient
from .quality import continuity_report, cross_check
from .static_klines import StaticKlinesClient
from .store import BarStore


def _splice(primary: pd.DataFrame, backfill: pd.DataFrame, tol_pct: float) -> tuple[pd.DataFrame, dict]:
    """主源覆盖期以主源为准；主源起点之前用备用源回填。重叠期校验中位数价差。"""
    if primary.empty:
        return backfill, {"mode": "backfill_only"}
    if backfill.empty:
        return primary, {"mode": "primary_only"}
    first = primary["ts"].min()
    overlap = backfill[backfill["ts"] >= first].merge(primary[["ts", "close"]], on="ts", suffixes=("_b", "_p"))
    info: dict = {"mode": "spliced", "primary_first": first.isoformat(), "overlap_bars": int(len(overlap))}
    if len(overlap) >= 10:
        dev = ((overlap["close_b"] - overlap["close_p"]) / overlap["close_p"]).abs().median() * 100
        info["overlap_median_abs_pct"] = round(float(dev), 3)
        if dev > tol_pct:
            info["mode"] = "primary_only_splice_rejected"
            return primary, info
    older = backfill[backfill["ts"] < first]
    info["backfilled_bars"] = int(len(older))
    return pd.concat([older, primary], ignore_index=True).sort_values("ts").reset_index(drop=True), info


def run_ingest(symbols: list[str] | None = None, timeframes: list[str] | None = None, source: str | None = None,
               skip_crosscheck: bool = False, store: BarStore | None = None, bitget_client=None, sk_client=None, llama_client=None) -> dict:
    c = cfg()
    symbols = symbols or c["symbols"]
    timeframes = timeframes or c["timeframes"]
    source = source or c["data"]["primary_source"]
    store = store or BarStore()
    status: dict = {"run_ts": datetime.now(timezone.utc).isoformat(), "primary_source_requested": source, "symbols": {}, "probes": {}}

    bitget = bitget_client or BitgetSpotHistoryClient()
    status["probes"]["bitget"] = bitget.probe()
    sk = sk_client or StaticKlinesClient()
    status["probes"]["static_klines"] = sk.probe()
    status["fallback_used"] = bool(source == "bitget" and not status["probes"]["bitget"]["ok"] and status["probes"]["static_klines"]["ok"])
    tol = float(c["data"].get("splice_tolerance_pct", 0.8))
    years_req = float(c.get("history_years_required", 5))

    for sym in symbols:
        status["symbols"][sym] = {}
        for tf in timeframes:
            entry: dict = {"attempts": []}
            primary = pd.DataFrame()
            if source == "bitget" and status["probes"]["bitget"]["ok"]:
                try:
                    primary = bitget.fetch(sym, tf)
                    entry["attempts"].append({"source": "bitget", "bars": int(len(primary)), "api": bitget.log.api_version,
                                              "interval": bitget.log.interval_used, "errors": bitget.log.errors[-5:]})
                except BitgetUnreachable as e:
                    entry["attempts"].append({"source": "bitget", "bars": 0, "errors": [str(e)]})
            elif source == "bitget":
                entry["attempts"].append({"source": "bitget", "bars": 0, "errors": [status["probes"]["bitget"]["detail"]]})
            backfill = pd.DataFrame()
            need_backfill = primary.empty or (datetime.now(timezone.utc) - primary["ts"].min().to_pydatetime()).days < years_req * 365.25
            if need_backfill and status["probes"]["static_klines"]["ok"]:
                backfill = sk.fetch(sym, tf)
                entry["attempts"].append({"source": "static_klines", "bars": int(len(backfill)), "errors": sk.errors[-5:]})
            merged, splice = _splice(primary, backfill, tol)
            entry["splice"] = splice
            if merged.empty:
                existing = store.load(sym, tf)
                entry["result"] = "no_new_data_kept_existing" if not existing.empty else "no_data"
                merged_all = existing
            else:
                up = store.upsert(merged)
                entry["result"] = "updated"
                entry["upsert"] = up
                merged_all = store.load(sym, tf)
            rep = continuity_report(merged_all, tf)
            entry["continuity"] = rep
            entry["meets_history_requirement"] = bool(rep["years"] >= years_req)
            entry["effective_source"] = (max(rep["sources"], key=rep["sources"].get) if rep.get("sources") else None)
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            if tf == "1d" and not skip_crosscheck and not merged_all.empty:
                try:
                    ref = (llama_client or LlamaClient()).daily_close(sym)
                    entry["cross_check"] = cross_check(merged_all, ref)
                    if not ref.empty:
                        ref.to_parquet(store.root / "bars" / f"{sym}_ref_daily_close.parquet", index=False)
                except Exception as e:  # noqa: BLE001
                    entry["cross_check"] = {"available": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            status["symbols"][sym][tf] = entry
    out = data_dir() / "data_status.json"
    out.write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str))
    store.save_json("data_status", run_ts=datetime.now(timezone.utc), payload=status)
    return status


def load_status() -> dict | None:
    p = data_dir() / "data_status.json"
    return json.loads(p.read_text()) if p.exists() else None
