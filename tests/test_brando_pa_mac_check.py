"""Mac 一键只读检查脚本（2026-11-08）：在非 macOS 上也能完整跑完并生成报告；不改 HEAD、不改账户文件、不停服务；bash 3.2 规则由 test_brando_pa_upgrade 的静态扫描覆盖。"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REAL = Path("reports_out/brando_pa/phase8_forward"); ROOT = Path(__file__).resolve().parents[1]


def _acct_hashes() -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for a in REAL.iterdir() if a.is_dir() and (a / "events.jsonl").exists() for p in a.iterdir() if p.is_file()}


def test_mac_check_runs_read_only_and_writes_bundle(tmp_path):
    if not (REAL / "A_BTC_SMA200" / "events.jsonl").exists():
        pytest.skip("no forward records")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(); before = _acct_hashes()
    out_dir = ROOT / "reports_out" / "brando_pa" / "mac_check"; n0 = len(list(out_dir.glob("mac_check_*.md"))) if out_dir.exists() else 0
    env = dict(os.environ, BRANDO_REPO=str(ROOT), BRANDO_PY=sys.executable, BRANDO_LOCAL_START="2026-11-08 12:23:00")
    r = subprocess.run(["bash", str(ROOT / "scripts" / "brando_pa_mac_check.sh")], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=600)
    assert "ENVIRONMENT" in r.stdout and "LEDGER AUDIT" in r.stdout and "SUMMARY" in r.stdout and "report=" in r.stdout, r.stdout[-2000:] + r.stderr[-500:]
    assert r.returncode in (0, 1)                                                      # 1 = 有待关注项（云容器内无服务），但脚本本身完整跑完
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() == head
    assert _acct_hashes() == before                                                    # 账户文件未变
    assert len(list(out_dir.glob("mac_check_*.md"))) == n0 + 1 and not (ROOT / ".mac_check_worktree").exists()
    md = sorted(out_dir.glob("mac_check_*.md"))[-1].read_text(encoding="utf-8")
    assert "applied: ledger_audit=yes F1/F3_reports=yes" in md and "## ledger audit" in md and "Real trading disabled" in md
    if sys.platform != "darwin":
        assert "NOT a Mac result" in r.stdout
