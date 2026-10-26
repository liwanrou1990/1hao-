#!/usr/bin/env python3
"""Round isolation check (evaluator side). Prints only PASS/FAIL counts, never
label content.

static  (before the blind run)
  * no detector / build / runner file mentions any case id, project name or
    repository slug of the held-out dataset;
  * BUILD.json / BUILD_SOURCE.json carry only build keys (no labels);
  * contracts/ holds only .sol files.
runtime (after the blind run, --round given)
  * every eval/ file the detector process opened is the case's own contracts/,
    BUILD.json, or the engine freeze file; nothing from answers/, results/,
    PROVENANCE.md or curation notes.

Usage: python3 eval/check_isolation.py --dataset eval/dataset_r4 [--round round4]
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = [ROOT / "tool" / "dao_audit" / "analysis" / "baseline.py",
          *sorted((ROOT / "tool" / "dao_audit" / "compiler").glob("*.py")),
          ROOT / "tool" / "solc_toolchain" / "solcjs_std.js",
          HERE / "blind_run.py", HERE / "freeze_engine.py"]
# generic words that occur in repo names but are ordinary Solidity/English terms
GENERIC = {"contracts", "governance", "safe", "protocol", "core", "monorepo", "smart", "solidity",
           "voting", "token", "bridge", "router", "network"}
BUILD_KEYS = {"repo", "commit", "files", "unmatched_files", "repo_root_hint"}


def tokens(dataset: Path):
    toks = set()
    for d in dataset.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        toks.add(d.name)
        a = HERE / "answers" / f"{d.name}.json"
        if a.exists():
            p = json.loads(a.read_text()).get("project")
            if p and len(p) >= 4:
                toks.add(p)
        bs = d / "BUILD_SOURCE.json"
        if bs.exists():
            slug = json.loads(bs.read_text())["repo"].replace("https://github.com/", "")
            toks |= {slug, slug.split("/")[-1]}
    return {t for t in toks if len(t) >= 4 and t.lower() not in GENERIC}


def static(dataset: Path):
    fails = []
    toks = tokens(dataset)
    for f in ENGINE:
        txt = f.read_text(errors="ignore").lower()
        for t in toks:
            if re.search(r"(?<![a-z0-9_])" + re.escape(t.lower()) + r"(?![a-z0-9_])", txt):
                fails.append(f"engine file {f.name} mentions held-out identifier {t!r}")
    n = 0
    for d in sorted(dataset.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        n += 1
        bj = d / "BUILD.json"
        if not bj.exists():
            fails.append(f"{d.name}: no BUILD.json")
        elif set(json.loads(bj.read_text())) - BUILD_KEYS:
            fails.append(f"{d.name}: BUILD.json has non-build keys")
        bs = d / "BUILD_SOURCE.json"
        if bs.exists() and set(json.loads(bs.read_text())) - {"repo", "commit"}:
            fails.append(f"{d.name}: BUILD_SOURCE.json has extra keys")
        for p in (d / "contracts").rglob("*"):
            if p.is_file() and p.suffix != ".sol":
                fails.append(f"{d.name}: non-.sol file in contracts/")
    return n, len(toks), fails


def runtime(dataset: Path, rnd: str):
    fails, n = [], 0
    for rf in sorted((HERE / "results" / rnd / "raw").glob("*.json")):
        r = json.loads(rf.read_text()); n += 1
        case = r["case"]
        rel_ds = str(dataset.resolve().relative_to(HERE))
        allowed = (f"{rel_ds}/{case}/contracts/", f"{rel_ds}/{case}/BUILD.json", "engine_freeze.json")
        for p in r.get("eval_files_opened", []):
            if not p.startswith(allowed):
                fails.append(f"{case}: detector opened a non-allowed eval file")
    return n, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--round")
    a = ap.parse_args()
    ds = Path(a.dataset).resolve()
    n, nt, f1 = static(ds)
    print(f"static: cases={n} identifiers_checked={nt} generic_words_skipped={sorted(GENERIC)} "
          f"failures={len(f1)}")
    for x in f1:
        print("  FAIL", x)
    f2 = []
    if a.round:
        m, f2 = runtime(ds, a.round)
        print(f"runtime: raw_outputs={m} failures={len(f2)}")
        for x in f2:
            print("  FAIL", x)
    ok = not f1 and not f2
    print("ISOLATION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
