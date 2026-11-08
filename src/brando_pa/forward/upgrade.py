"""安全升级器（第九阶段 Mac 部署）。由**新版本代码**执行（引导脚本把 origin/<branch> 检出到临时 worktree 并以其 src 运行本模块），步骤：

 1. 升级前快照：每个账户 state.json（现金、持仓、挂单数、已处理订单数、last_daily_ts、forward_start）、事件链条数与末尾哈希、链校验、纠错链、forward_data_status。
 2. 完整备份（tar.gz + 清单 SHA256）：账户状态/事件链/纠错链/日报/排行榜/心跳/告警/运行日志、forward_bars 行情仓、forward_data_status.json、data_events.jsonl、config/brando_pa.yaml、~/.config/brando_pa/env、launchd plist。
    备份后解包到临时目录逐文件比对哈希；任一不一致 → 禁止升级。
 3. 安全停止服务：launchctl bootout（macOS）→ 等待 service.lock 可获取且无 forward-service 进程；超时 → 中止（不升级）。
 4. Git 预检：只允许 fast-forward；本地修改的**数据文件**（data_store/**、reports_out/**、docs/**/forward_logs/**、backups/**）先原样保存，不 reset、不 clean；其他本地修改 → 中止。
 5. 依赖安装（新版本 requirements-forward.txt）与新版本时间顺序审计（只读）在切换代码之前完成；任一失败 → 恢复旧版本服务并报告原因。
 6. 切换代码：仅对已保存的数据文件执行 checkout 以让 ff 合并通过，合并后立即把保存的副本放回并校验哈希一致（数据文件在升级前后字节相同）。
 7. 升级后验证：快照逐项相同（权益不变由现金 + 持仓数量不变保证；事件链条数、末尾哈希、链校验、已处理订单、last_daily_ts、forward_start、挂单数）；任一不同 → 不启动服务并报告。
 8. 启动新版本服务 → 健康检查。全部步骤写 upgrade_log.jsonl（追加）与 upgrade_report_<UTC>.json。
真实交易：不触碰任何开关；config 中 live_trading 必须为 false 否则中止。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import pandas as pd

LABEL = "com.brando-pa.forward"
DATA_PREFIXES = ("data_store/", "reports_out/", "backups/")
DATA_SUBSTR = ("/forward_logs/",)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=check)


def _last_line(p: Path) -> str | None:
    if not p.exists() or p.stat().st_size == 0:
        return None
    with open(p, "rb") as f:
        f.seek(max(0, p.stat().st_size - 65536)); lines = f.read().splitlines()
    return lines[-1].decode("utf-8", "replace") if lines else None


def verify_chain_file(p: Path) -> dict:
    """独立实现的哈希链校验（不依赖旧/新版本其他模块）。"""
    if not p.exists():
        return {"ok": True, "n": 0}
    prev = "0" * 64; n = 0
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line); h = rec.pop("hash", None)
        if rec.get("prev_hash") != prev or hashlib.sha256(json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest() != h:
            return {"ok": False, "n": n, "broken_at_line": n + 1}
        prev = h; n += 1
    return {"ok": True, "n": n, "last_hash": prev}


# ---------- 1. 快照 ----------
def snapshot(forward_dir: Path, data_status: Path | None = None) -> dict:
    forward_dir = Path(forward_dir); out = {"taken_at_utc": str(pd.Timestamp.now(tz="UTC")), "accounts": {}}
    for d in sorted(p for p in forward_dir.iterdir() if p.is_dir() and (p / "state.json").exists()):
        st = json.loads((d / "state.json").read_text())
        led = {k: {"cash": v["cash"], "lots_qty": {s: sum(l["qty"] for l in ls) for s, ls in v.get("lots", {}).items()}, "fees_paid": v.get("fees_paid"), "realized": v.get("realized")} for k, v in st["ledgers"].items()}
        out["accounts"][d.name] = {"initial_usdt": st.get("initial_usdt"), "forward_start_utc": st.get("forward_start_utc"), "last_daily_ts": st.get("last_daily_ts"), "pending": len(st.get("pending", [])), "processed_orders": len(st.get("processed_orders", [])), "ledgers": led,
                                   "events": verify_chain_file(d / "events.jsonl"), "corrections": verify_chain_file(d / "corrections.jsonl"), "events_sha256": sha256(d / "events.jsonl") if (d / "events.jsonl").exists() else None}
    out["runner_events"] = verify_chain_file(forward_dir / "runner_events.jsonl")
    out["alerts_lines"] = sum(1 for _ in open(forward_dir / "alerts.jsonl")) if (forward_dir / "alerts.jsonl").exists() else 0
    if data_status and Path(data_status).exists():
        ds = json.loads(Path(data_status).read_text()); out["data_status"] = {k: ds.get(k) for k in ("last_success_utc", "last_success_source", "per_tf")}
    return out


def compare_snapshots(a: dict, b: dict) -> list[str]:
    diffs = []
    if set(a["accounts"]) != set(b["accounts"]):
        diffs.append(f"账户集合变化 {sorted(a['accounts'])} → {sorted(b['accounts'])}")
    for n in a["accounts"]:
        if n not in b["accounts"]:
            continue
        x, y = a["accounts"][n], b["accounts"][n]
        for k in ("initial_usdt", "forward_start_utc", "last_daily_ts", "pending", "processed_orders", "ledgers", "events_sha256"):
            if x.get(k) != y.get(k):
                diffs.append(f"{n}.{k}: {x.get(k)} → {y.get(k)}")
        for k in ("events", "corrections"):
            if (x[k].get("n"), x[k].get("last_hash"), x[k].get("ok")) != (y[k].get("n"), y[k].get("last_hash"), y[k].get("ok")):
                diffs.append(f"{n}.{k}: {x[k]} → {y[k]}")
    if (a["runner_events"].get("n"), a["runner_events"].get("last_hash")) != (b["runner_events"].get("n"), b["runner_events"].get("last_hash")):
        diffs.append(f"runner_events: {a['runner_events']} → {b['runner_events']}")
    return diffs


# ---------- 2. 备份 ----------
def backup(repo: Path, forward_dir: Path, extra_paths: list[Path], out_dir: Path) -> dict:
    repo = Path(repo); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ"); tgz = out_dir / f"brando_pa_forward_backup_{ts}.tgz"; manifest = {}
    files = []
    for base in [forward_dir] + list(extra_paths):
        base = Path(base)
        if base.is_dir():
            files += [p for p in base.rglob("*") if p.is_file() and p.name != "service.lock"]
        elif base.is_file():
            files.append(base)
    if not any(p.name == "state.json" for p in files) or not any(p.name == "events.jsonl" for p in files):
        raise RuntimeError("备份内容不完整：缺少账户 state.json / events.jsonl，禁止升级")
    with tarfile.open(tgz, "w:gz") as tar:
        for p in files:
            arc = str(p.resolve().relative_to(repo.resolve())) if str(p.resolve()).startswith(str(repo.resolve())) else "external/" + str(p).strip("/").replace("/", "__")
            manifest[arc] = {"sha256": sha256(p), "bytes": p.stat().st_size}; tar.add(p, arcname=arc)
    mpath = out_dir / f"brando_pa_forward_backup_{ts}.manifest.json"; mpath.write_text(json.dumps({"created_utc": ts, "archive": tgz.name, "files": manifest}, indent=1, ensure_ascii=False))
    # 完整性校验：解包到临时目录逐文件比对
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz, "r:gz") as tar:
            tar.extractall(td, filter="data")
        bad = []
        for arc, meta in manifest.items():
            q = Path(td) / arc
            if not q.exists() or sha256(q) != meta["sha256"]:
                bad.append(arc)
    if bad:
        raise RuntimeError(f"备份完整性校验失败：{bad[:5]}；禁止升级")
    return {"archive": str(tgz), "manifest": str(mpath), "n_files": len(manifest), "bytes": int(sum(m["bytes"] for m in manifest.values())), "verified": True}


# ---------- 3. 服务停止 / 启动 ----------
def lock_is_free(lock_path: Path) -> bool:
    import fcntl
    if not lock_path.exists():
        return True
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fh, fcntl.LOCK_UN); return True
    except OSError:
        return False
    finally:
        fh.close()


def service_processes() -> list[str]:
    try:
        r = subprocess.run(["pgrep", "-fl", "brando_pa.cli forward-service"], capture_output=True, text=True)
        return [l for l in r.stdout.splitlines() if "forward-upgrade" not in l and "brando_pa.forward.upgrade" not in l]
    except Exception:  # noqa: BLE001
        return []


def stop_service(lock_path: Path, use_launchd: bool, timeout_s: int = 90) -> dict:
    uid = os.getuid()
    if use_launchd:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if lock_is_free(lock_path) and not service_processes():
            return {"stopped": True, "waited_s": round(time.time() - t0, 1)}
        time.sleep(2)
    return {"stopped": False, "lock_free": lock_is_free(lock_path), "processes": service_processes()}


def start_service(repo: Path, use_launchd: bool) -> dict:
    uid = os.getuid()
    if not use_launchd:
        return {"started": False, "note": "非 macOS/launchd 环境：请用 systemd 或手动启动"}
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
    r = subprocess.run(["launchctl", "kickstart", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    return {"started": r.returncode == 0, "stderr": r.stderr[-300:]}


# ---------- 4. Git ----------
def classify_modified(repo: Path) -> dict:
    r = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo)
    data, other = [], []
    for line in r.stdout.splitlines():
        path = line[3:].strip()
        if path.startswith(DATA_PREFIXES) or any(s in path for s in DATA_SUBSTR):
            data.append(path)
        else:
            other.append(path)
    return {"data": data, "other": other}


def ff_possible(repo: Path, remote_ref: str) -> bool:
    return _run(["git", "merge-base", "--is-ancestor", "HEAD", remote_ref], cwd=repo, check=False).returncode == 0


def apply_fast_forward(repo: Path, remote_ref: str, data_files: list[str]) -> dict:
    """保存本地修改的数据文件 → 对这些路径 checkout 索引版本（仅为让 ff 合并通过）→ ff 合并 → 放回副本并校验哈希。"""
    repo = Path(repo); saved = {}; tmp = Path(tempfile.mkdtemp(prefix="brando_upgrade_"))
    for f in data_files:
        src = repo / f
        if src.exists():
            dst = tmp / f; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst); saved[f] = sha256(src)
    try:
        if data_files:
            _run(["git", "checkout", "--"] + data_files, cwd=repo)
        r = _run(["git", "merge", "--ff-only", remote_ref], cwd=repo, check=False)
        merged = r.returncode == 0; err = r.stderr[-400:]
    finally:
        restored = {}
        for f, h in saved.items():
            dst = repo / f; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(tmp / f, dst); restored[f] = sha256(dst) == h
        shutil.rmtree(tmp, ignore_errors=True)
    return {"merged": merged, "stderr": err, "restored_ok": all(restored.values()) if restored else True, "restored": restored, "head": _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()}


# ---------- 主流程 ----------
def run_upgrade(repo: Path, branch: str, worktree_src: Path | None = None, use_launchd: bool | None = None, python: str | None = None, dry_run: bool = False, skip_pip: bool = False, log=print) -> dict:
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=Path(repo).resolve(), check=False)
    if top.returncode != 0:
        raise SystemExit(f"--repo {repo} 不是 Git 仓库（请指向 ~/1hao- 这类项目目录）")
    repo = Path(top.stdout.strip()).resolve(); use_launchd = (sys.platform == "darwin") if use_launchd is None else use_launchd; python = python or sys.executable
    fdir = repo / "reports_out" / "brando_pa" / "phase8_forward"; lock = fdir / "service.lock"; remote = f"origin/{branch}"
    ulog = fdir / "upgrade_log.jsonl"; rep = {"started_utc": str(pd.Timestamp.now(tz="UTC")), "repo": str(repo), "branch": branch, "steps": {}, "ok": False, "service_restarted_old_version": False}
    def step(name, **kw):
        rep["steps"][name] = kw; log(f"[upgrade] {name}: {json.dumps(kw, ensure_ascii=False, default=str)[:400]}")
        with open(ulog, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": str(pd.Timestamp.now(tz="UTC")), "step": name, **kw}, ensure_ascii=False, default=str) + "\n")
    def finish(ok, reason=""):
        rep["ok"] = ok; rep["reason"] = reason; rep["finished_utc"] = str(pd.Timestamp.now(tz="UTC"))
        (fdir / f"upgrade_report_{rep['started_utc'][:19].replace(':', '').replace(' ', 'T')}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str)); log(f"[upgrade] {'成功' if ok else '失败'}：{reason}"); return rep
    def restart_old():
        r = start_service(repo, use_launchd); rep["service_restarted_old_version"] = bool(r.get("started")); step("restart_old_version", **r)
    # 0 前置：真实交易禁用
    cfgp = repo / "config" / "brando_pa.yaml"
    if cfgp.exists() and any(l.strip().startswith("live_trading:") and "true" in l for l in cfgp.read_text().splitlines()):
        return finish(False, "config live_trading: true —— 拒绝升级")
    # 1 快照
    before = snapshot(fdir, repo / "data_store" / "brando_pa" / "forward_data_status.json"); step("snapshot_before", accounts=list(before["accounts"]), chains_ok=all(v["events"]["ok"] for v in before["accounts"].values()))
    if not all(v["events"]["ok"] for v in before["accounts"].values()):
        return finish(False, "升级前事件链校验失败，先人工检查")
    # 2 备份 + 校验
    try:
        extra = [repo / "data_store" / "brando_pa" / "forward_bars", repo / "data_store" / "brando_pa" / "forward_data_status.json", repo / "data_store" / "brando_pa" / "data_events.jsonl", cfgp, Path.home() / ".config" / "brando_pa" / "env", Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"]
        b = backup(repo, fdir, [p for p in extra if p.exists()], repo / "backups"); step("backup", **b)
    except Exception as e:  # noqa: BLE001
        return finish(False, f"备份失败：{e}")
    # 3 停止服务
    s = stop_service(lock, use_launchd); step("stop_service", **s)
    if not s["stopped"]:
        return finish(False, "无法确认服务已停止（锁仍被持有或进程仍在），中止升级")
    if dry_run:
        restart_old(); return finish(True, "dry-run：备份与停止/恢复流程已验证，未改动代码")
    # 4 Git 预检
    try:
        _run(["git", "fetch", "origin", branch], cwd=repo)
    except Exception as e:  # noqa: BLE001
        restart_old(); return finish(False, f"git fetch 失败：{e}")
    mods = classify_modified(repo); step("local_modifications", **mods)
    if mods["other"]:
        restart_old(); return finish(False, f"存在本地修改的非数据文件 {mods['other'][:5]}，不自动覆盖；请先处理")
    if not ff_possible(repo, remote):
        restart_old(); return finish(False, "本地分支与远程不能 fast-forward（本地有未推送提交或分叉）；不使用 reset/强制覆盖")
    new_head = _run(["git", "rev-parse", remote], cwd=repo).stdout.strip(); old_head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip(); step("git_refs", old_head=old_head, new_head=new_head)
    # 5 依赖（新版本清单）+ 新版本审计（只读，在切换代码前）
    wt_src = Path(worktree_src) if worktree_src else None
    if not skip_pip:
        req = (wt_src.parent / "deploy" / "requirements-forward.txt") if wt_src else None
        if req and req.exists():
            r = subprocess.run([python, "-m", "pip", "install", "-q", "-r", str(req)], capture_output=True, text=True)
            step("pip_install", returncode=r.returncode, stderr=r.stderr[-300:])
            if r.returncode != 0:
                restart_old(); return finish(False, f"依赖安装失败：{r.stderr[-200:]}")
    if wt_src:
        env = dict(os.environ, PYTHONPATH=str(wt_src)); r = subprocess.run([python, "-m", "brando_pa.cli", "forward-timeline-audit", "--log-dir", str(fdir)], cwd=str(repo), env=env, capture_output=True, text=True)
        step("timeline_audit_new_version", returncode=r.returncode, tail=r.stdout[-200:])
        if r.returncode != 0:
            restart_old(); return finish(False, "新版本时间顺序审计未通过，不启动新版本服务（旧版本已恢复）")
    # 6 切换代码（数据文件保存/放回）
    if old_head != new_head:
        a = apply_fast_forward(repo, remote, mods["data"]); step("fast_forward", **a)
        if not a["merged"]:
            restart_old(); return finish(False, f"fast-forward 合并失败：{a['stderr'][-200:]}（数据文件已放回，旧版本服务已恢复）")
        if not a["restored_ok"]:
            return finish(False, "数据文件放回后哈希不一致！服务不启动，请用备份恢复")
    else:
        step("fast_forward", merged=True, note="已是最新")
    # 7 升级后验证
    after = snapshot(fdir, repo / "data_store" / "brando_pa" / "forward_data_status.json"); diffs = compare_snapshots(before, after); step("verify_after", diffs=diffs)
    if diffs:
        return finish(False, f"升级后账户/事件链快照有变化：{diffs[:5]}；服务不启动，请用备份 {b['archive']} 核对")
    # 8 启动 + 健康
    st = start_service(repo, use_launchd); step("start_service", **st)
    if use_launchd and st.get("started"):
        time.sleep(20); env = dict(os.environ, PYTHONPATH="src")
        r = subprocess.run([python, "-m", "brando_pa.cli", "forward-health"], cwd=str(repo), env=env, capture_output=True, text=True); step("health", returncode=r.returncode, tail=r.stdout[-300:])
    return finish(True, f"升级完成 {old_head[:7]} → {new_head[:7]}；备份 {b['archive']}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); ap.add_argument("--branch", default="claude/brando-pa-phase9-deploy"); ap.add_argument("--worktree-src", dest="worktree_src"); ap.add_argument("--dry-run", dest="dry_run", action="store_true"); ap.add_argument("--no-launchd", dest="no_launchd", action="store_true"); ap.add_argument("--skip-pip", dest="skip_pip", action="store_true")
    a = ap.parse_args(argv); rep = run_upgrade(Path(a.repo), a.branch, Path(a.worktree_src) if a.worktree_src else None, use_launchd=(False if a.no_launchd else None), dry_run=a.dry_run, skip_pip=a.skip_pip)
    print(json.dumps({k: rep[k] for k in ("ok", "reason")}, ensure_ascii=False)); raise SystemExit(0 if rep["ok"] else 1)


if __name__ == "__main__":
    main()
