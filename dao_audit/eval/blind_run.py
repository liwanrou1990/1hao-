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

DATASET = HERE / "dataset"


def run_case(case_dir: Path) -> dict:
    """Run the engine on every .sol under contracts/. Returns raw findings.
    Only reads contracts/ — nothing else in the case directory."""
    contracts = sorted((case_dir / "contracts").rglob("*.sol"))
    out = {"case": case_dir.name, "n_files": len(contracts), "files": {}, "findings": []}
    for sol in contracts:
        rel = str(sol.relative_to(case_dir / "contracts"))
        t0 = time.monotonic()
        try:
            su = parse_source(sol)
            fs = run_detectors(su)
            ms = round((time.monotonic() - t0) * 1000, 1)
            out["files"][rel] = {"ok": True, "n_findings": len(fs), "ms": ms}
            for f in fs:
                d = f.to_dict()
                d["file"] = rel
                out["findings"].append(d)
        except Exception as e:  # engine must never crash the whole run on one file
            out["files"][rel] = {"ok": False, "error": repr(e)[:200]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None)
    ap.add_argument("--round", default="round1")
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
        res = run_case(c)
        (raw_dir / f"{c.name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"[blind] {c.name}: {res['n_files']} files, {len(res['findings'])} findings")
    print(f"raw outputs -> {raw_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
