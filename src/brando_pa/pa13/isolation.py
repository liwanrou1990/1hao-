"""隔离检查：影子目录 / 行情仓 / 锁与生产（Mac 三账户）目录完全分离；一轮运行前后生产目录哈希不变；真实交易禁用；launchd 标签不同。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from ..config import cfg, live_trading_allowed
from .shadow import shadow_dirs

PROD_LABEL = "com.brando-pa.forward"; SHADOW_LABEL = "com.brando-pa.shadow13"


def tree_hash(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


def isolation_check(log_dir: Path | None = None, run_cycle: bool = False, sources=None, clock=None) -> dict:
    c = cfg(); d = shadow_dirs(c); log_dir = Path(log_dir or d["log_dir"]); prod = d["production_dir"]
    out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "shadow_log_dir": str(log_dir), "shadow_bars_root": str(d["bars_root"]), "shadow_feed_dir": str(d["feed_dir"]), "production_dir": str(prod),
           "paths_disjoint": not (str(log_dir).startswith(str(prod)) or str(prod).startswith(str(log_dir))) and str(d["bars_root"]) != str(Path(c["data"]["own_dir"]).resolve() / "forward_bars"),
           "locks_distinct": str(log_dir / "service.lock") != str(prod / "service.lock"), "launchd_labels": {"production": PROD_LABEL, "shadow": SHADOW_LABEL, "distinct": PROD_LABEL != SHADOW_LABEL},
           "live_trading_allowed": bool(live_trading_allowed(c)), "live_confirm_env_set": "BRANDO_LIVE_CONFIRM" in os.environ, "production_hash_unchanged": None, "ok": False}
    before = tree_hash(prod)
    if run_cycle:
        from .shadow import build_service
        svc = build_service(c, log_dir=log_dir, sources=sources, clock=clock); svc.cycle(); out["cycle_ran"] = True
    out["production_hash_unchanged"] = tree_hash(prod) == before; out["production_files"] = len(before)
    out["ok"] = bool(out["paths_disjoint"] and out["locks_distinct"] and out["launchd_labels"]["distinct"] and not out["live_trading_allowed"] and not out["live_confirm_env_set"] and out["production_hash_unchanged"])
    return out
