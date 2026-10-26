"""Compile a small Solidity source with an exact solc-js version into Slither.

Used only by unit tests of the permission engine (capability probes); real
cases are built by dao_audit.compiler.build.
"""
from __future__ import annotations
import logging
import os
import tempfile
from pathlib import Path

from dao_audit.compiler.toolchain import ensure_solc


def load_snippet(source: str, version: str = "0.8.19", name: str = "T.sol"):
    from crytic_compile import CryticCompile
    from crytic_compile.platform.solc_standard_json import SolcStandardJson
    from slither import Slither
    logging.disable(logging.CRITICAL)
    meta = ensure_solc(version)
    d = Path(tempfile.mkdtemp(prefix="perm_snip_"))
    (d / name).write_text(source)
    s = SolcStandardJson(None)
    s.add_source_file(name)
    cwd = os.getcwd()
    try:
        os.chdir(d)
        cc = CryticCompile(s, solc=meta["shim"], solc_working_dir=str(d),
                           solc_env={"SOLCJS_BASES": str(d)})
        return Slither(cc)
    finally:
        os.chdir(cwd)


def fn(sl, contract: str, name: str):
    for c in sl.contracts:
        if c.name == contract:
            for f in c.functions_and_modifiers:
                if f.name == name:
                    return f
    raise KeyError(f"{contract}.{name}")
