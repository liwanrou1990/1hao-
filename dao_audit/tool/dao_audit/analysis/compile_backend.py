"""Best-effort compiler/Slither backend (V0.14, Phase 1).

Honest capability note: this environment has Slither installed (0.11.x) but only
solc 0.8.28 (foundry's); solc 0.5/0.6/0.7 binaries are blocked (403 on
binaries.soliditylang.org and GitHub releases), and the historical dataset
contracts import external dependencies that are not vendored. Therefore Slither
/ the compiler cannot build most historical contracts. Rather than silently
treat an un-analyzable contract as safe (forbidden by Phase 1.6), this backend
returns status ANALYSIS_INCOMPLETE with the reason, and the caller records it.

When a contract DOES compile (self-contained 0.8.x), Slither findings are
returned so they can corroborate the semantic analyzer.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

_PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);")


def detect_pragma(path: Path) -> Optional[str]:
    m = _PRAGMA.search(path.read_text(errors="ignore"))
    return m.group(1).strip() if m else None


def _solc_bin() -> str:
    return shutil.which("solc") or "/root/.foundry/bin/solc"


def slither_available() -> bool:
    return shutil.which("slither") is not None


def try_analyze(sol_file) -> dict:
    """Try to compile+analyze one .sol with Slither (foundry solc). On any
    failure (version mismatch, unresolved import, compile error) return
    ANALYSIS_INCOMPLETE — never a false "clean"."""
    sol = Path(sol_file).resolve()
    pragma = detect_pragma(sol)
    if not slither_available():
        return {"status": "ANALYSIS_INCOMPLETE", "reason": "slither not installed",
                "pragma": pragma, "file": sol.name}
    # only the installed solc 0.8.x can be used; a <0.8 pragma cannot compile here
    if pragma and re.search(r"0\.[567]\.", pragma) and "0.8" not in pragma:
        return {"status": "ANALYSIS_INCOMPLETE",
                "reason": f"pragma {pragma} needs solc <0.8 (blocked: only 0.8.28 available)",
                "pragma": pragma, "file": sol.name}
    tmp = tempfile.mkdtemp(prefix="slither_")
    try:
        p = subprocess.run(
            ["slither", str(sol), "--solc", _solc_bin(), "--json", os.path.join(tmp, "o.json")],
            capture_output=True, text=True, timeout=120, cwd=tmp)
        outp = Path(tmp) / "o.json"
        if outp.exists():
            data = json.loads(outp.read_text())
            dets = data.get("results", {}).get("detectors", []) if data.get("success") else []
            return {"status": "OK", "pragma": pragma, "file": sol.name,
                    "n_detectors": len(dets),
                    "detectors": [{"check": d.get("check"), "impact": d.get("impact"),
                                   "confidence": d.get("confidence")} for d in dets]}
        reason = (p.stderr or p.stdout or "compile failed").strip().splitlines()
        return {"status": "ANALYSIS_INCOMPLETE",
                "reason": (reason[-1] if reason else "compile failed")[:200],
                "pragma": pragma, "file": sol.name}
    except subprocess.TimeoutExpired:
        return {"status": "ANALYSIS_INCOMPLETE", "reason": "slither timeout", "pragma": pragma, "file": sol.name}
    except Exception as e:
        return {"status": "ANALYSIS_INCOMPLETE", "reason": repr(e)[:200], "pragma": pragma, "file": sol.name}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def analyze_case(contracts_dir) -> dict:
    """Run the compiler backend over a case's .sol files; summarize coverage."""
    files = sorted(Path(contracts_dir).rglob("*.sol"))
    per = [try_analyze(f) for f in files]
    ok = [r for r in per if r["status"] == "OK"]
    return {"n_files": len(files), "n_compiled": len(ok),
            "n_incomplete": len(per) - len(ok),
            "coverage": round(len(ok) / len(files), 3) if files else None,
            "per_file": per}
