"""Rebuild a case from the original project at its recorded commit.

* snapshot   - `git archive <commit>` of the project subtree (no history, so no
               later fix commits / messages can leak into the analysis)
* config     - compiler versions the project itself pins (truffle / hardhat /
               foundry / dapp / brownie / package.json "solc")
* deps       - npm packages at the version recorded by the project's lockfile
               (else its package.json range), and git submodules at their gitlink
               commits
* remappings - project remappings.txt / foundry.toml, plus dapp-style lib/<x>/src
"""
from __future__ import annotations
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from .toolchain import CACHE

_CONFIGS = ("hardhat.config.ts", "hardhat.config.js", "truffle-config.js", "truffle.js",
            "foundry.toml", "Dappfile", ".dapprc", "buidler.config.js", "buidler.config.ts",
            "brownie-config.yaml", "dapptools.nix", "saddle.config.js")
_ROOT_FILES = ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
               ".gitmodules", "remappings.txt", "foundry.toml", "Dockerfile")
# CI / container files that pin the solc binary a project builds with
_PIN_FILES = ("Dockerfile", ".circleci/config.yml", ".travis.yml")
_IMPORT = re.compile(r"""import\s+(?:[^;'"]*?\bfrom\s+)?["']([^"']+)["']""")
_PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);")


def git(repo: Path, *args, binary=False):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {out.stderr.decode(errors='ignore')[-300:]}")
    return out.stdout if binary else out.stdout.decode(errors="ignore")


def ensure_clone(url: str) -> Path:
    slug = re.sub(r"^https?://(www\.)?github\.com/", "", url).rstrip("/")
    slug = re.sub(r"\.git$", "", slug)
    d = CACHE / "repos" / (slug.replace("/", "_") + ".git")
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:none",
                            f"https://github.com/{slug}", str(d)], capture_output=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"clone {slug}: {r.stderr.decode(errors='ignore')[-300:]}")
    return d


def _ls(repo: Path, commit: str, path: str) -> List[str]:
    try:
        args = ["ls-tree", "--name-only", commit] + ([path.rstrip("/") + "/"] if path else [])
        return git(repo, *args).split()
    except RuntimeError:
        return []


def project_root(repo: Path, commit: str, hint: str) -> Tuple[str, Optional[str]]:
    """Nearest ancestor of `hint` that holds a Solidity build config."""
    parts = PurePosixPath(hint).parts if hint else ()
    for i in range(len(parts), -1, -1):
        d = "/".join(parts[:i])
        names = {PurePosixPath(n).name for n in _ls(repo, commit, d)}
        for c in _CONFIGS:
            if c in names:
                return d, (d + "/" + c if d else c)
    return hint, None


def snapshot(repo: Path, commit: str, subtree: str) -> Path:
    dest = CACHE / "snapshots" / f"{repo.stem}@{commit[:12]}"
    marker = dest / ".dao_audit_snapshot"
    want = subtree or "."
    done = set(marker.read_text().split("\n")) if marker.exists() else set()
    if want not in done:
        dest.mkdir(parents=True, exist_ok=True)
        args = ["archive", "--format=tar", commit] + ([subtree] if subtree else [])
        tarfile.open(fileobj=io.BytesIO(git(repo, *args, binary=True))).extractall(dest, filter="data")
        # root-level manifest/lock files (monorepos hoist dependencies to the root)
        for f in _ROOT_FILES + _PIN_FILES:
            try:
                (dest / f).parent.mkdir(parents=True, exist_ok=True)
                (dest / f).write_bytes(git(repo, "show", f"{commit}:{f}", binary=True))
            except RuntimeError:
                pass
        done.add(want)
        marker.write_text("\n".join(sorted(done)))
    return dest


# ---------------------------------------------------------------- compiler config
_VER = r"(\d+\.\d+\.\d+)"


