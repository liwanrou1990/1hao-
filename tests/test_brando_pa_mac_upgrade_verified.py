"""Mac 门控升级脚本（2026-11-08）：已最新 → 不升级；有待应用提交 → dry-run 通过门控后正式升级，HEAD 到 origin，三账户资金/持仓/手续费/已实现不变，备份存在；
dry-run 门控失败（本地修改的非数据文件）→ 立即停止、HEAD 不变、不执行升级。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_brando_pa_upgrade import _git, _make_repo_with_package, _push_v2

ROOT = Path(__file__).resolve().parents[1]


def _prep(tmp_path):
    origin, work = _make_repo_with_package(tmp_path)
    shutil.copy2(ROOT / "scripts" / "brando_pa_mac_upgrade_verified.sh", work / "scripts" / "brando_pa_mac_upgrade_verified.sh")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "add gated script"); _git(work, "push", "-q", "origin", "main")
    env = dict(os.environ, BRANDO_NO_LAUNCHD="1", BRANDO_SKIP_PIP="1", BRANDO_PY=sys.executable, BRANDO_SKIP_POSTCHECK="1", BRANDO_REPO=str(work))
    return origin, work, env


def _run(work, env):
    return subprocess.run(["bash", str(work / "scripts" / "brando_pa_mac_upgrade_verified.sh"), "main"], cwd="/", env=env, capture_output=True, text=True, timeout=600)


def _cost_state(work):
    out = {}
    for a in sorted(p for p in (work / "reports_out" / "brando_pa" / "phase8_forward").iterdir() if p.is_dir() and (p / "state.json").exists()):
        st = json.loads((a / "state.json").read_text()); out[a.name] = {k: (round(v["cash"], 8), {s: round(sum(l["qty"] for l in ls), 10) for s, ls in v["lots"].items()}, round(v["fees_paid"], 8), round(v["realized"], 8)) for k, v in st["ledgers"].items()} | {"initial": st["initial_usdt"]}
    return out


def test_up_to_date_no_upgrade_then_gated_upgrade_applies_and_keeps_accounts(tmp_path):
    origin, work, env = _prep(tmp_path); head0 = _git(work, "rev-parse", "HEAD"); acc0 = _cost_state(work)
    r = _run(work, env)
    assert r.returncode == 0 and "already at origin/main" in r.stdout and "upgraded=0" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    assert _git(work, "rev-parse", "HEAD") == head0 and not list((work / "reports_out" / "brando_pa" / "phase8_forward").glob("upgrade_report_*.json"))
    _push_v2(tmp_path / "my repo dir", origin); (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").write_bytes(b"local bars")
    r = _run(work, env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-800:]
    assert "dry-run PASSED" in r.stdout and "upgraded=1" in r.stdout and "accounts_identical=YES" in r.stdout
    assert _git(work, "rev-parse", "HEAD") == _git(work, "rev-parse", "origin/main") != head0
    assert _cost_state(work) == acc0 and (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").read_bytes() == b"local bars"
    assert len(list((work / "backups").glob("*.tgz"))) >= 2 and len(list((work / "reports_out" / "brando_pa" / "phase8_forward").glob("upgrade_report_*.json"))) == 2   # dry-run + upgrade


def test_gate_failure_stops_before_upgrade(tmp_path):
    origin, work, env = _prep(tmp_path)
    _push_v2(tmp_path / "my repo dir", origin)
    (work / "src.txt").write_text("locally edited code\n")                     # 本地修改的非数据文件 → dry-run 门控必须拒绝
    head0 = _git(work, "rev-parse", "HEAD"); acc0 = _cost_state(work)
    r = _run(work, env)
    assert r.returncode == 1 and "[gate] STOP" in r.stdout and "upgraded=1" not in r.stdout, r.stdout[-2000:]
    assert _git(work, "rev-parse", "HEAD") == head0 and _cost_state(work) == acc0
    assert len(list((work / "reports_out" / "brando_pa" / "phase8_forward").glob("upgrade_report_*.json"))) == 1     # 只有 dry-run 报告，没有升级报告


def test_protected_file_in_pending_commits_is_refused(tmp_path):
    origin, work, env = _prep(tmp_path)
    other = tmp_path / "other2"; subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "t@t"); _git(other, "config", "user.name", "t")
    (other / "config").mkdir(exist_ok=True); (other / "config" / "brando_pa.yaml").write_text("execution:\n  live_trading: false\nusdt:\n  trend:\n    sma: 150\n")
    _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "touch config"); _git(other, "push", "-q", "origin", "main")
    head0 = _git(work, "rev-parse", "HEAD"); r = _run(work, env)
    assert r.returncode == 1 and "touch strategy/config/ledger/account files" in r.stdout and _git(work, "rev-parse", "HEAD") == head0
    assert not list((work / "reports_out" / "brando_pa" / "phase8_forward").glob("upgrade_report_*.json"))              # 连 dry-run 都没跑
