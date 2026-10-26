"""Exact-version Solidity compilers via solc-js (npm package "solc").

Native solc binaries are unreachable from this environment (binaries.soliditylang.org
and GitHub release assets return 403), but the npm registry is reachable and each
npm `solc@X.Y.Z` release ships the official Emscripten build of compiler X.Y.Z
(soljson.js). That is the same compiler front-end/semantics as the native binary,
so the AST / IR derived from it is the real historical compiler output.

For every version we generate a tiny `solc` shim executable that understands the
two invocations crytic-compile makes (`--version`, `--standard-json`), so Slither
can drive the exact historical compiler.
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

CACHE = Path(os.environ.get("DAO_AUDIT_CACHE", str(Path.home() / ".cache" / "dao_audit")))
BRIDGE = Path(__file__).resolve().parents[2] / "solc_toolchain" / "solcjs_std.js"


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def available_versions(refresh: bool = False) -> List[str]:
    """Release versions published on npm (pre-releases excluded)."""
    f = CACHE / "solc_versions.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text())
    p = _run(["npm", "view", "solc", "versions", "--json"], timeout=120)
    vs = [v for v in json.loads(p.stdout) if "-" not in v] if p.returncode == 0 else []
    if vs:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(vs))
    return vs


def _pkg_dir(version: str, tag: str = "") -> Path:
    return CACHE / "solcjs" / f"v{version}{tag}" / "node_modules" / "solc"


def _install(version: str, tag: str) -> Path:
    pkg = _pkg_dir(version, tag)
    if not (pkg / "soljson.js").exists():
        prefix = pkg.parents[1]
        prefix.mkdir(parents=True, exist_ok=True)
        p = _run(["npm", "install", "--silent", "--no-audit", "--no-fund", "--ignore-scripts",
                  "--prefix", str(prefix), f"solc@{version}{tag}"], timeout=600)
        if p.returncode != 0 or not (pkg / "soljson.js").exists():
            raise RuntimeError(f"cannot install solc-js {version}{tag}: {(p.stderr or p.stdout)[-300:]}")
    return pkg


def _meta(pkg: Path, version: str, tag: str) -> Dict:
    meta_f = pkg.parent / "dao_audit_meta.json"
    if meta_f.exists():
        return json.loads(meta_f.read_text())
    vs = _run(["node", "--no-warnings", "-e",
               f"process.stdout.write(require({json.dumps(str(pkg))}).version())"], timeout=120)
    meta = {"requested": version, "npm_spec": f"solc@{version}{tag}",
            "version_string": vs.stdout.strip(),
            "soljson_sha256": hashlib.sha256((pkg / "soljson.js").read_bytes()).hexdigest()}
    meta_f.write_text(json.dumps(meta))
    return meta


def ensure_solc(version: str) -> Dict:
    """Install solc-js==version and VERIFY that the bundled compiler really is
    that version. Some npm releases are mispackaged (e.g. solc@0.8.7 ships
    compiler 0.7.5; the real one is published as 0.8.7-fixed): on a mismatch
    the "-fixed" tag is tried, and if no package matches, installation fails
    rather than silently compiling with the wrong compiler."""
    tried = []
    for tag in ("", "-fixed"):
        try:
            pkg = _install(version, tag)
        except RuntimeError as e:
            tried.append(str(e)[:120]); continue
        meta = dict(_meta(pkg, version, tag))
        actual = meta["version_string"].split("+")[0]
        if actual == version:
            meta["shim"] = str(_shim(version + tag, pkg))
            return meta
        tried.append(f"solc@{version}{tag} contains compiler {meta['version_string']}")
    raise RuntimeError(f"no npm package provides solc {version}: " + "; ".join(tried))


def _shim(version: str, pkg: Path) -> Path:
    d = CACHE / "shims"
    d.mkdir(parents=True, exist_ok=True)
    s = d / f"solc-{version}"
    version = version.replace("-fixed", "")
    body = f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "solc, the solidity compiler commandline interface"
  echo "Version: {version}+commit.solcjs"
  exit 0
fi
AP=.
while [ $# -gt 0 ]; do case "$1" in --allow-paths) AP="$2"; shift 2;; *) shift;; esac; done
exec node --no-warnings --max-old-space-size=4096 "{BRIDGE}" "{pkg}" "${{SOLCJS_BASES:-$AP}}"
"""
    if not s.exists() or s.read_text() != body:
        s.write_text(body)
        s.chmod(0o755)
    return s


def compile_standard_json(version: str, std_input: dict, bases: List[str],
                          timeout: int = 900) -> dict:
    """Run the exact compiler on a standard-JSON input; returns parsed output."""
    meta = ensure_solc(version)
    env = dict(os.environ, SOLCJS_BASES=",".join(bases))
    p = subprocess.run([meta["shim"], "--standard-json"], input=json.dumps(std_input),
                       capture_output=True, text=True, timeout=timeout, env=env)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"errors": [{"severity": "error", "type": "CompilerCrash", "component": "general",
                            "message": (p.stderr or p.stdout or "no output")[-400:],
                            "formattedMessage": (p.stderr or p.stdout or "no output")[-400:]}]}