def configured_versions(snap: Path, proot: str) -> List[Tuple[str, str]]:
    """[(version, source_file)] the project pins, searched from project root up."""
    out: List[Tuple[str, str]] = []
    d = snap / proot if proot else snap
    seen = set()
    while True:
        for name in _CONFIGS + ("package.json", "Makefile") + _PIN_FILES:
            f = d / name
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            t = f.read_text(errors="ignore")
            rel = str(f.relative_to(snap))
            pats = []
            if name.startswith(("hardhat", "buidler")):
                pats = [rf"version\s*:\s*['\"]{_VER}['\"]", rf"solidity\s*:\s*['\"]{_VER}['\"]"]
            elif name.startswith("truffle"):
                # only the active (uncommented) solc.version
                t = "\n".join(l for l in t.splitlines() if not l.strip().startswith("//"))
                pats = [rf"version\s*:\s*['\"]{_VER}['\"]"]
            elif name == "foundry.toml":
                pats = [rf"solc(?:_version)?\s*=\s*['\"]{_VER}['\"]"]
            elif name in ("Dappfile", ".dapprc", "Makefile", "dapptools.nix"):
                pats = [rf"DAPP_SOLC_VERSION\s*[:?]?=\s*{_VER}", rf"solc_{_VER.replace('.', '_')}"]
            elif name in _PIN_FILES:
                pats = [rf"solidity/releases/download/v{_VER}", rf"solc-select\s+(?:install|use)\s+{_VER}",
                        rf"SOLC_VERSION[=:\s]+['\"]?{_VER}"]
            elif name == "brownie-config.yaml":
                pats = [rf"version\s*:\s*{_VER}"]
            elif name == "package.json":
                try:
                    pj = json.loads(t)
                except Exception:
                    pj = {}
                for k in ("dependencies", "devDependencies"):
                    v = (pj.get(k) or {}).get("solc")
                    if v and re.fullmatch(r"\d+\.\d+\.\d+", v):
                        out.append((v, rel))
                continue
            for p in pats:
                for m in re.finditer(p, t):
                    out.append((m.group(1), rel))
        if d == snap:
            break
        d = d.parent
    return out


# ---------------------------------------------------------------- imports / deps
def read_pragmas(text: str) -> List[str]:
    return [m.group(1).strip() for m in _PRAGMA.finditer(text)]


def read_imports(text: str) -> List[str]:
    # drop comments first so commented-out imports are not followed
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    t = re.sub(r"//[^\n]*", "", t)
    return _IMPORT.findall(t)


def load_remappings(snap: Path, proot: str) -> List[str]:
    rem: List[str] = []
    base = snap / proot if proot else snap
    f = base / "remappings.txt"
    if f.is_file():
        rem += [l.strip() for l in f.read_text().splitlines() if "=" in l and not l.startswith("#")]
    ft = base / "foundry.toml"
    if ft.is_file():
        m = re.search(r"remappings\s*=\s*\[(.*?)\]", ft.read_text(), re.S)
        if m:
            rem += re.findall(r"['\"]([^'\"]+=[^'\"]+)['\"]", m.group(1))
    return normalize_remappings(rem)


def normalize_remappings(rem: List[str]) -> List[str]:
    """Forge semantics: a directory prefix (`x/`) maps to a directory, so the
    target gets a trailing slash; duplicates of the same (context, prefix) keep
    the first occurrence (remappings.txt before foundry.toml)."""
    out, seen = [], set()
    for r in rem:
        k, _, t = r.partition("=")
        if k.endswith("/") and t and not t.endswith("/"):
            t += "/"
        if k in seen:
            continue
        seen.add(k)
        out.append(f"{k}={t}")
    return out


def apply_remap(path: str, remaps: List[str]) -> str:
    best = None
    for r in remaps:
        ctx_pre, _, tgt = r.partition("=")
        pre = ctx_pre.split(":")[-1]
        if path.startswith(pre) and (best is None or len(pre) > len(best[0])):
            best = (pre, tgt)
    return best[1] + path[len(best[0]):] if best else path


