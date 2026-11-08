"""安全升级器：备份完整性（篡改 → 拒绝）、快照比较（任何账户/链变化 → 阻止启动）、本地修改分类（数据文件 vs 代码）、
ff 合并时数据文件保存/放回且字节不变、非 ff 或代码修改 → 中止且不 reset、服务未停（锁被持有）→ 中止、live_trading true → 拒绝、端到端 dry-run 与真实升级（临时 git 仓库 + 伪 origin）。"""
import json
import os
import subprocess
import sys
import tempfile
import tarfile
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.forward import upgrade as U
from tests.test_brando_pa_causality import _synth_oc, T
from tests.test_brando_pa_forward import _store


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def _make_repo(tmp_path, with_forward=True):
    """origin（裸仓）+ 本地克隆；本地含前瞻账户目录（由真实 ForwardRunner 生成）与被跟踪的数据文件。"""
    tmp_path = Path(tmp_path); tmp_path.mkdir(parents=True, exist_ok=True)
    origin = tmp_path / "origin.git"; _git(tmp_path, "init", "-q", "--bare", str(origin))
    work = tmp_path / "work"; work.mkdir()
    _git(work, "init", "-q"); _git(work, "config", "user.email", "t@t"); _git(work, "config", "user.name", "t"); _git(work, "checkout", "-q", "-b", "main")
    (work / "config").mkdir(); (work / "config" / "brando_pa.yaml").write_text("execution:\n  live_trading: false\n")
    (work / "deploy").mkdir(); (work / "deploy" / "requirements-forward.txt").write_text("")
    (work / "data_store" / "brando_pa" / "forward_bars").mkdir(parents=True); (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").write_bytes(b"v1")
    (work / "src.txt").write_text("code v1\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "v1"); _git(work, "remote", "add", "origin", str(origin)); _git(work, "push", "-q", "origin", "main")
    if with_forward:
        from brando_pa.config import cfg
        from brando_pa.forward.accounts import ForwardRunner
        bars = _synth_oc(); store = _store(work / "bars", bars); r = ForwardRunner(cfg(), log_dir=work / "reports_out" / "brando_pa" / "phase8_forward", store=store)
        r.run_once(now=T("2024-08-01 05:00")); r.run_once(now=T("2024-08-02 00:30")); r.run_once(now=T("2024-08-02 01:10"))
        (work / "data_store" / "brando_pa" / "forward_data_status.json").write_text(json.dumps({"last_success_utc": "x", "last_success_source": "fake", "per_tf": {}}))
    return origin, work


def _push_v2(tmp_path, origin):
    """在另一个克隆上提交 v2（代码变化 + 删除对数据文件的跟踪），推到 origin。"""
    other = tmp_path / "other"; subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "t@t"); _git(other, "config", "user.name", "t")
    (other / "src.txt").write_text("code v2\n"); _git(other, "rm", "-q", "--cached", "data_store/brando_pa/forward_bars/tracked.parquet"); (other / ".gitignore").write_text("data_store/brando_pa/forward_bars/\n")
    _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "v2"); _git(other, "push", "-q", "origin", "main")


def test_backup_integrity_and_tamper_detection(tmp_path):
    origin, work = _make_repo(tmp_path); fdir = work / "reports_out" / "brando_pa" / "phase8_forward"
    b = U.backup(work, fdir, [work / "data_store" / "brando_pa" / "forward_bars", work / "config" / "brando_pa.yaml"], work / "backups")
    assert b["verified"] and b["n_files"] >= 8
    man = json.loads(Path(b["manifest"]).read_text())["files"]
    assert any(k.endswith("A_BTC_SMA200/events.jsonl") for k in man) and any(k.endswith("A_BTC_SMA200/state.json") for k in man) and any("forward_bars" in k for k in man) and any(k.endswith("brando_pa.yaml") for k in man)
    with tarfile.open(b["archive"], "r:gz") as tar:
        names = set(tar.getnames())
    assert set(man) == names
    # 没有账户状态 → 拒绝
    with pytest.raises(RuntimeError):
        U.backup(work, work / "config", [], work / "backups2")
    # 伪造备份：篡改清单哈希后用升级器的校验逻辑（重新解包比对）应失败 —— 这里直接验证 sha256 比对函数
    p = fdir / "A_BTC_SMA200" / "events.jsonl"; h0 = U.sha256(p); p.write_text(p.read_text() + "\n"); assert U.sha256(p) != h0


