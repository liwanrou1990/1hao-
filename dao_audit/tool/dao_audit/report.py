"""Assemble findings + static hits + dynamic evidence into JSON / Markdown."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .rules import RULES, SEVERITY_ORDER, rule_by_id
from .static_scan import StaticHit


def build_findings(static_hits: List[StaticHit], suite_summary: Dict[str, Dict]) -> List[dict]:
    """One finding per rule, enriched with locations and test evidence."""
    by_rule: Dict[str, List[StaticHit]] = {}
    for h in static_hits:
        by_rule.setdefault(h.rule_id, []).append(h)

    findings = []
    for rule in RULES:
        hits = by_rule.get(rule.id, [])
        # Only count hits in the vulnerable target files as vuln locations;
        # a hit sitting next to a mitigation (fixed/*) is reported as mitigated.
        # Findings are scoped to the audit subject: the authorized vulnerable
        # targets. Hits in fixed/ (remediation references) are excluded here.
        _seen = set()
        vuln_locations = []
        for h in hits:
            if "/targets/" not in h.file:
                continue
            key = (h.file, h.line)
            if key in _seen:
                continue
            _seen.add(key)
            vuln_locations.append(
                {"file": h.file, "line": h.line, "snippet": h.snippet, "reason": h.reason}
            )
        poc = suite_summary.get(rule.poc_test, {})
        fix = suite_summary.get(rule.fix_test, {})
        poc_passed = bool(poc) and all(poc.values())
        fix_passed = bool(fix) and all(fix.values())

        if poc_passed:
            status = "CONFIRMED"   # exploit reproduced on local chain
        elif vuln_locations:
            status = "SUSPECTED"   # static hit, no dynamic confirmation
        else:
            status = "NOT_OBSERVED"

        findings.append({
            "id": rule.id,
            "title": rule.title,
            "category": rule.category,
            "severity": rule.severity,
            "status": status,
            "swc": rule.swc,
            "cwe": rule.cwe,
            "description": rule.description,
            "locations": vuln_locations,
            "evidence": {
                "poc_test": rule.poc_test,
                "poc_results": poc,
                "poc_passed": poc_passed,
                "fix_test": rule.fix_test,
                "fix_results": fix,
                "fix_verified": fix_passed,
            },
            "remediation": rule.remediation,
        })
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9),
                                 0 if f["status"] == "CONFIRMED" else 1))
    return findings


def to_json(findings: List[dict], meta: dict) -> str:
    return json.dumps({"meta": meta, "findings": findings}, ensure_ascii=False, indent=2)


_BADGE = {"CONFIRMED": "🔴 已确认", "SUSPECTED": "🟡 疑似", "NOT_OBSERVED": "⚪ 未观测",
          "Critical": "严重", "High": "高", "Medium": "中", "Low": "低"}


def to_markdown(findings: List[dict], meta: dict) -> str:
    n_conf = sum(1 for f in findings if f["status"] == "CONFIRMED")
    n_fixed = sum(1 for f in findings if f["evidence"]["fix_verified"])
    lines = []
    lines.append("# X100 DAO 治理合约安全审计报告")
    lines.append("")
    lines.append("> 防御性安全研究。仅审计授权合约；全部漏洞在**本地测试链 + 模拟金库**上复现；"
                 "不接触真实资产，不向主网发送交易。")
    lines.append("")
    lines.append(f"- 审计工程: `{meta.get('project')}`")
    lines.append(f"- 生成时间: {meta.get('generated_at')}")
    lines.append(f"- 授权委托: {meta.get('authorized_by')}")
    lines.append(f"- 本地链: {meta.get('chain')}")
    lines.append(f"- 结论: **{n_conf}/{len(findings)} 项已确认**，其中 **{n_fixed} 项已验证修复有效**。")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| ID | 风险 | 严重程度 | 状态 | PoC | 修复验证 |")
    lines.append("|----|------|----------|------|-----|----------|")
    for f in findings:
        lines.append("| {id} | {title} | {sev} | {st} | {poc} | {fix} |".format(
            id=f["id"], title=f["title"].split(" / ")[0],
            sev=_BADGE.get(f["severity"], f["severity"]),
            st=_BADGE.get(f["status"], f["status"]),
            poc="✅ 复现" if f["evidence"]["poc_passed"] else "—",
            fix="✅ 已验证" if f["evidence"]["fix_verified"] else "—",
        ))
    lines.append("")
    for f in findings:
        lines.append(f"## {f['id']} — {f['title']}")
        lines.append("")
        lines.append(f"- **严重程度**: {_BADGE.get(f['severity'])} / {f['severity']}")
        lines.append(f"- **状态**: {_BADGE.get(f['status'])}")
        lines.append(f"- **分类**: {f['category']}  ·  **SWC**: {f['swc']}  ·  **CWE**: {f['cwe']}")
        lines.append("")
        lines.append(f"**说明**: {f['description']}")
        lines.append("")
        if f["locations"]:
            lines.append("**漏洞位置**:")
            for loc in f["locations"]:
                lines.append(f"- `{loc['file']}:{loc['line']}` — {loc['reason']}")
                lines.append(f"  ```solidity\n  {loc['snippet']}\n  ```")
            lines.append("")
        ev = f["evidence"]
        lines.append("**测试证据 (本地链)**:")
        lines.append(f"- PoC `{ev['poc_test']}`: " +
                     ("✅ 漏洞在本地链被复现" if ev["poc_passed"] else "未复现") +
                     f"  — {_fmt_results(ev['poc_results'])}")
        lines.append(f"- 修复回归 `{ev['fix_test']}`: " +
                     ("✅ 同一攻击对修复版失败，修复有效" if ev["fix_verified"] else "未验证") +
                     f"  — {_fmt_results(ev['fix_results'])}")
        lines.append("")
        lines.append(f"**修复建议**: {f['remediation']}")
        lines.append("")
    lines.append("---")
    lines.append("_本报告由 dao_audit 自动生成。靶标合约为授权研究用途，禁止部署到主网。_")
    return "\n".join(lines)


def _fmt_results(results: Dict[str, bool]) -> str:
    if not results:
        return "未运行"
    return ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())


def write_reports(out_dir: Path, findings: List[dict], meta: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "x100_audit_report.json"
    mp = out_dir / "x100_audit_report.md"
    jp.write_text(to_json(findings, meta), encoding="utf-8")
    mp.write_text(to_markdown(findings, meta), encoding="utf-8")
    return {"json": str(jp), "markdown": str(mp)}