def resolve(imp: str, importer: str, bases: List[Path], remaps: List[str]) -> Optional[Tuple[str, Path]]:
    """Return (source unit name, file) the compiler would use, or None."""
    if imp.startswith("."):
        unit = str(PurePosixPath(importer).parent / imp)
        unit = _norm(unit)
    else:
        unit = apply_remap(imp, remaps)
    for b in bases:
        f = b / unit
        if f.is_file():
            return unit, f
    return None


def _norm(p: str) -> str:
    out: List[str] = []
    for s in p.split("/"):
        if s in ("", "."):
            continue
        if s == ".." and out and out[-1] != "..":
            out.pop()
        else:
            out.append(s)
    return "/".join(out)


def package_of(imp: str) -> Optional[str]:
    if imp.startswith("."):
        return None
    parts = imp.split("/")
    if parts[0].startswith("@") and len(parts) > 1:
        return parts[0] + "/" + parts[1]
    return parts[0]


def _declared(dirs: List[Path], pkg: str) -> Tuple[Optional[str], Optional[Path]]:
    for d in dirs:
        pj = d / "package.json"
        if pj.is_file():
            try:
                j = json.loads(pj.read_text())
            except Exception:
                continue
            for k in ("dependencies", "devDependencies", "peerDependencies"):
                v = (j.get(k) or {}).get(pkg)
                if v:
                    return v, pj
    return None, None


def locked_version(snap: Path, proot: str, pkg: str) -> Tuple[Optional[str], str]:
    """Version of `pkg` as the project would have installed it.
    1. the nearest package.json declaration; an exact version is used as is;
    2. otherwise the lockfile entry for THAT declared range (monorepo lockfiles
       hold several versions of one package);
    3. otherwise the declared range, resolved later as-of the commit date."""
    dirs = []
    d = snap / proot if proot else snap
    while True:
        dirs.append(d)
        if d == snap:
            break
        d = d.parent
    rng, pj = _declared(dirs, pkg)
    if rng and re.fullmatch(r"\d+\.\d+\.\d+", rng.strip()):
        return rng.strip(), str(pj.relative_to(snap)) + " (exact)"
    for d in dirs:
        yl = d / "yarn.lock"
        if rng and yl.is_file():
            txt = yl.read_text(errors="ignore")
            m = re.search(r'(?m)^[^\n]*' + re.escape(f"{pkg}@{rng}") + r'["\s,:][^\n]*\n\s+version "?([^"\n]+)"?', txt)
            if m:
                return m.group(1), str(yl.relative_to(snap))
        lf = d / "package-lock.json"
        if lf.is_file():
            try:
                j = json.loads(lf.read_text())
                pk = j.get("packages") or {}
                rel = str(pj.parent.relative_to(d)) if pj and pj.parent != d and \
                    str(pj.parent).startswith(str(d)) else ""
                v = ((pk.get(f"{rel}/node_modules/{pkg}") if rel else None) or
                     pk.get(f"node_modules/{pkg}") or {}).get("version") \
                    or ((j.get("dependencies") or {}).get(pkg) or {}).get("version")
                if v and (not rng or _sat(v, rng)):
                    return v, str(lf.relative_to(snap))
            except Exception:
                pass
        pl = d / "pnpm-lock.yaml"
        if pl.is_file():
            for m in re.finditer(r"(?m)^\s+/?" + re.escape(pkg) + r"[@/](\d+\.\d+\.\d+)", pl.read_text(errors="ignore")):
                if not rng or _sat(m.group(1), rng):
                    return m.group(1), str(pl.relative_to(snap))
    if rng:
        return rng, str(pj.relative_to(snap)) + " (range)"
    return None, "not declared"


def _sat(v: str, rng: str) -> bool:
    from .semver import satisfies
    try:
        return satisfies(v, rng)
    except Exception:
        return False


