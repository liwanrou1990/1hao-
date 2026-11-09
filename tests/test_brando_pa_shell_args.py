"""Shell 参数传递回归（2026-11-09，Mac 真实报错：/tmp/brando_upgrade_verified.sh 第 122 行 `BRANDO_LOCAL_START=2026-11-08 12:23:00: command not found`）。
根因：`${LS:+BRANDO_LOCAL_START="${LS}"}` 由参数展开生成的“赋值”不是赋值——shell 只在展开前识别 VAR=value 前缀，展开结果（含空格的整词）被当作命令名。
规则：含空格的 UTC 时间 / 路径只能通过 ① 直接写死的 VAR="${X}" 前缀、② 显式 if/else 分支传参；禁止 ${X:+VAR=...}。本文件覆盖：静态扫描、根因复现、ctl / 一键检查 / 门控脚本在含空格路径 + 含空格时间下的真实运行。"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_brando_pa_upgrade import MAC_SCRIPTS, _git, _make_repo_with_package

ROOT = Path(__file__).resolve().parents[1]
LS = "2026-11-08 12:23:00"


def test_no_env_assignment_built_from_parameter_expansion():
    bad = []
    for f in MAC_SCRIPTS:
        for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*:\+[A-Za-z_][A-Za-z0-9_]*=", line):
                bad.append(f"{f}:{i}")
    assert not bad, bad


def test_root_cause_reproduction_and_fixed_form():
    r = subprocess.run(["bash", "-c", 'LS="2026-11-08 12:23:00"; ${LS:+X="${LS}"} true'], capture_output=True, text=True)
    assert r.returncode == 127 and "command not found" in r.stderr                       # 错误写法：展开出的赋值变成命令名
    r = subprocess.run(["bash", "-c", 'LS="2026-11-08 12:23:00"; X="${LS}" bash -c \'printf %s "${X}"\''], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == LS                                            # 正确写法：直接前缀赋值，空格完整保留
    r = subprocess.run(["bash", "-c", 'LS=""; X="${LS}" bash -c \'[ -z "${X:-}" ] && echo empty\''], capture_output=True, text=True)
    assert r.stdout.strip() == "empty"                                                     # 空值 = 被调方视为未设置


def test_ctl_acceptance_and_audit_accept_space_separated_utc_time():
    env = dict(os.environ, BRANDO_PY=sys.executable)
    r = subprocess.run(["bash", "scripts/brando_pa_forward_ctl.sh", "acceptance", LS], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "command not found" not in r.stderr, r.stdout[-800:] + r.stderr[-400:]
    assert json.loads((ROOT / "reports_out/brando_pa/phase8_forward/acceptance_status.json").read_text())["local_start"] == LS
    r = subprocess.run(["bash", "scripts/brando_pa_forward_ctl.sh", "audit", LS], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode in (0, 1) and "command not found" not in r.stderr and "## 5. 72" in r.stdout
    latest = sorted((ROOT / "reports_out/brando_pa/forward_audit").glob("ledger_audit_*.json"))[-1]
    assert json.loads(latest.read_text())["acceptance"]["local_start"] == LS


def _fake_repo_with_mac_scripts(tmp_path):
    origin, work = _make_repo_with_package(tmp_path)                                       # 路径含空格："my repo dir"
    for f in ("scripts/brando_pa_mac_check.sh", "scripts/brando_pa_mac_upgrade_verified.sh", "scripts/brando_pa_forward_ctl.sh"):
        shutil.copy2(ROOT / f, work / f)
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "mac scripts"); _git(work, "push", "-q", "origin", "main")
    return origin, work


def test_mac_check_with_space_in_repo_path_and_local_start(tmp_path):
    origin, work = _fake_repo_with_mac_scripts(tmp_path)
    env = dict(os.environ, BRANDO_REPO=str(work), BRANDO_PY=sys.executable, BRANDO_LOCAL_START=LS)
    r = subprocess.run(["bash", str(work / "scripts" / "brando_pa_mac_check.sh"), "main"], cwd="/", env=env, capture_output=True, text=True, timeout=600)
    assert "command not found" not in (r.stdout + r.stderr) and "SUMMARY" in r.stdout and "report=" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    la = sorted((work / "reports_out/brando_pa/mac_check").glob("ledger_audit_*.json"))[-1]
    assert json.loads(la.read_text())["acceptance"]["local_start"] == LS                 # 含空格的时间完整到达 Python


def test_gated_upgrade_post_verify_passes_local_start_with_space(tmp_path):
    origin, work = _fake_repo_with_mac_scripts(tmp_path)
    env = dict(os.environ, BRANDO_REPO=str(work), BRANDO_PY=sys.executable, BRANDO_LOCAL_START=LS, BRANDO_NO_LAUNCHD="1", BRANDO_SKIP_PIP="1", BRANDO_HEALTH_WAIT_S="0")
    env.pop("BRANDO_SKIP_POSTCHECK", None)                                                 # 走真实的第 4 步（出错的那一段）
    r = subprocess.run(["bash", str(work / "scripts" / "brando_pa_mac_upgrade_verified.sh"), "main"], cwd="/", env=env, capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    assert "command not found" not in out, out[-2000:]
    assert "accounts_identical=YES" in r.stdout and "overall=" in r.stdout, r.stdout[-2000:]   # 第 4 步跑到最后一行（一键检查 bundle）
    la = sorted((work / "reports_out/brando_pa/mac_check").glob("ledger_audit_*.json"))[-1]
    assert json.loads(la.read_text())["acceptance"]["local_start"] == LS
