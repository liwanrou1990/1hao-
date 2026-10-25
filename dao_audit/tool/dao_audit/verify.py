"""Independent, clean-room re-verification of the vault fund-flow result.

Rebuilds from scratch (forge clean + build), runs the full suite plus the
fund-flow and independent-verification contracts, and captures reproducible
evidence: raw forge JSON, full -vvvv traces, the executed assertion lines, and
SHA256 checksums of every artifact. Emits an overall PASS/FAIL derived only
from the real run — nothing is preset.

Local EVM / simulated assets only; never connects to a real vault or mainnet.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

from .authorization import Authorization


def _forge() -> str:
    f = which("forge")
    if not f:
        raise RuntimeError("找不到 forge；请安装 Foundry 工具链。")
    return f


def _sh(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([_forge(), *args], cwd=str(root), capture_output=True, text=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _parse_tests(data: dict) -> list:
    out = []
    for suite_path, suite in data.items():
        if suite_path.startswith("_"):
            continue
        for name, res in suite.get("test_results", {}).items():
            kind = res.get("kind", {})
            gas = kind["Unit"]["gas"] if isinstance(kind, dict) and "Unit" in kind else None
            out.append({
                "suite": suite_path.split(":")[-1],
                "test": name.split("(")[0],
                "status": res.get("status"),
                "gas": gas,
                "reason": res.get("reason"),
                "logs": res.get("decoded_logs", []) or [],
            })
    return out


def _json_run(root: Path, match: str | None) -> dict:
    args = ["test", "--json"]
    if match:
        args += ["--match-contract", match]
    p = _sh(root, *args)
    out = p.stdout.strip()
    i = out.find("{")
    if i < 0:
        return {"_error": p.stderr or out}
    try:
        return json.loads(out[i:])
    except json.JSONDecodeError:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {"_error": "unparseable forge json"}


def run(root: Path, auth: Authorization) -> dict:
    auth.assert_local_rpc(None)
    auth.assert_no_mainnet_fork()
    out_dir = root / "reports_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = {}
    # 1) clean-room rebuild
    clean = _sh(root, "clean")
    build = _sh(root, "build")
    steps["clean_ok"] = clean.returncode == 0
    steps["build_ok"] = build.returncode == 0

    # 2) full suite (raw JSON evidence)
    all_json = _json_run(root, None)
    (out_dir / "verify_all_tests.json").write_text(
        json.dumps(all_json, ensure_ascii=False, indent=2), encoding="utf-8")
    all_tests = _parse_tests(all_json)

    # 3) fund-flow + independent raw JSON and full traces
    ff_json = _json_run(root, "FundFlow")
    (out_dir / "verify_fundflow.json").write_text(
        json.dumps(ff_json, ensure_ascii=False, indent=2), encoding="utf-8")
    ff_tests = _parse_tests(ff_json)

    trace = _sh(root, "test", "--match-contract", "FundFlow", "-vvvv").stdout
    (out_dir / "verify_fundflow_trace.txt").write_text(trace, encoding="utf-8")

    # 4) extract executed assertion lines (real VM::assertEq calls) as evidence
    assert_lines = [ln.strip() for ln in trace.splitlines() if "VM::assert" in ln]
    (out_dir / "verify_assertions.txt").write_text("\n".join(assert_lines), encoding="utf-8")

    # 5) key Transfer event line(s) for 5e24
    transfer_lines = [ln.strip() for ln in trace.splitlines()
                      if "emit Transfer(" in ln and "5000000000000000000000000" in ln]
    (out_dir / "verify_transfer_events.txt").write_text("\n".join(transfer_lines), encoding="utf-8")

    total = len(all_tests)
    passed = sum(1 for t in all_tests if t["status"] == "Success")
    ff_total = len(ff_tests)
    ff_pass = sum(1 for t in ff_tests if t["status"] == "Success")

    verdict = (steps["clean_ok"] and steps["build_ok"]
               and total > 0 and passed == total)

    # 6) SHA256 of all evidence artifacts
    artifacts = [
        "verify_all_tests.json", "verify_fundflow.json", "verify_fundflow_trace.txt",
        "verify_assertions.txt", "verify_transfer_events.txt",
    ]
    checksums = {}
    for a in artifacts:
        p = out_dir / a
        if p.exists():
            checksums[a] = _sha256(p)
    sums = "\n".join(f"{v}  {k}" for k, v in checksums.items()) + "\n"
    (out_dir / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    checksums["SHA256SUMS.txt"] = _sha256(out_dir / "SHA256SUMS.txt")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chain": "forge in-process local EVM (no network)",
        "forge_version": _sh(root, "--version").stdout.strip().splitlines()[0] if True else "",
        "clean_ok": steps["clean_ok"],
        "build_ok": steps["build_ok"],
        "total_tests": total,
        "passed_tests": passed,
        "fundflow_tests": ff_tests,
        "fundflow_total": ff_total,
        "fundflow_passed": ff_pass,
        "transfer_event_lines": transfer_lines,
        "assertion_count": len(assert_lines),
        "checksums": checksums,
        "verdict": "PASS" if verdict else "FAIL",
    }
    (out_dir / "INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "INDEPENDENT_VERIFICATION.md").write_text(to_markdown(result), encoding="utf-8")
    result["checksums"]["INDEPENDENT_VERIFICATION.json"] = _sha256(out_dir / "INDEPENDENT_VERIFICATION.json")
    return result


def to_markdown(r: dict) -> str:
    L = ["# 独立复核报告：500 万模拟 USDC 金库资金流转", "",
         "> 从干净环境（`forge clean` + 重新编译）独立重跑。证据取自真实 Foundry 执行，"
         "附 SHA256 校验；本地 EVM + 模拟资产，绝不连接真实金库或主网。", "",
         f"- 生成时间: {r['generated_at']}",
         f"- 执行环境: {r['chain']}  ·  {r.get('forge_version','')}",
         f"- 干净重建: clean={'OK' if r['clean_ok'] else 'FAIL'}, build={'OK' if r['build_ok'] else 'FAIL'}",
         f"- 全量测试: **{r['passed_tests']}/{r['total_tests']} 通过**",
         f"- 资金流转用例: {r['fundflow_passed']}/{r['fundflow_total']} 通过",
         f"- 执行的断言数（trace 内 VM::assert*）: {r['assertion_count']}",
         f"- **总判定: {r['verdict']}**", "",
         "## 资金流转 / 独立验证用例（真实执行）", "",
         "| 合约 | 用例 | 结果 | Gas |", "|------|------|------|-----|"]
    for t in sorted(r["fundflow_tests"], key=lambda x: (x["suite"], x["test"])):
        L.append("| {s} | `{n}` | {st} | {g} |".format(
            s=t["suite"], n=t["test"],
            st="✅ PASS" if t["status"] == "Success" else f"❌ FAIL ({t['reason']})",
            g=t["gas"] if t["gas"] is not None else "-"))
    L += ["", "## VULN-1 至 4：权限影响与资金风险（逐项）", "",
          "| 漏洞 | 权限影响 | 资金风险 | 对应用例 |",
          "|------|----------|----------|----------|",
          "| VULN-1 席位劫持 | 任意地址重初始化 council → 获得治理特权席位、改写金库指针 | 间接：为后续清空铺路（控制提案/执行） | `test_01_vuln1_...` |",
          "| VULN-2 闪电贷投票 | 以实时余额计票 → 零持仓者借币即获压倒性票权 | 间接：零成本操纵提案结果 | `test_02_vuln2_...` |",
          "| VULN-3 无时间锁 | 通过即执行，无延迟/否决窗口 | 间接：恶意提案即时落地，守护者无法拦截 | `test_03_vuln3_...` |",
          "| VULN-4 金库过大权限 | 金库任意 (target,calldata) 调用，仅单角色门禁 | 直接：单笔调用转走全部 5,000,000 USDC | `test_04_vuln4_...` |",
          "| 组合链 (2+3+4) | 异常投票→即时执行→任意调用 | 直接：近零成本清空金库 5,000,000→0 | `test_05_combined_...` |",
          ""]
    L += ["", "## 关键 ERC20 Transfer 事件（5e24 原始单位）", "", "```"]
    L += (r["transfer_event_lines"] or ["(未捕获)"])
    L += ["```", "",
          "说明：USDC 模拟代币 decimals=18，5,000,000 × 10^18 = 5,000,000,000,000,000,000,000,000 "
          "= 5e24（原始最小单位）。余额与事件均以此原始单位计。", "",
          "## 证据文件与 SHA256", "", "| 文件 | SHA256 |", "|------|--------|"]
    for k, v in r["checksums"].items():
        L.append(f"| `reports_out/{k}` | `{v}` |")
    L += ["", "## 可独立复现", "", "```bash",
          "cd dao_audit && FOUNDRY_PROFILE=offline PYTHONPATH=tool \\",
          "  python3 -m dao_audit recheck    # 干净重建 + 全量测试 + 证据 + 校验",
          "# 或直接用 Foundry 核对单项：",
          "forge clean && forge build",
          "forge test --match-contract FundFlowIndependentTest -vvvv",
          "sha256sum reports_out/verify_*.{json,txt}",
          "```", "",
          "---", "_由 dao_audit verify 生成；数据取自真实 Foundry 执行，未使用预设结果。_"]
    return "\n".join(L)