def install_npm(pkg: str, spec: str) -> Tuple[Optional[Path], str]:
    """Install pkg@spec into a shared cache dir; returns (node_modules dir, resolved version)."""
    safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", f"{pkg}@{spec}")
    prefix = CACHE / "deps" / safe
    nm = prefix / "node_modules"
    pj = nm / pkg / "package.json"
    if not pj.is_file():
        prefix.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["npm", "install", "--silent", "--no-audit", "--no-fund", "--ignore-scripts",
                            "--no-package-lock", "--legacy-peer-deps", "--prefix", str(prefix),
                            f"{pkg}@{spec}"], capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not pj.is_file():
            return None, (r.stderr or r.stdout)[-200:]
    try:
        return nm, json.loads(pj.read_text()).get("version", spec)
    except Exception:
        return nm, spec


def submodules(repo: Path, commit: str, snap: Path, proot: str, depth: int = 0) -> List[Dict]:
    """Materialise git submodules under the project root at their gitlink commits."""
    out: List[Dict] = []
    gm = None
    for cand in ([f"{proot}/.gitmodules"] if proot else []) + [".gitmodules"]:
        try:
            gm = git(repo, "show", f"{commit}:{cand}")
            break
        except RuntimeError:
            continue
    if not gm or depth > 2:
        return out
    for block in re.split(r"\[submodule", gm)[1:]:
        p = re.search(r"path\s*=\s*(\S+)", block)
        u = re.search(r"url\s*=\s*(\S+)", block)
        if not (p and u):
            continue
        path = p.group(1)
        if proot and not path.startswith(proot) and not (snap / path).parent.exists():
            continue
        try:
            line = git(repo, "ls-tree", commit, path).strip()
        except RuntimeError:
            continue
        if not line.startswith("160000"):
            continue
        sha = line.split()[2]
        dst = snap / path
        rec = {"path": path, "url": u.group(1), "commit": sha}
        try:
            if not any(dst.iterdir()) if dst.exists() else True:
                sub = ensure_clone(u.group(1))
                dst.mkdir(parents=True, exist_ok=True)
                tarfile.open(fileobj=io.BytesIO(git(sub, "archive", "--format=tar", sha, binary=True))
                             ).extractall(dst, filter="data")
                rec["nested"] = submodules(sub, sha, dst, "", depth + 1)
            rec["status"] = "ok"
        except Exception as e:
            rec["status"] = f"failed: {str(e)[:150]}"
        out.append(rec)
    return out


def dapp_remappings(snap: Path, proot: str) -> List[str]:
    """dapp/forge default: `<name>/=lib/<name>/src/` for every lib (and nested lib)."""
    base = snap / proot if proot else snap
    rem = []
    for lib in sorted(base.glob("lib/*")) + sorted(base.glob("lib/*/lib/*")):
        if lib.is_dir():
            rel = str(lib.relative_to(base))
            tgt = rel + "/src/" if (lib / "src").is_dir() else rel + "/"
            rem.append(f"{lib.name}/={tgt}")
    return rem


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def commit_date(repo: Path, commit: str) -> str:
    return git(repo, "log", "-1", "--format=%cI", commit).strip()


def resolve_range_asof(pkg: str, rng: str, date_iso: str) -> Tuple[Optional[str], str]:
    """Resolve a package.json range the way `npm install` would have on the commit
    date: newest non-prerelease release satisfying the range published on or
    before that date (prevents analysing code against later dependency versions)."""
    from datetime import datetime, timezone
    from .semver import satisfies
    if re.fullmatch(r"\d+\.\d+\.\d+", rng.strip()):
        return rng.strip(), "exact"
    f = CACHE / "npm_time" / (re.sub(r"[^A-Za-z0-9_.-]", "_", pkg) + ".json")
    if f.is_file():
        times = json.loads(f.read_text())
    else:
        r = subprocess.run(["npm", "view", pkg, "time", "--json"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None, "npm view failed"
        times = json.loads(r.stdout)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(times))
    cutoff = datetime.fromisoformat(date_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    best = None
    for v, t in times.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            continue
        pub = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if pub <= cutoff and satisfies(v, rng.replace("x", "0") if rng.strip() in ("*", "x", "latest") else rng):
            if best is None or tuple(map(int, v.split("."))) > tuple(map(int, best.split("."))):
                best = v
    return best, f"range {rng} resolved as-of {cutoff.date()}"
