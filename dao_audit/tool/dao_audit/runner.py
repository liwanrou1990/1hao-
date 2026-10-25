"""Dynamic evidence: run Foundry PoC + regression suites on the LOCAL EVM.

forge's default test EVM is an in-process local chain (no network). We also
support an explicit local anvil RPC, but the authorization layer refuses any
non-local endpoint, so attack transactions can never reach a public network.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Dict

from .authorization import Authorization


class ForgeNotFound(Exception):
    pass


def _forge_bin() -> str:
    from shutil import which
    f = which("forge")
    if not f:
        raise ForgeNotFound("找不到 forge，请先安装 Foundry 工具链。")
    return f


def run_forge_json(project_root: Path, auth: Authorization,
                   match_contract: str | None = None,
                   rpc_url: str | None = None) -> Dict:
    """Run `forge test --json`, optionally filtered to one test contract."""
    auth.assert_local_rpc(rpc_url)
    auth.assert_no_mainnet_fork()
    cmd = [_forge_bin(), "test", "--json"]
    if match_contract:
        cmd += ["--match-contract", match_contract]
    if rpc_url:
        cmd += ["--fork-url", rpc_url]  # only reachable if local (guarded above)
    proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True)
    # forge prints the JSON object on stdout; tolerate warning lines around it.
    stdout = proc.stdout.strip()
    start = stdout.find("{")
    if start < 0:
        return {"_raw": stdout, "_stderr": proc.stderr, "_ok": False}
    try:
        data = json.loads(stdout[start:])
    except json.JSONDecodeError:
        # Find the last complete JSON object line.
        data = {}
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    pass
    return data


def summarize(forge_json: Dict) -> Dict[str, Dict]:
    """Flatten forge JSON into {TestContract: {test_name: passed_bool}}."""
    out: Dict[str, Dict] = {}
    for suite_path, suite in forge_json.items():
        if suite_path.startswith("_"):
            continue
        for test_name, res in suite.get("test_results", {}).items():
            short_fn = test_name.split("(")[0]
            contract = suite_path.split(":")[-1]
            out.setdefault(contract, {})[short_fn] = (res.get("status") == "Success")
    return out
