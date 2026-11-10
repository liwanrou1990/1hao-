"""第二十一阶段独立观察服务的单次运行（launchd 每 3600 秒调用一次；不是常驻进程）。
锁 → 冻结账本更新（forward21）→ 漂移与观察记录（monitor21）→ 只读页面 → 运行记录（哈希链）与心跳。任何异常都被记录，不会下单。
用法：PYTHONPATH=src python3 -m brando_pa.pa21.service21 {tick|health}"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
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
    rec = {"kind": "tick", "tick_at": str(now), "pid": os.getpid(), "host": socket.gethostname(), "platform": sys.platform}; lock = open(out / "service.lock", "a+")
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


def verify(out_dir: Path | None = None, now: pd.Timestamp | None = None) -> dict:
    """部署后核对：心跳、冻结规则、六条哈希链、行情（来源 / 最新已收盘日 / 是否过期）、账本（起点 / 天数 / 不重复 / 四账户本金）、只读页面。"""
    from ..trendsys.paper import verify_chain
    from . import ACCOUNTS, FORWARD_START, RESEARCH_INITIAL_USDT, freeze21
    out = Path(out_dir or OUT_DIR); now = now or utcnow(); C = {}
    h = health(out, now); C["heartbeat"] = {"ok": bool(h.get("ok")), "detail": f'最近心跳 {h.get("heartbeat_utc")}（{h.get("heartbeat_age_s")} 秒前），最近状态 {h.get("last_status")}，连续失败 {h.get("consecutive_failures")}'}
    fz = freeze21.verify(); C["freeze"] = {"ok": fz["ok"], "detail": f'冻结参数指纹 {str(fz.get("params_sha256"))[:12]}；改动文件 {fz.get("changed_files") or "无"}'}
    ch = {k: verify_chain(out / f"{k}.jsonl") for k in ("daily_ledger", "alerts", "events", "runs", "observations", "drift")}; C["hash_chains"] = {"ok": all(v["ok"] for v in ch.values()), "detail": ", ".join(f"{k} {v['n']} 行{'' if v['ok'] else ' 损坏'}" for k, v in ch.items())}
    st = json.loads((out / "state.json").read_text(encoding="utf-8")) if (out / "state.json").exists() else {}; fd = st.get("feed", {})
    lag = (now - pd.Timestamp(st["latest_closed_day"], tz="UTC") - pd.Timedelta(days=1)).total_seconds() / 3600 if st.get("latest_closed_day") else None
    C["market_data"] = {"ok": bool(fd.get("ok")) and not fd.get("quality_fault") and lag is not None and lag <= 30, "detail": f'来源 {fd.get("source")}，最新已收盘日 {st.get("latest_closed_day")}（收盘后 {None if lag is None else round(lag, 1)} 小时），错误 {fd.get("errors") or "无"}'}
    rows = [json.loads(x) for x in (out / "daily_ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (out / "daily_ledger.jsonl").exists() else []
    dates = [r["date"] for r in rows]; first_ok = not rows or (dates[0] == FORWARD_START and all(abs(rows[0]["accounts"][a]["liquidation"] - RESEARCH_INITIAL_USDT) < 1e-6 for a in ACCOUNTS))
    C["ledger"] = {"ok": st.get("status") in ("observing", "awaiting_first_close") and len(set(dates)) == len(dates) and first_ok and st.get("initial_usdt_virtual") == RESEARCH_INITIAL_USDT and st.get("auto_trading") is False,
                   "detail": f'状态 {st.get("status")}；观察起点 {FORWARD_START}；已记录 {len(rows)} 天' + (f'（{dates[0]} → {dates[-1]}）' if rows else "（起点日线尚未收盘时为 0）") + f'；四账户虚拟本金 {st.get("initial_usdt_virtual")}；自动交易 {st.get("auto_trading")}'}
    dp = out / "dashboard21_forward_center.html"; age = (now.timestamp() - dp.stat().st_mtime) / 3600 if dp.exists() else None
    C["dashboard"] = {"ok": dp.exists() and age is not None and age <= 2.5 and "不是真实资产" in dp.read_text(encoding="utf-8"), "detail": f"{dp}（{None if age is None else round(age, 2)} 小时前生成）"}
    return {"ok": all(v["ok"] for v in C.values()), "checks": C}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        r = tick(); print(json.dumps({k: r.get(k) for k in ("tick_at", "status", "forward_status", "latest_closed_day", "n_days", "error")}, ensure_ascii=False))
    elif cmd == "verify":
        r = verify()
        for k, v in r["checks"].items():
            print(("PASS  " if v["ok"] else "FAIL  ") + k + "：" + v["detail"])
        print("VERIFY_OK" if r["ok"] else "VERIFY_FAILED"); sys.exit(0 if r["ok"] else 1)
    elif cmd == "health":
        r = health(); print(json.dumps(r, ensure_ascii=False, indent=1, default=str)); sys.exit(0 if r.get("ok") else 1)
