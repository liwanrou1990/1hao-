#!/usr/bin/env python3
"""Dataset-prep tool (NOT part of the detection engine).

For each case, write eval/dataset/<case>/BUILD.json containing ONLY build
information: repository URL, commit, and the in-repo path of every dataset
contract file. Paths are found by exact git-blob-hash match against the tree of
that commit, which also proves the dataset file is byte-identical to the
historical source. BUILD.json carries no vulnerability labels, fix notes or
project verdicts; it is what lets the compiler backend rebuild the case from the
original project (real import graph, real dependency versions).

Usage: python3 eval/tools/make_build_manifests.py [--repos DIR] [case ...]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DATASET = HERE / "dataset"

# repo + commit of the source snapshot each case was extracted from
# (copied from each case's PROVENANCE.md; commit only, no labels)
CASES = {
    "aave_governance_v2": ("aave/governance-v2", "d4e5ae0c31824b9ec9aec4e06a3ee2c3c34ae431"),
    "audius_governance": ("AudiusProject/audius-protocol", "01f924a7762659141d3b32491b81ca6e0f63c822"),
    "beanstalk_governance": ("BeanstalkFarms/Beanstalk", "e9f49910e287e7a7afaa6db8f536b7194728b0af"),
    "compound_governance": ("compound-finance/compound-protocol", "a3214f67b73310d547e00fc578e8355911c9d376"),
    "dodo_dvm": ("DODOEX/contractV2", "94ba7fbd21adddcc7ecdcea467dd8efd961098ec"),
    "dodo_dvm_fixed": ("DODOEX/contractV2", "01c544780291a5acc3e2be4980493e63065fb200"),
    "dschief_governance": ("dapphub/ds-chief", "4a833cdaeb66313da0fb16108771a52135d23a04"),
    "furucombo_proxy": ("dinngo/furucombo-contract", "e416ad1baa1ec8dd6efbce98dd465a304ad3e818"),
    "furucombo_proxy_fixed": ("dinngo/furucombo-contract", "b096f10b4081f0cbc459cea75eddfe5e81d7b2e1"),
    "nouns_dao": ("nounsDAO/nouns-monorepo", "2213eea1c15f331f786784c2621d53bdecf907e2"),
    "openzeppelin_governor": ("OpenZeppelin/openzeppelin-contracts", "11565e5f6277a320408025e649240056ba6a3919"),
    "parity_wallet": ("paritytech/parity-ethereum", "02d462e2636f1898df3e7556364260c594b112e6"),
    "polynetwork_bridge": ("polynetwork/eth-contracts", "15d84b7f34d2068d7548d5385c3d43c37a7232ba"),
    "punk_proxy_init": ("PunkFinance/punk.protocol", "6fcdda755a4b076e362d032ffe2b18850a6c208f"),
    "punk_proxy_init_fixed": ("PunkFinance/punk.protocol", "d68a58ddcb98a0641f0af44c31791ead99f4433f"),
    "tornado_governance": ("tornadocash/tornado-governance", "6464973b108102b70efafa29e55da69bfb43b7b9"),
}


def git(repo: Path, *args) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def ensure_repo(repos: Path, slug: str) -> Path:
    d = repos / (slug.replace("/", "_") + ".git")
    if not d.exists():
        subprocess.check_call(["git", "clone", "-q", "--bare", "--filter=blob:none",
                               f"https://github.com/{slug}", str(d)])
    return d


def blob_index(repo: Path, commit: str) -> dict:
    idx = {}
    for line in git(repo, "ls-tree", "-r", commit).splitlines():
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ == "blob" and path.endswith(".sol"):
            idx.setdefault(sha, []).append(path)
    return idx


def common_root(paths):
    parts = [p.split("/")[:-1] for p in paths]
    out = []
    for seg in zip(*parts):
        if all(s == seg[0] for s in seg):
            out.append(seg[0])
        else:
            break
    return "/".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default="/tmp/claude-0/repos")
    ap.add_argument("--dataset", default=str(DATASET),
                    help="dataset dir; cases not listed in CASES must contain BUILD_SOURCE.json "
                         '{"repo": "owner/name", "commit": "<sha>"}')
    ap.add_argument("cases", nargs="*")
    a = ap.parse_args()
    dataset = Path(a.dataset)
    repos = Path(a.repos); repos.mkdir(parents=True, exist_ok=True)
    names = a.cases or sorted(d.name for d in dataset.iterdir()
                              if d.is_dir() and (d / "contracts").is_dir())
    for case in names:
        src = dataset / case / "BUILD_SOURCE.json"
        if src.is_file():
            j = json.loads(src.read_text())
            slug = j["repo"].replace("https://github.com/", "").rstrip("/")
            commit = j["commit"]
        else:
            slug, commit = CASES[case]
        repo = ensure_repo(repos, slug)
        idx = blob_index(repo, commit)
        cdir = dataset / case / "contracts"
        mapping, missing = {}, []
        for f in sorted(cdir.rglob("*.sol")):
            rel = str(f.relative_to(cdir))
            sha = subprocess.check_output(["git", "hash-object", str(f)], text=True).strip()
            cands = idx.get(sha, [])
            if not cands:
                missing.append(rel)
                continue
            # prefer a candidate with the same basename, then the shortest path
            cands = sorted(cands, key=lambda p: (Path(p).name != Path(rel).name, len(p)))
            mapping[rel] = cands[0]
        manifest = {
            "repo": f"https://github.com/{slug}",
            "commit": commit,
            "files": mapping,
            "unmatched_files": missing,
            "repo_root_hint": common_root(list(mapping.values())) if mapping else "",
        }
        (dataset / case / "BUILD.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"{case}: {len(mapping)} matched, {len(missing)} unmatched {missing[:3]}")


if __name__ == "__main__":
    sys.exit(main())
