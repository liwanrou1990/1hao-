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
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tool"))

import freeze_engine  # noqa: E402
from dao_audit.analysis.solmodel import parse_source  # noqa: E402
from dao_audit.analysis.detectors import run_detectors  # noqa: E402
from dao_audit.analysis.semantic import run_semantic_case  # noqa: E402

DATASET = HERE / "dataset"


def run_case(case_dir: Path, engine: str = "semantic") -> dict:
    """Run the engine over the case, reading ONLY contracts/. engine='semantic'
    (V0.13, case-level call-graph) or 'regex' (V0.12 per-file detectors)."""
    cdir = case_dir / "contracts"
    contracts = sorted(cdir.rglob("*.sol"))
    out = {"case": case_dir.name, "engine": engine, "n_files": len(contracts),
           "files": {}, "findings": []}
    if engine == "semantic":
        t0 = time.monotonic()
        try:
            fs = run_semantic_case(cdir)
            out["ms"] = round((time.monotonic() - t0) * 1000, 1)
            for f in fs:
                d = f.to_dict()
                d["file"] = f.file.split("/")[-1] if f.file else ""
                out["findings"].append(d)
        except Exception as e:
            out["error"] = repr(e)[:200]
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
    ap.add_argument("--engine", default="semantic", choices=["semantic", "regex"])
    a = ap.parse_args()

    if not freeze_engine.check():
        print("ABORT: engine freeze mismatch — re-freeze or restore engine before a blind run",
              file=sys.stderr)
        return 1

    cases = ([DATASET / a.case] if a.case else
             sorted(d for d in DATASET.iterdir() if d.is_dir())) if DATASET.exists() else []
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
        (raw_dir / f"{c.name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"[blind:{a.engine}] {c.name}: {res['n_files']} files, {len(res['findings'])} findings")
    print(f"raw outputs -> {raw_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
