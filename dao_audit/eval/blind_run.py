#!/usr/bin/env python3
"""Blind runner: run the FROZEN detection engine over each dataset case, reading
ONLY eval/dataset/<case>/contracts/ (never answers/, reports, or fix notes).

Saves raw per-file findings to eval/results/raw/<case>.json. It performs no
scoring and never imports the answers. Verifies the engine freeze first; aborts
if the engine changed (so rules cannot be altered mid-blind-test).

Usage:
  python3 eval/blind_run.py                 # run all cases in dataset/
  python3 eval/blind_run.py --case <id>     # run one case
  python3 eval/blind_run.py --round round2  # separate results namespace
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tool"))

import freeze_engine  # noqa: E402
from dao_audit.analysis.solmodel import parse_source  # noqa: E402
from dao_audit.analysis.detectors import run_detectors  # noqa: E402
from dao_audit.analysis.semantic import run_semantic_case, governance_relation_graph  # noqa: E402
from dao_audit.analysis.compile_backend import analyze_case as compile_analyze  # noqa: E402

DATASET = HERE / "dataset"

# ---- isolation guard (V0.15): record every file the detector process opens
# under eval/ and abort if it touches labels, provenance notes or scores.
_OPENED = set()
_FORBIDDEN = (str(HERE / "answers"), "PROVENANCE.md", "_CURATION_LOG", "_UNAVAILABLE",
              str(HERE / "results"), "FINAL_REPORT", "PREREGISTRATION")


class IsolationViolation(RuntimeError):
    pass


def _audit(event, args):
    if event != "open" or not args:
        return
    p = args[0]
    if not isinstance(p, str):
        return
    ap = os.path.abspath(p)
    if ap.startswith(str(HERE)):
        _OPENED.add(ap)
        writing = len(args) > 1 and isinstance(args[1], str) and any(m in args[1] for m in "wax+")
        if not writing and any(f in ap for f in _FORBIDDEN):
            raise IsolationViolation(f"detector tried to read {ap}")


def run_baseline(case_dir: Path, profile: str = "v15") -> dict:
    """V0.15 baseline: exact-compiler build + Slither built-ins (read-only)."""
    from dao_audit.compiler.build import build_case
    from dao_audit.analysis.baseline import analyze
    out = {"case": case_dir.name, "engine": "baseline", "findings": [], "heuristic_findings": []}
    t0 = time.monotonic()
    before = set(_OPENED)
    try:
        cb = build_case(case_dir)
        out["build"] = cb.manifest
        out["n_files"] = len(cb.unit_to_dataset)
        st = cb.manifest.get("status")
        out["data_status"] = "OK" if st == "OK" else ("PARTIAL" if st == "PARTIAL" else "ANALYSIS_INCOMPLETE")
        if cb.units:
            out.update(analyze(cb, profile=profile))
    except IsolationViolation:
        raise
    except Exception as e:
        out["error"] = repr(e)[:300]
        out["data_status"] = "ANALYSIS_INCOMPLETE"
    out["ms"] = round((time.monotonic() - t0) * 1000, 1)
    out["eval_files_opened"] = sorted(os.path.relpath(p, HERE) for p in _OPENED - before)
    return out


def run_case(case_dir: Path, engine: str = "semantic") -> dict:
    """Run the engine over the case, reading ONLY contracts/. engine='semantic'
    (V0.13, case-level call-graph) or 'regex' (V0.12 per-file detectors)."""
    cdir = case_dir / "contracts"
    contracts = sorted(cdir.rglob("*.sol"))
    out = {"case": case_dir.name, "engine": engine, "n_files": len(contracts),
           "files": {}, "findings": []}
    if engine == "baseline":
        return run_baseline(case_dir)
    if engine == "baseline17":
        r = run_baseline(case_dir, profile="v17")
        r["engine"] = "baseline17"
        return r
    if engine == "semantic":
        t0 = time.monotonic()
        try:
            fs = run_semantic_case(cdir)
            out["ms"] = round((time.monotonic() - t0) * 1000, 1)
            for f in fs:
                d = f.to_dict()
                d["file"] = f.file.split("/")[-1] if f.file else ""
                out["findings"].append(d)
            # Phase-3 governance relation graph + full-chain view
            out["relation_graph"] = governance_relation_graph(cdir)
            # Phase-1 compiler/Slither backend coverage (ANALYSIS_INCOMPLETE honesty)
            out["compiler_backend"] = compile_analyze(cdir)
        except Exception as e:
            out["error"] = repr(e)[:200]
            out["data_status"] = "ANALYSIS_INCOMPLETE"
        return out
    for sol in contracts:
        rel = str(sol.relative_to(cdir))
        try:
            su = parse_source(sol)
            fs = run_detectors(su)
            out["files"][rel] = {"ok": True, "n_findings": len(fs)}
            for f in fs:
                d = f.to_dict(); d["file"] = rel
                out["findings"].append(d)
        except Exception as e:
            out["files"][rel] = {"ok": False, "error": repr(e)[:200]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None)
    ap.add_argument("--round", default="round1")
    ap.add_argument("--engine", default="semantic", choices=["semantic", "regex", "baseline", "baseline17"])
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--dev", action="store_true",
                    help="development run: skip the freeze check (recorded in every raw output); "
                         "results of a --dev run are never blind scores")
    a = ap.parse_args()
    dataset = Path(a.dataset)
    sys.addaudithook(_audit)

    if not a.dev and not freeze_engine.check():
        print("ABORT: engine freeze mismatch — re-freeze or restore engine before a blind run",
              file=sys.stderr)
        return 1

    cases = ([dataset / a.case] if a.case else
             sorted(d for d in dataset.iterdir() if d.is_dir() and not d.name.startswith("_"))) \
        if dataset.exists() else []
    if not cases:
        print(f"no cases under {DATASET}", file=sys.stderr)
        return 1

    raw_dir = HERE / "results" / a.round / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for c in cases:
        if not (c / "contracts").exists():
            print(f"skip {c.name}: no contracts/", file=sys.stderr)
            continue
        res = run_case(c, engine=a.engine)
        res["freeze_checked"] = not a.dev
        res["run_kind"] = "dev" if a.dev else "blind"
        (raw_dir / f"{c.name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"[blind:{a.engine}] {c.name}: {res.get('n_files')} files, {len(res['findings'])} findings, "
              f"{len(res.get('heuristic_findings', []))} heuristic, {res.get('data_status', '')}")
    print(f"raw outputs -> {raw_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
