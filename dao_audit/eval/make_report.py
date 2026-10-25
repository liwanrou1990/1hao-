#!/usr/bin/env python3
"""Render eval/results/<round>/scored.json into a human-readable markdown report
covering the pre-registered acceptance outputs (五/六). Pure rendering — reads
only the already-produced score file; computes nothing new.

Usage: python3 eval/make_report.py --round round1
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ci(t):
    if not t or t[0] is None:
        return "n/a"
    return f"{t[0]} (95%CI {t[1]}–{t[2]})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="round1")
    a = ap.parse_args()
    sf = HERE / "results" / a.round / "scored.json"
    if not sf.exists():
        print(f"no scored.json at {sf}; run evaluate.py first")
        return 1
    s = json.loads(sf.read_text())
    agg = s["aggregate"]; vd = s["verdict"]
    L = [f"# 盲测结果报告 ({a.round}) — 真实历史合约 DAO 治理漏洞检测能力", "",
         "> 引擎冻结后盲测，答案由独立评分器 evaluate.py 事后比对。见 eval/PREREGISTRATION.md（通过标准先于结果写定）。",
         "> 仅基于真实历史样本与独立评分；不以模拟合约成功率作为真实世界能力证明。", "",
         f"- 案例数: {s['n_cases']}（漏洞样本 {s['n_vulnerable']}，安全对照 {s['n_safe_control']}，"
         f"完整风险链案例 {s['n_chain_cases']}）",
         f"- **结论性**: {vd['conclusive']}  —  {vd['note']}", ""]
    L += ["## 一、总体指标（定位正确的 TP 口径，含 Wilson 95% CI）", "",
          f"- Recall（召回）: {ci(agg['overall']['recall'])}  FNR: {ci(agg['overall']['fnr'])}",
          f"- Precision（精确率）: {ci(agg['overall']['precision'])}",
          f"- 安全对照 FPR: {ci(agg['fpr'])}（{agg['safe_fp_cases']}/{agg['safe_cases']} 安全样本出现高危治理误报）",
          f"- 完整治理风险链识别率: {ci(agg['chain_rate'])}（{agg['chain_identified']}/{agg['chain_cases']}）", ""]
    L += ["## 二、各治理漏洞类别识别率", "",
          "| 类别 | TP | FN | FP | Recall(95%CI) | Precision(95%CI) |",
          "|------|----|----|----|---------------|------------------|"]
    names = {"VULN-1": "席位/初始化接管", "VULN-2": "投票权异常", "VULN-3": "提案执行/Timelock",
             "VULN-4": "金库资金控制", "VULN-5": "预言机操纵"}
    for c, pc in agg["per_class"].items():
        L.append(f"| {c} {names.get(c,'')} | {pc['tp']} | {pc['fn']} | {pc['fp']} | "
                 f"{ci(pc['recall'])} | {ci(pc['precision'])} |")
    L += ["", "## 三、逐案结果", "",
          "| 案例 | 项目 | 类型 | 自动发现 | 命中类别 | 链识别 | 高危误报(安全样本) |",
          "|------|------|------|----------|----------|--------|--------------------|"]
    for r in s["per_case"]:
        L.append(f"| {r['case']} | {r['project']} | {r['kind']} | {'是' if r['auto_found'] else '否'} | "
                 f"{','.join(r['located_classes']) or '—'} | {'是' if r.get('chain_identified') else '—'} | "
                 f"{r.get('safe_fp_highsev', 0)} |")
    L += ["", "## 四、判定（对照预注册冻结标准）", "",
          f"- 冻结标准满足项: {vd['n_criteria_met']}/3 — {vd['criteria']}",
          f"- **判定: {vd['label']}**", ""]
    if not vd["conclusive"]:
        L.append("> 样本量未达结论性阈值（N_vuln≥10 且链式≥3）→ 结果为**探索性**，只报实测数字，"
                 "不宣称系统具备或不具备普适的真实世界治理漏洞检测能力。")
    L += ["", "## 五、漏报与误报（技术原因，供第二轮改进）", ""]
    for r in s["per_case"]:
        if r["kind"] == "vulnerable":
            missed = [c for c, pc in r["per_class"].items() if pc["fn"] > 0]
            if missed:
                L.append(f"- 漏报 {r['case']}: 未定位 {missed}（原因见 round2 改进记录）")
        if r.get("safe_fp_highsev", 0) > 0:
            L.append(f"- 误报 {r['case']}(安全样本): {r['fp_findings']}")
    (HERE / "results" / a.round / "REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print(f"report -> {HERE/'results'/a.round/'REPORT.md'}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
