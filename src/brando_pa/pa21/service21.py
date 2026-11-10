"""第二十一阶段独立观察服务的单次运行（launchd 每 3600 秒调用一次；不是常驻进程）。
锁 → 冻结账本更新（forward21）→ 漂移与观察记录（monitor21）→ 只读页面 → 运行记录（哈希链）与心跳。任何异常都被记录，不会下单。
用法：PYTHONPATH=src python3 -m brando_pa.pa21.service21 {tick|health}"""
from __future__ import annotations

import fcntl
import json
import os
import time
import traceback
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg, own_data_dir
from ..data.sources import utcnow
from ..trendsys.paper import _append
from . import FEED_SUBDIR, OUT_DIR
from . import forward21, monitor21

INTERVAL_S = 3600


def tick(now: pd.Timestamp | None = None, store=None, out_dir: Path | None = None, feed_dir: Path | None = None, sources: list | None = None, fetch: bool = True, dashboard: bool = True, c: dict | None = None) -> dict:
    c = c or cfg(); now = now or utcnow(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); t0 = time.time(); store = store or forward21.default_store(c)
    rec = {"kind": "tick", "tick_at": str(now), "pid": os.getpid()}; lock = open(out / "service.lock", "a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            rec |= {"status": "skipped_locked", "note": "上一轮仍在运行"}; _append(out / "runs.jsonl", rec); return rec
        try:
            st = forward21.run_once(c=c, now=now, store=store, out_dir=out, fetch=fetch, feed_dir=feed_dir or own_data_dir(c) / FEED_SUBDIR, sources=sources)
            mon = monitor21.update(now, store, out)
            if dashboard:
                from . import dashboard21
                dashboard21.build(out_dir=out)
            feed = st.get("feed", {})
            status = "ok" if st["status"] in ("observing", "awaiting_first_close") and feed.get("ok", True) and not feed.get("quality_fault") else ("data_unavailable" if not feed.get("ok", True) else st["status"])
            rec |= {"status": status, "forward_status": st["status"], "latest_closed_day": st.get("latest_closed_day"), "n_days": st.get("n_days"), "added_days": st.get("added_days"), "feed_source": feed.get("source"),
                    "feed_errors": feed.get("errors"), "freeze_ok": st.get("freeze", {}).get("ok"), "new_observations": mon["new_observations"], "fix25_btc_weight": mon["fix25_btc_weight"]}
        except Exception as e:  # noqa: BLE001
            rec |= {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}", "trace_tail": traceback.format_exc()[-800:]}
        rec["elapsed_s"] = round(time.time() - t0, 2); _append(out / "runs.jsonl", rec)
        h = json.loads((out / "health.json").read_text(encoding="utf-8")) if (out / "health.json").exists() else {"n_ticks": 0, "consecutive_failures": 0}
        ok = rec["status"] == "ok"
        h |= {"heartbeat_utc": str(now), "interval_s": INTERVAL_S, "n_ticks": h.get("n_ticks", 0) + 1, "last_status": rec["status"], "consecutive_failures": 0 if ok else h.get("consecutive_failures", 0) + 1, "last_ok_tick_at": str(now) if ok else h.get("last_ok_tick_at"),
              "auto_trading": False, "real_trading": "disabled (no implementation)"}
        atomic_write_json(out / "health.json", h, indent=1, default=str); return rec
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def health(out_dir: Path | None = None, now: pd.Timestamp | None = None) -> dict:
    out = Path(out_dir or OUT_DIR); p = out / "health.json"
    if not p.exists():
        return {"ok": False, "reason": "尚无心跳"}
    h = json.loads(p.read_text(encoding="utf-8")); age = ((now or utcnow()) - pd.Timestamp(h["heartbeat_utc"])).total_seconds()
    return h | {"heartbeat_age_s": round(age), "ok": age < 2 * INTERVAL_S + 600 and h.get("consecutive_failures", 0) < 3}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        r = tick(); print(json.dumps({k: r.get(k) for k in ("tick_at", "status", "forward_status", "latest_closed_day", "n_days", "error")}, ensure_ascii=False))
    elif cmd == "health":
        r = health(); print(json.dumps(r, ensure_ascii=False, indent=1, default=str)); sys.exit(0 if r.get("ok") else 1)
