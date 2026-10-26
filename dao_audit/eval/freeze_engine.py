#!/usr/bin/env python3
"""Freeze the detection engine: record SHA256 of every engine source file plus
the git commit, into eval/engine_freeze.json. The blind runner verifies these
hashes before a run so rules cannot be silently changed mid-blind-test.

Usage:
  python3 eval/freeze_engine.py            # write/refresh the freeze file
  python3 eval/freeze_engine.py --check    # verify current files match the freeze
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tool" / "dao_audit"
ENGINE_FILES = [
    TOOL / "rules.py",
    TOOL / "static_scan.py",
    TOOL / "analysis" / "detectors.py",
    TOOL / "analysis" / "solmodel.py",
    TOOL / "analysis" / "semantic.py",   # V0.13 semantic governance analyzer
    TOOL / "analysis" / "compile_backend.py",  # V0.14 compiler/Slither backend
    # V0.15 read-only baseline: exact-compiler build + Slither built-ins
    TOOL / "analysis" / "baseline.py",
    TOOL / "analysis" / "evidence.py",          # V0.17 permission evidence tiers
    TOOL / "analysis" / "perm" / "deps.py",     # V0.15 tested dependency gap fills
    TOOL / "compiler" / "build.py",
    TOOL / "compiler" / "project.py",
    TOOL / "compiler" / "toolchain.py",
    TOOL / "compiler" / "semver.py",
    ROOT / "tool" / "solc_toolchain" / "solcjs_std.js",
    Path(__file__).resolve().parent / "blind_run.py",
    Path(__file__).resolve().parent / "evaluate.py",
    Path(__file__).resolve().parent / "evaluate_v15.py",
    Path(__file__).resolve().parent / "check_isolation.py",
    Path(__file__).resolve().parent / "v17" / "review_queue.py",
    Path(__file__).resolve().parent / "v17" / "coverage_and_plan.py",
]
FREEZE = Path(__file__).resolve().parent / "engine_freeze.json"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


def _env() -> dict:
    """Analysis dependencies pinned with the freeze (informational)."""
    out = {}
    try:
        from importlib.metadata import version
        for pkg in ("slither-analyzer", "crytic-compile"):
            out[pkg] = version(pkg)
    except Exception:
        pass
    try:
        out["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
    except Exception:
        pass
    return out


def compute() -> dict:
    return {
        "git_commit": _git_commit(),
        "files": {str(p.relative_to(ROOT)): _sha256(p) for p in ENGINE_FILES},
        "environment": _env(),
    }


def check() -> bool:
    if not FREEZE.exists():
        print("no freeze file; run without --check first", file=sys.stderr)
        return False
    frozen = json.loads(FREEZE.read_text())
    cur = compute()["files"]
    env = compute()["environment"]
    for k, v in frozen.get("environment", {}).items():
        if env.get(k) != v:
            print(f"MISMATCH env {k}: frozen={v} now={env.get(k)}", file=sys.stderr)
            ok_env = False
            break
    else:
        ok_env = True
    ok = True
    for f, h in frozen["files"].items():
        if cur.get(f) != h:
            print(f"MISMATCH {f}: frozen={h[:12]} now={cur.get(f, 'MISSING')[:12]}", file=sys.stderr)
            ok = False
    ok = ok and ok_env
    print("engine freeze OK" if ok else "engine freeze CHANGED", file=sys.stderr)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check() else 1)
    FREEZE.write_text(json.dumps(compute(), ensure_ascii=False, indent=2))
    print(f"wrote {FREEZE}")
