"""Case build: original project @ commit -> exact historical solc -> Slither IR.

Reads only <case>/BUILD.json (repo, commit, in-repo file paths; no labels) and
<case>/contracts/. Produces a build manifest recording, per compilation unit,
the exact compiler (version string + soljson sha256 + why it was chosen), every
source file compiled with its sha256, dependency versions and where they came
from, unresolved imports, and coverage. Anything that cannot be compiled is
reported as ANALYSIS_INCOMPLETE with the reason — never treated as safe.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import project as P
from .semver import parse, satisfies_all, sort_desc
from .toolchain import available_versions, compile_standard_json, ensure_solc

MAX_ATTEMPTS = 60


class Unit:
    """One compilation unit: a compiler version + the target files it builds."""

    def __init__(self, version: str, targets: List[str]):
        self.version = version
        self.targets = targets          # source unit names (project-root relative)
        self.slither = None
        self.output: Optional[dict] = None


class CaseBuild:
    def __init__(self, case_dir: Path):
        self.case_dir = Path(case_dir)
        self.manifest: Dict = {"case": self.case_dir.name}
        self.units: List[Unit] = []
        self.root: Optional[Path] = None             # filesystem dir of project root
        self.unit_to_dataset: Dict[str, str] = {}    # source unit name -> dataset rel path
        self.bases: List[Path] = []
        self.remaps: List[str] = []

    # ------------------------------------------------------------------ helpers
    def dataset_file(self, unit_name: str) -> Optional[str]:
        return self.unit_to_dataset.get(unit_name)

    @property
    def ok(self) -> bool:
        return self.manifest.get("status") in ("OK", "PARTIAL")


def _closure(targets: List[str], bases: List[Path], remaps: List[str]):
    """Static import closure (for version selection + dependency discovery)."""
    seen: Dict[str, Path] = {}
    unresolved: List[Tuple[str, str]] = []
    packages = set()
    stack = list(targets)
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        f = next((b / u for b in bases if (b / u).is_file()), None)
        if f is None:
            unresolved.append((u, "?"))
            continue
        seen[u] = f
        for imp in P.read_imports(f.read_text(errors="ignore")):
            r = P.resolve(imp, u, bases, remaps)
            if r:
                stack.append(r[0])
                if not imp.startswith(".") and "node_modules" in str(r[1]):
                    packages.add(P.package_of(imp))
            else:
                unresolved.append((imp, u))
                if P.package_of(imp):
                    packages.add(P.package_of(imp))
    return seen, unresolved, packages


def _errors(out: dict) -> List[str]:
    return [e.get("formattedMessage") or e.get("message", "")
            for e in out.get("errors", []) if e.get("severity") == "error"]


def _std_input(targets: List[str], remaps: List[str], version: str) -> dict:
    sel = {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object",
                       "evm.deployedBytecode.sourceMap"], "": ["ast"]}}
    inp = {"language": "Solidity",
           "sources": {t: {"urls": [t]} for t in targets},
           "settings": {"outputSelection": sel, "optimizer": {"enabled": False}}}
    if remaps and parse(version) >= (0, 4, 11):
        inp["settings"]["remappings"] = remaps
    return inp


def _candidates(pragmas: List[str], configured: List[Tuple[str, str]], avail: List[str]):
    """Ordered (version, reason) candidates. Project-pinned versions first."""
    out, seen = [], set()
    for v, src in sorted(configured, key=lambda x: parse(x[0]), reverse=True):
        if satisfies_all(v, pragmas) and v not in seen:
            out.append((v, f"project_config:{src}")); seen.add(v)
    ok = [v for v in sort_desc(avail) if satisfies_all(v, pragmas)]
    # newest patch of each minor first (what solc-select/hardhat would pick),
    # then every remaining older patch
    by_minor: Dict[Tuple[int, int], List[str]] = {}
    for v in ok:
        by_minor.setdefault(parse(v)[:2], []).append(v)
    for mm in sorted(by_minor, reverse=True):
        v = by_minor[mm][0]
        if v not in seen:
            out.append((v, "pragma:newest_compiling")); seen.add(v)
    for v in ok:
        if v not in seen:
            out.append((v, "pragma:older_patch_compiling")); seen.add(v)
    return out


def build_case(case_dir, slither: bool = True) -> CaseBuild:
    cb = CaseBuild(case_dir)
    m = cb.manifest
    t0 = time.monotonic()
    bj = cb.case_dir / "BUILD.json"
    if not bj.exists():
        m.update(status="ANALYSIS_INCOMPLETE", reason="no BUILD.json (repo/commit unknown)")
        return cb
    spec = json.loads(bj.read_text())
    m.update(repo=spec["repo"], commit=spec["commit"], mode="original_project_snapshot")
    try:
        repo = P.ensure_clone(spec["repo"])
        proot, cfg = P.project_root(repo, spec["commit"], spec.get("repo_root_hint", ""))
        snap = P.snapshot(repo, spec["commit"], proot)
    except Exception as e:
        m.update(status="ANALYSIS_INCOMPLETE", reason=f"snapshot failed: {e}"[:300])
        return cb
    root = snap / proot if proot else snap
    cb.root = root
    m.update(project_root=proot or ".", build_config=cfg)

    # dataset file <-> source unit name, and byte-identity check
    integ = {}
    for ds_rel, repo_path in spec["files"].items():
        unit = os.path.relpath(repo_path, proot) if proot else repo_path
        cb.unit_to_dataset[unit] = ds_rel
        a = cb.case_dir / "contracts" / ds_rel
        b = snap / repo_path
        integ[ds_rel] = b.is_file() and P.sha256_file(a) == P.sha256_file(b)
    m["dataset_identical_to_snapshot"] = integ
    targets = sorted(cb.unit_to_dataset)

    # dependencies: submodules, remappings, npm packages
    m["submodules"] = P.submodules(repo, spec["commit"], snap, proot)
    remaps = P.load_remappings(snap, proot)
    have = {r.split("=")[0].split(":")[-1] for r in remaps}
    for r in P.dapp_remappings(snap, proot):
        if r.split("=")[0] not in have:
            remaps.append(r); have.add(r.split("=")[0])
    cb.remaps = remaps
    bases = [root, root / "node_modules", snap / "node_modules"]
    seen, unresolved, pkgs = _closure(targets, bases, remaps)
    deps = []
    cdate = P.commit_date(repo, spec["commit"])
    m["commit_date"] = cdate
    for pkg in sorted(pkgs):
        ver, src = P.locked_version(snap, proot, pkg)
        rec = {"package": pkg, "declared": ver, "declared_in": src}
        if ver and src.endswith("(range)"):
            exact, how = P.resolve_range_asof(pkg, ver, cdate)
            rec["resolution"] = how
            ver = exact
        if ver:
            nm, got = P.install_npm(pkg, ver)
            if nm:
                _link_dep(root, nm, pkg)
                rec.update(installed=got, status="ok")
            else:
                rec.update(status=f"install_failed: {got}")
        else:
            rec["status"] = "undeclared"
        deps.append(rec)
    cb.bases = bases
    seen, unresolved, _ = _closure(targets, bases, remaps)
    for d in deps:
        d["files_sha256"] = {u: P.sha256_file(f) for u, f in sorted(seen.items())
                             if u.startswith(d["package"] + "/")}
    m["npm_dependencies"] = deps
    m["remappings"] = remaps
    m["unresolved_imports"] = [{"import": i, "from": f} for i, f in unresolved]
    m["sources_sha256"] = {u: P.sha256_file(f) for u, f in sorted(seen.items())}

    # compiler selection per target (a target's pragma set = its import closure)
    configured = P.configured_versions(snap, proot)
    m["configured_compilers"] = [{"version": v, "file": f} for v, f in configured]
    avail = available_versions()
    groups: Dict[Tuple[str, ...], List[str]] = {}
    for t in targets:
        cl, _, _ = _closure([t], bases, remaps)
        prags = sorted({p for f in cl.values() for p in P.read_pragmas(f.read_text(errors="ignore"))})
        groups.setdefault(tuple(prags), []).append(t)
    # merge groups that share a candidate so most cases are a single unit
    unit_plan: List[Tuple[List[str], List[str]]] = []
    for prags, ts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        for up in unit_plan:
            if any(satisfies_all(v, list(up[0]) + list(prags)) for v in avail):
                up[0][:] = sorted(set(up[0]) | set(prags)); up[1].extend(ts); break
        else:
            unit_plan.append((list(prags), list(ts)))

    m["units"] = []
    compiled_targets = set()
    for prags, ts in unit_plan:
        rec = {"targets": sorted(ts), "pragmas": prags, "attempts": []}
        chosen = None
        for v, why in _candidates(prags, configured, avail)[:MAX_ATTEMPTS]:
            try:
                meta = ensure_solc(v)
            except Exception as e:
                rec["attempts"].append({"version": v, "result": f"install_failed {e}"[:200]}); continue
            out = compile_standard_json(v, _std_input(sorted(ts), remaps, v), [str(b) for b in bases])
            errs = _errors(out)
            rec["attempts"].append({"version": v, "reason": why,
                                    "result": "ok" if not errs else errs[0][:240]})
            if not errs:
                chosen = (v, why, meta, out); break
            if why.startswith("project_config"):
                continue  # pinned version failed: still try pragma candidates, recorded
        if not chosen:
            rec["status"] = "ANALYSIS_INCOMPLETE"
            m["units"].append(rec)
            continue
        v, why, meta, out = chosen
        rec.update(status="COMPILED", compiler={"version": v, "selected_by": why,
                   "version_string": meta["version_string"], "soljson_sha256": meta["soljson_sha256"],
                   "frontend": "solc-js (official emscripten build, npm solc@%s)" % v},
                   compiled_sources=sorted(out.get("sources", {})))
        u = Unit(v, sorted(ts))
        u.output = out
        if slither:
            try:
                u.slither = _slither(root, sorted(ts), meta["shim"], remaps, bases)
                rec["slither"] = {"status": "OK", "contracts": len(u.slither.contracts)}
            except Exception as e:
                rec["slither"] = {"status": "FAILED", "error": repr(e)[:300]}
        cb.units.append(u)
        compiled_targets |= set(ts)
        m["units"].append(rec)

    n_ir = sum(len(u.targets) for u in cb.units if u.slither is not None)
    m["coverage"] = {"targets": len(targets), "compiled": len(compiled_targets),
                     "slither_ir": n_ir,
                     "ratio": round(n_ir / len(targets), 3) if targets else 0.0}
    m["status"] = ("OK" if n_ir == len(targets) and targets else
                   "PARTIAL" if n_ir else "ANALYSIS_INCOMPLETE")
    if m["status"] != "OK":
        m["reason"] = "; ".join(
            f"{','.join(r['targets'][:2])}: {r['attempts'][-1]['result'] if r['attempts'] else 'no candidate compiler'}"
            for r in m["units"] if r.get("status") != "COMPILED" or r.get("slither", {}).get("status") != "OK")[:600]
    m["build_seconds"] = round(time.monotonic() - t0, 1)
    return cb


def _link_dep(root: Path, nm: Path, pkg: str) -> None:
    """Expose an installed dependency at <project>/node_modules/<pkg> (inside the
    cached snapshot) so the compiler and crytic-compile see the normal layout."""
    dst = root / "node_modules" / pkg
    if dst.is_symlink():
        if dst.resolve() == (nm / pkg).resolve():
            return
        dst.unlink()            # a link we created earlier for another version
    elif dst.exists():
        return                  # real directory shipped in the snapshot: keep it
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.symlink_to(nm / pkg, target_is_directory=True)


def _slither(root: Path, targets: List[str], shim: str, remaps: List[str], bases: List[Path]):
    from crytic_compile import CryticCompile
    from crytic_compile.platform.solc_standard_json import SolcStandardJson
    from slither import Slither
    s = SolcStandardJson(None)
    for t in targets:
        s.add_source_file(t)
    cwd = os.getcwd()
    try:
        os.chdir(root)
        cc = CryticCompile(s, solc=shim, solc_working_dir=str(root),
                           solc_remaps=remaps or None,
                           solc_env={"SOLCJS_BASES": ",".join(str(b) for b in bases)})
        return Slither(cc)
    finally:
        os.chdir(cwd)
