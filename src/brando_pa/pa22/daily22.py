"""每日任务（launchd 每 6 小时）：增量下载新发布的 OKX 归档 → 重新计算证据等级与验证 → 前瞻事件 → 页面。只读研究，不交易。"""
from __future__ import annotations

import json
import time

import pandas as pd

from ..config import own_data_dir
from ..trendsys.paper import _append
from . import DATA_SUBDIR, OUT_DIR
from . import archive22, dashboard22, phase22


def main() -> dict:
    dd = own_data_dir() / DATA_SUBDIR; t0 = time.time(); rec = {"kind": "daily_run", "at": str(pd.Timestamp.now(tz="UTC"))}
    try:
        rec["archive"] = archive22.build(dd / "okx_archive", workers=2, log=lambda m: None)
        r = phase22.run(dd); rec["current"] = {s: r["current"][s].get("forward_level") for s in r["current"]}; rec["verdict"] = r["verdict"]["verdict"]
        rec["dashboard"] = str(dashboard22.build()); rec["status"] = "ok"
    except Exception as e:  # noqa: BLE001
        rec |= {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    rec["seconds"] = round(time.time() - t0); OUT_DIR.mkdir(parents=True, exist_ok=True); _append(OUT_DIR / "daily_runs.jsonl", rec); return rec


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, default=str))
