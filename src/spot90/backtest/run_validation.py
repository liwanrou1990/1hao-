"""一键验证：读取 walk-forward 产物 → 泄漏审计 → BTC/ETH 分别验证 → JSON + 信号 CSV + Markdown 表格。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..config import cfg, report_dir
from ..data.store import BarStore
from .audit import run_audit
from .dataset import build_dataset, load_bars
from .validation import per_symbol, signal_table
from .walkforward import MODEL_NAMES


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def main() -> dict:
    bt = report_dir() / "backtest"
    summary = json.loads((bt / "summary.json").read_text())
    store = BarStore()
    bars = load_bars(store)
    ds = build_dataset(bars)
    predA, predB, signals = {}, {}, {}
    for m in MODEL_NAMES:
        if (bt / f"predA_{m}.parquet").exists():
            predA[m] = pd.read_parquet(bt / f"predA_{m}.parquet"); predB[m] = pd.read_parquet(bt / f"predB_{m}.parquet")
            signals[m] = pd.read_parquet(bt / f"signals_{m}.parquet")
            predA[m]["ts"] = pd.to_datetime(predA[m]["ts"], utc=True); predB[m]["ts"] = pd.to_datetime(predB[m]["ts"], utc=True); signals[m]["ts"] = pd.to_datetime(signals[m]["ts"], utc=True)
            # parquet 读回后索引丢失 → 用 (ts, symbol[, d_pct]) 对齐回数据集索引
            ka = ds["wide"].reset_index().set_index(["ts", "symbol"])["index"]
            predA[m].index = ka.loc[list(zip(predA[m]["ts"], predA[m]["symbol"]))].to_numpy()
            signals[m].index = ka.loc[list(zip(signals[m]["ts"], signals[m]["symbol"]))].to_numpy()
            kb = ds["long"].reset_index().set_index(["ts", "symbol", "d_pct"])["index"]
            predB[m].index = kb.loc[list(zip(predB[m]["ts"], predB[m]["symbol"], predB[m]["d_pct"]))].to_numpy()
    thr = float(cfg()["thresholds"]["drawdown_threshold"])
    models = list(predA)
    out_dir = report_dir() / "validation"; out_dir.mkdir(parents=True, exist_ok=True)
    audit = run_audit(bars, summary["folds"], store)
    res = {"run_ts": datetime.now(timezone.utc).isoformat(), "selection": summary["selection"], "audit": audit, "symbols": {}}
    for sym in cfg()["symbols"]:
        res["symbols"][sym] = per_symbol(sym, ds, predA, predB, signals, models, thr)
        for m in models:
            signal_table(sym, signals[m], predA[m], predB[m]).to_csv(out_dir / f"{sym}_signals_{m}.csv", index=False)
    res = _clean(res)
    (out_dir / "validation.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps({"all_audits_passed": r["audit"]["all_passed"], "selection": r["selection"]}, ensure_ascii=False, indent=2))
