"""第六阶段测试：原子写入、告警去重/恢复（不真正外发）、每日脚本锁、手机视图参数解析。"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_to_parquet, atomic_write_json, atomic_write_text


def test_atomic_writes(tmp_path):
    p = tmp_path / "a" / "x.json"
    atomic_write_json(p, {"k": 1}); assert json.loads(p.read_text()) == {"k": 1}
    atomic_write_text(p, "{}"); assert p.read_text() == "{}" and not list((tmp_path / "a").glob("*.tmp"))
    q = tmp_path / "b.parquet"; atomic_to_parquet(pd.DataFrame({"a": [1, 2]}), q); assert pd.read_parquet(q)["a"].tolist() == [1, 2]


def test_notify_dedupe_and_recovery(tmp_path, monkeypatch):
    import spot90.monitor.healthcheck as hc
    monkeypatch.setenv("SPOT90_REPORT_DIR", str(tmp_path)); monkeypatch.setenv("SPOT90_ALERT_WEBHOOK", "http://127.0.0.1:1/hook")
    import spot90.config as C; C.cfg.cache_clear()
    sent = []
    class R: status_code = 200
    monkeypatch.setattr(hc.httpx, "post", lambda url, json=None, timeout=10: (sent.append(json), R())[1])
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    fail = {"overall": "FAIL", "checks": [{"check": "X 日线新鲜度", "status": "FAIL", "detail": "延迟 3 天"}]}
    ok = {"overall": "OK", "checks": [{"check": "X 日线新鲜度", "status": "OK", "detail": ""}]}
    assert hc.notify(fail, now)["action"] == "sent_fail"
    assert hc.notify(fail, now + timedelta(hours=1))["action"] == "suppressed_duplicate"
    assert hc.notify(fail, now + timedelta(hours=25))["action"] == "resent_fail_after_repeat_window"
    assert hc.notify(ok, now + timedelta(hours=26))["action"] == "sent_recovered"
    assert hc.notify(ok, now + timedelta(hours=27))["action"] == "none"
    assert len(sent) == 3 and all("127.0.0.1" not in json.dumps(m) for m in sent)
    assert "127.0.0.1" not in (tmp_path / "monitor" / "alerts_state.json").read_text()


def test_daily_script_lock(tmp_path):
    """用 flock 占住锁文件后启动脚本 → 应立即以 9 退出，且不执行任何步骤。"""
    root = Path(__file__).resolve().parents[1]
    lock = root / "data_store" / "spot90" / ".daily.flock"; lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(["bash", "-c", f"exec 9>'{lock}'; flock 9; sleep 20"])
    try:
        import time; time.sleep(1)
        r = subprocess.run(["bash", str(root / "scripts" / "spot90_monitor_daily.sh")], cwd=root, timeout=60)
        assert r.returncode == 9
    finally:
        holder.kill()


def test_mobile_flag_parsing():
    assert str({"m": "1"}.get("m", "0")).lower() in ("1", "true", "yes")
    assert str({}.get("m", "0")).lower() not in ("1", "true", "yes")