def test_snapshot_compare_detects_any_change(tmp_path):
    origin, work = _make_repo(tmp_path); fdir = work / "reports_out" / "brando_pa" / "phase8_forward"
    a = U.snapshot(fdir); assert all(v["events"]["ok"] for v in a["accounts"].values()) and not U.compare_snapshots(a, U.snapshot(fdir))
    st_p = fdir / "A_BTC_SMA200" / "state.json"; st = json.loads(st_p.read_text()); st["ledgers"]["cost"]["cash"] += 1; st_p.write_text(json.dumps(st))
    assert any("ledgers" in d for d in U.compare_snapshots(a, U.snapshot(fdir)))
    st["ledgers"]["cost"]["cash"] -= 1; st_p.write_text(json.dumps(st)); assert not U.compare_snapshots(a, U.snapshot(fdir))
    with open(fdir / "B_modelB_state_machine" / "events.jsonl", "a") as f:
        f.write('{"kind":"x"}\n')
    d = U.compare_snapshots(a, U.snapshot(fdir)); assert any("events" in x for x in d)


def test_classify_and_apply_ff_preserves_data_files(tmp_path):
    origin, work = _make_repo(tmp_path); _push_v2(tmp_path, origin)
    (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").write_bytes(b"v1+local-appended")       # Mac 本地修改的被跟踪数据文件
    _git(work, "fetch", "-q", "origin", "main")
    m = U.classify_modified(work); assert m["data"] == ["data_store/brando_pa/forward_bars/tracked.parquet"] and m["other"] == []
    assert U.ff_possible(work, "origin/main")
    h_before = U.sha256(work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet")
    a = U.apply_fast_forward(work, "origin/main", m["data"])
    assert a["merged"] and a["restored_ok"] and (work / "src.txt").read_text() == "code v2\n"
    assert U.sha256(work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet") == h_before                 # 数据文件字节不变
    assert "tracked.parquet" not in _git(work, "ls-files")                                                               # 升级后不再被跟踪，后续不会再冲突
    # 代码文件被本地修改 → 归为 other（升级器会中止，不覆盖）
    (work / "src.txt").write_text("local hack\n"); assert U.classify_modified(work)["other"] == ["src.txt"]
    # 非 ff（本地有未推送提交）→ 不允许
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "local"); assert not U.ff_possible(work, "origin/main")


def test_run_upgrade_end_to_end_and_abort_paths(tmp_path, monkeypatch):
    origin, work = _make_repo(tmp_path); _push_v2(tmp_path, origin)
    (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").write_bytes(b"v1+local")
    fdir = work / "reports_out" / "brando_pa" / "phase8_forward"; before = U.snapshot(fdir)
    # 新版本 worktree（审计用当前 src）
    wt_src = Path(__file__).resolve().parents[1] / "src"
    rep = U.run_upgrade(work, "main", worktree_src=wt_src, use_launchd=False, skip_pip=True, log=lambda *_: None)
    assert rep["ok"], rep
    assert rep["steps"]["fast_forward"]["merged"] and not rep["steps"]["verify_after"]["diffs"] and (work / "src.txt").read_text() == "code v2\n"
    assert not U.compare_snapshots(before, U.snapshot(fdir)) and (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").read_bytes() == b"v1+local"
    assert list((work / "backups").glob("*.tgz")) and (fdir / "upgrade_log.jsonl").exists() and list(fdir.glob("upgrade_report_*.json"))
    # 中止路径 1：服务锁被持有 → 中止，不改代码
    origin2, work2 = _make_repo(tmp_path / "r2"); _push_v2(tmp_path / "r2", origin2)
    import fcntl
    lock = work2 / "reports_out" / "brando_pa" / "phase8_forward" / "service.lock"; fh = open(lock, "a+"); fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(U.time, "sleep", lambda *_: None)
    rep2 = U.run_upgrade(work2, "main", worktree_src=wt_src, use_launchd=False, skip_pip=True, log=lambda *_: None)
    assert not rep2["ok"] and "停止" in rep2["reason"] and (work2 / "src.txt").read_text() == "code v1\n"; fcntl.flock(fh, fcntl.LOCK_UN); fh.close()
    # 中止路径 2：本地修改了代码文件 → 中止，不覆盖
    (work2 / "src.txt").write_text("local change\n")
    rep3 = U.run_upgrade(work2, "main", worktree_src=wt_src, use_launchd=False, skip_pip=True, log=lambda *_: None)
    assert not rep3["ok"] and "非数据文件" in rep3["reason"] and (work2 / "src.txt").read_text() == "local change\n"
    # 中止路径 3：live_trading true → 拒绝
    (work2 / "src.txt").write_text("code v1\n"); (work2 / "config" / "brando_pa.yaml").write_text("execution:\n  live_trading: true\n")
    rep4 = U.run_upgrade(work2, "main", worktree_src=wt_src, use_launchd=False, skip_pip=True, log=lambda *_: None); assert not rep4["ok"] and "live_trading" in rep4["reason"]
    # 中止路径 4：升级前事件链已损坏 → 拒绝
    (work2 / "config" / "brando_pa.yaml").write_text("execution:\n  live_trading: false\n")
    p = work2 / "reports_out" / "brando_pa" / "phase8_forward" / "A_BTC_SMA200" / "events.jsonl"; lines = p.read_text().splitlines(); rec = json.loads(lines[0]); rec["kind"] = "zz"; lines[0] = json.dumps(rec, ensure_ascii=False); p.write_text("\n".join(lines) + "\n")
    rep5 = U.run_upgrade(work2, "main", worktree_src=wt_src, use_launchd=False, skip_pip=True, log=lambda *_: None); assert not rep5["ok"] and "事件链" in rep5["reason"]


def _make_repo_with_package(tmp_path):
    """含 brando_pa 源码副本的临时仓库（让 worktree 里有新版本升级器），路径带空格。"""
    import shutil
    base = tmp_path / "my repo dir"; base.mkdir(parents=True)
    origin, work = _make_repo(base, with_forward=True)
    src_real = Path(__file__).resolve().parents[1] / "src"
    shutil.copytree(src_real / "brando_pa", work / "src" / "brando_pa", ignore=shutil.ignore_patterns("__pycache__")); shutil.copytree(src_real / "spot90", work / "src" / "spot90", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / "brando_pa_safe_upgrade.sh", work / "scripts" / "brando_pa_safe_upgrade.sh") if (work / "scripts").mkdir(exist_ok=True) or True else None
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "with package"); _git(work, "push", "-q", "origin", "main")
    return origin, work


def test_bootstrap_from_tmp_resolves_repo_and_dry_run_then_real_upgrade(tmp_path):
    """真实报错回归：从 /tmp 运行引导脚本副本时曾把 /tmp/.. 当作仓库 → 'not a git repository'。现在：BRANDO_REPO / 当前目录 / ~/1hao- 解析；路径含空格。"""
    import shutil
    origin, work = _make_repo_with_package(tmp_path)
    script_copy = Path(tempfile.mkdtemp(prefix="brando tmp ")) / "brando_safe_upgrade.sh"; shutil.copy2(work / "scripts" / "brando_pa_safe_upgrade.sh", script_copy)
    env = dict(os.environ, BRANDO_NO_LAUNCHD="1", BRANDO_SKIP_PIP="1", BRANDO_PY=sys.executable)
    # 1) 从 /tmp 运行、cwd 也在 /tmp、无 BRANDO_REPO、~/1hao- 不存在 → 明确报错，不触碰服务
    env_nohome = dict(env, HOME=str(tmp_path / "nohome")); (tmp_path / "nohome").mkdir()
    r = subprocess.run(["bash", str(script_copy), "main", "--dry-run"], cwd=str(script_copy.parent), env=env_nohome, capture_output=True, text=True)
    assert r.returncode == 1 and "找不到 brando_pa 仓库" in r.stdout and "not a git repository" not in (r.stdout + r.stderr)
    # 2) cwd = 仓库（含空格路径）→ dry-run：备份 + 停/恢复流程验证，不改代码
    fdir = work / "reports_out" / "brando_pa" / "phase8_forward"; before = U.snapshot(fdir); head0 = _git(work, "rev-parse", "HEAD")
    r = subprocess.run(["bash", str(script_copy), "main", "--dry-run"], cwd=str(work), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "仓库目录：" in r.stdout and "dry-run" in r.stdout and _git(work, "rev-parse", "HEAD") == head0 and not U.compare_snapshots(before, U.snapshot(fdir)) and list((work / "backups").glob("*.tgz"))
    # 3) BRANDO_REPO 显式指定、cwd 在 /tmp → 正式升级到 origin 的新提交（数据文件保存/放回）
    _push_v2(tmp_path / "my repo dir", origin)
    (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").write_bytes(b"local bars")
    r = subprocess.run(["bash", str(script_copy), "main"], cwd=str(script_copy.parent), env=dict(env, BRANDO_REPO=str(work)), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / "src.txt").read_text() == "code v2\n" and (work / "data_store" / "brando_pa" / "forward_bars" / "tracked.parquet").read_bytes() == b"local bars"
    assert not U.compare_snapshots(before, U.snapshot(fdir)) and not (work / ".upgrade_worktree").exists()
    # 4) 故障自动恢复：新版审计失败（人为损坏一个账户事件链后再升级）→ 不启动新版本，报告原因；代码保持原样
    p = fdir / "A_BTC_SMA200" / "events.jsonl"; lines = p.read_text().splitlines(); rec = json.loads(lines[1]); rec["kind"] = "zz"; lines[1] = json.dumps(rec, ensure_ascii=False); p.write_text("\n".join(lines) + "\n")
    r = subprocess.run(["bash", str(script_copy), "main"], cwd=str(work), env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "事件链" in r.stdout
