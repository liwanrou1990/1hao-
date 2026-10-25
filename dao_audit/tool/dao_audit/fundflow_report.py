"""Vault fund-flow verification report.

Runs the FundFlow Foundry suites on the LOCAL EVM and builds a report from the
ACTUAL run: pass/fail, gas, decoded console logs, and the full execution trace.
Nothing is fabricated — if a test fails, the report records the failure reason
and the overall verdict is FAIL.
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from shutil import which

from .authorization import Authorization


VULN_CONTRACT = "FundFlowX100Test"
FIXED_CONTRACT = "FundFlowX100FixedTest"


def _forge() -> str:
    f = which("forge")
    if not f:
        raise RuntimeError("找不到 forge；请安装 Foundry 工具链。")
    return f


def _run_json(root: Path, match_contract: str, rpc_url=None) -> dict:
    cmd = [_forge(), "test", "--match-contract", match_contract, "--json"]
    if rpc_url:
        cmd += ["--fork-url", rpc_url]
    p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    out = p.stdout.strip()
    start = out.find("{")
    if start < 0:
        return {"_error": p.stderr or out}
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {"_error": "无法解析 forge --json 输出"}


def _run_trace(root: Path, match_contract: str, rpc_url=None) -> str:
    cmd = [_forge(), "test", "--match-contract", match_contract, "-vvvv"]
    if rpc_url:
        cmd += ["--fork-url", rpc_url]
    p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    return p.stdout + ("\n--- stderr ---\n" + p.stderr if p.stderr.strip() else "")


def _parse_suite(data: dict) -> list:
    tests = []
    for suite_path, suite in data.items():
        if suite_path.startswith("_"):
            continue
        for name, res in suite.get("test_results", {}).items():
            kind = res.get("kind", {})
            gas = None
            if isinstance(kind, dict) and "Unit" in kind:
                gas = kind["Unit"].get("gas")
            tests.append({
                "suite": suite_path.split(":")[-1],
                "name": name.split("(")[0],
                "status": res.get("status"),
                "gas": gas,
                "reason": res.get("reason"),
                "logs": res.get("decoded_logs", []) or [],
                "duration": res.get("duration"),
            })
    tests.sort(key=lambda t: t["name"])
    return tests


def _find_log(logs, needle):
    for l in logs:
        if needle in l:
            return l
    return None


def run(project_root: Path, auth: Authorization, rpc_url=None) -> dict:
    auth.assert_local_rpc(rpc_url)
    auth.assert_no_mainnet_fork()

    vuln_json = _run_json(project_root, VULN_CONTRACT, rpc_url)
    fixed_json = _run_json(project_root, FIXED_CONTRACT, rpc_url)
    vuln = _parse_suite(vuln_json)
    fixed = _parse_suite(fixed_json)

    # Save the REAL execution traces.
    out_dir = project_root / "reports_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_vuln = _run_trace(project_root, VULN_CONTRACT, rpc_url)
    trace_fixed = _run_trace(project_root, FIXED_CONTRACT, rpc_url)
    (out_dir / "fundflow_trace_vuln.txt").write_text(trace_vuln, encoding="utf-8")
    (out_dir / "fundflow_trace_fixed.txt").write_text(trace_fixed, encoding="utf-8")

    all_pass = all(t["status"] == "Success" for t in vuln + fixed) and bool(vuln) and bool(fixed)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chain": rpc_url or "forge in-process local EVM (no network)",
        "vuln_tests": vuln,
        "fixed_tests": fixed,
        "verdict": "PASS" if all_pass else "FAIL",
        "trace_files": {
            "vuln": str(out_dir / "fundflow_trace_vuln.txt"),
            "fixed": str(out_dir / "fundflow_trace_fixed.txt"),
        },
    }


def to_markdown(r: dict) -> str:
    vuln = r["vuln_tests"]
    fixed = r["fixed_tests"]
    combined = next((t for t in vuln if "combined" in t["name"]), None)

    L = []
    L.append("# 金库资金流转验证报告 (Vault Fund-Flow Verification)")
    L.append("")
    L.append("> 防御性安全研究。全部执行于**本地隔离 EVM + 模拟 USDC**；无主网、无真实资产。")
    L.append("> 本报告的每一项结论都由下方 Foundry 测试的**真实通过/失败**与**链上状态断言**支撑；"
             "若测试失败，报告如实标注原因并判定 FAIL。")
    L.append("")
    L.append(f"- 生成时间: {r['generated_at']}")
    L.append(f"- 执行环境: {r['chain']}")
    L.append(f"- 总判定: **{r['verdict']}**")
    L.append(f"- 漏洞版用例: {sum(1 for t in vuln if t['status']=='Success')}/{len(vuln)} 通过  ·  "
             f"修复版用例: {sum(1 for t in fixed if t['status']=='Success')}/{len(fixed)} 通过")
    L.append("")

    # Acceptance criteria — mapped to the asserting test's REAL status.
    def st(name_sub):
        t = next((x for x in vuln if name_sub in x["name"]), None)
        return "✅ PASS" if (t and t["status"] == "Success") else "❌ FAIL"
    L.append("## 核心验收标准（由真实断言验证）")
    L.append("")
    L.append("| 标准 | 断言所在用例 | 结果 |")
    L.append("|------|--------------|------|")
    L.append(f"| 金库初始余额 = 5,000,000 USDC | `test_05_combined...` / `test_04_vuln4...` | {st('combined')} |")
    L.append(f"| 金库最终余额 = 0 USDC | `test_05_combined...` | {st('combined')} |")
    L.append(f"| 接收地址余额 += 5,000,000 USDC | `test_05_combined...` | {st('combined')} |")
    L.append(f"| 资产总量守恒 | `test_05_combined...` | {st('combined')} |")
    L.append(f"| 单笔金库清空 (VULN-4) | `test_04_vuln4...` | {st('vuln4')} |")
    L.append("")

    # Observed fund flow from REAL decoded logs.
    if combined:
        v_init = _find_log(combined["logs"], "vault USDC")
        L.append("## 实测资金流（来自真实 console 日志）")
        L.append("")
        L.append("```")
        for l in combined["logs"]:
            L.append(l)
        L.append("```")
        L.append("")

    # Per-vulnerability table.
    L.append("## 分漏洞影响（本地 EVM 执行）")
    L.append("")
    L.append("| 用例 | 覆盖 | 结果 | Gas |")
    L.append("|------|------|------|-----|")
    label = {
        "test_01_vuln1_seat_hijack_takes_control": "VULN-1 治理控制权失守",
        "test_02_vuln2_flashloan_reaches_quorum": "VULN-2 闪电贷异常投票",
        "test_03_vuln3_no_timelock_immediate_execution": "VULN-3 无时间锁即时执行",
        "test_04_vuln4_single_call_drains_treasury": "VULN-4 单笔金库清空",
        "test_05_combined_killchain_full_vault_drain": "组合攻击链 全额提取",
    }
    for t in vuln:
        L.append("| `{n}` | {lab} | {res} | {gas} |".format(
            n=t["name"], lab=label.get(t["name"], ""),
            res="✅ PASS" if t["status"] == "Success" else f"❌ FAIL ({t['reason']})",
            gas=t["gas"] if t["gas"] is not None else "-"))
    L.append("")

    # Fixed-version table.
    L.append("## 修复版：未经授权的资金转移被阻止")
    L.append("")
    L.append("| 用例 | 阻止层 | 结果 | Gas |")
    L.append("|------|--------|------|-----|")
    flab = {
        "test_01_fixed_flashvote_has_zero_weight": "FIX-2 快照票权→闪电贷零权重→无法达法定人数",
        "test_02_fixed_timelock_blocks_immediate_execution": "FIX-3 时间锁阻止即时执行",
        "test_03_fixed_treasury_blocks_arbitrary_drain": "FIX-4 金库拒绝任意提取",
        "test_04_fixed_combined_attack_blocked": "组合链断裂，金库保持 5,000,000",
    }
    for t in fixed:
        L.append("| `{n}` | {lab} | {res} | {gas} |".format(
            n=t["name"], lab=flab.get(t["name"], ""),
            res="✅ PASS（已阻止）" if t["status"] == "Success" else f"❌ FAIL ({t['reason']})",
            gas=t["gas"] if t["gas"] is not None else "-"))
    L.append("")
    fc = next((t for t in fixed if "combined" in t["name"]), None)
    if fc and fc["logs"]:
        L.append("修复版组合攻击后的链上状态（真实日志）：")
        L.append("```")
        L += fc["logs"]
        L.append("```")
        L.append("")

    # Gas / duration summary.
    total_gas = sum(t["gas"] or 0 for t in vuln + fixed)
    L.append("## 资源消耗")
    L.append("")
    L.append(f"- 合计 Gas（全部用例）: {total_gas}")
    L.append(f"- 组合攻击链 Gas: {combined['gas'] if combined else 'n/a'}")
    L.append("")
    L.append("## 完整交易 Trace")
    L.append("")
    L.append(f"- 漏洞版完整 trace: `{r['trace_files']['vuln']}`")
    L.append(f"- 修复版完整 trace: `{r['trace_files']['fixed']}`")
    L.append("  （含每一步调用树、Transfer 事件、gas 明细，由 `forge test -vvvv` 原样保存）")
    L.append("")
    L.append("---")
    L.append("_由 dao_audit fundflow 生成；数据取自真实 Foundry 执行结果，未人工编造。_")
    return "\n".join(L)


def write_report(project_root: Path, r: dict) -> dict:
    out_dir = project_root / "reports_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "fundflow_verification.json"
    mp = out_dir / "fundflow_verification.md"
    jp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(to_markdown(r), encoding="utf-8")
    return {"json": str(jp), "markdown": str(mp),
            "trace_vuln": r["trace_files"]["vuln"], "trace_fixed": r["trace_files"]["fixed"]}
