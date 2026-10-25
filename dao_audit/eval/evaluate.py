#!/usr/bin/env python3
"""Independent evaluator: the ONLY component that reads the answers. It scores
the blind raw outputs (eval/results/<round>/raw/) against eval/answers/, and
computes per-class and overall Precision / Recall / FPR / FNR with Wilson 95%
confidence intervals, plus the full governance-risk-chain identification rate.

Run ONLY after all blind outputs exist. It never changes the engine or the raw
outputs; it writes eval/results/<round>/scored.json and a markdown summary.

Answer schema (eval/answers/<case>.json):
  {
    "case": "<id>", "project": "<project>",
    "kind": "vulnerable" | "fixed" | "secure_control",
    "is_chain_case": true/false,
    "truth": [ {"vuln":"VULN-2","contract":"X","function":"vote",
                "line":[100,160],"treasury_risk":false,
                "governance_relation":"..."} , ... ],
    "evidence": {...}
  }
For fixed / secure_control cases, truth is [] and the case measures false positives.
"""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANSWERS = HERE / "answers"
CLASSES = ["VULN-1", "VULN-2", "VULN-3", "VULN-4", "VULN-5"]
GOV_CLASSES = ["VULN-1", "VULN-2", "VULN-3", "VULN-4"]


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score 95% CI for a proportion k/n. Returns (point, lo, hi)."""
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (round(p, 4), round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def _norm(s):
    return (s or "").strip().lower()


def _located(finding, truth_item):
    """A finding LOCATES a truth item per the frozen PREREGISTRATION §3 rule:
    same VULN class AND same contract AND (function matches OR line within the
    expected range ±5). This is the stricter 'contract AND (func OR line)' form
    (not merely any-of), so the engine is not flattered."""
    if _norm(finding.get("rule_id")) != _norm(truth_item["vuln"]):
        return False
    if _norm(finding.get("contract")) != _norm(truth_item.get("contract")):
        return False
    line = finding.get("line")
    rng = truth_item.get("line")
    line_ok = bool(rng and line is not None and (rng[0] - 5) <= line <= (rng[1] + 5))
    func_ok = bool(truth_item.get("function") and
                   _norm(finding.get("function")) == _norm(truth_item["function"]))
    return line_ok or func_ok


def score_case(raw: dict, ans: dict) -> dict:
    findings = raw.get("findings", [])
    kind = ans.get("kind", "vulnerable")
    truth = ans.get("truth", [])
    res = {"case": ans["case"], "project": ans.get("project"), "kind": kind,
           "is_chain_case": bool(ans.get("is_chain_case")),
           "n_findings": len(findings), "per_class": {}, "located_classes": [],
           "fp_findings": [], "auto_found": len(findings) > 0}

    matched_findings = set()
    # True positives: each truth item located by some finding
    for ti in truth:
        hit = None
        for i, f in enumerate(findings):
            if _located(f, ti):
                hit = i
                matched_findings.add(i)
                break
        c = ti["vuln"]
        pc = res["per_class"].setdefault(c, {"tp": 0, "fn": 0, "fp": 0})
        if hit is not None:
            pc["tp"] += 1
            if c not in res["located_classes"]:
                res["located_classes"].append(c)
        else:
            pc["fn"] += 1

    # False positives: findings not matching any truth item.
    # On safe/fixed cases EVERY governance finding is an FP; on vulnerable cases a
    # finding that located no truth item (wrong class or wrong place) is an FP.
    high = {"critical", "high"}
    res["safe_fp_highsev"] = 0
    for i, f in enumerate(findings):
        if i in matched_findings:
            continue
        c = f.get("rule_id")
        res["per_class"].setdefault(c, {"tp": 0, "fn": 0, "fp": 0})["fp"] += 1
        res["fp_findings"].append({"rule_id": c, "contract": f.get("contract"),
                                   "function": f.get("function"), "line": f.get("line"),
                                   "severity": f.get("severity")})
        if kind in ("fixed", "secure_control") and _norm(f.get("severity")) in high \
                and c in GOV_CLASSES:
            res["safe_fp_highsev"] += 1

    # chain identification: >=2 distinct located gov classes incl a treasury-risk class
    treasury_truth_classes = {ti["vuln"] for ti in truth if ti.get("treasury_risk")}
    located_gov = [c for c in res["located_classes"] if c in GOV_CLASSES]
    res["chain_identified"] = bool(
        res["is_chain_case"] and len(set(located_gov)) >= 2 and
        (treasury_truth_classes & set(located_gov)))
    return res


def aggregate(cases: list) -> dict:
    tp = {c: 0 for c in CLASSES}
    fn = {c: 0 for c in CLASSES}
    fp = {c: 0 for c in CLASSES}
    for r in cases:
        for c, pc in r["per_class"].items():
            if c not in tp:
                tp[c] = fn[c] = fp[c] = 0
            tp[c] += pc["tp"]; fn[c] += pc["fn"]; fp[c] += pc["fp"]
    per_class = {}
    for c in tp:
        t, f_, p_ = tp[c], fn[c], fp[c]
        per_class[c] = {
            "tp": t, "fn": f_, "fp": p_,
            "recall": wilson(t, t + f_), "precision": wilson(t, t + p_),
        }
    TP, FN, FP = sum(tp.values()), sum(fn.values()), sum(fp.values())
    overall = {
        "tp": TP, "fn": FN, "fp": FP,
        "recall": wilson(TP, TP + FN), "precision": wilson(TP, TP + FP),
        "fnr": wilson(FN, TP + FN),
    }
    # FPR on safe/fixed controls: fraction of safe cases with >=1 high-sev gov FP
    safe = [r for r in cases if r["kind"] in ("fixed", "secure_control")]
    safe_fp_cases = sum(1 for r in safe if r.get("safe_fp_highsev", 0) > 0)
    fpr = wilson(safe_fp_cases, len(safe)) if safe else (None, None, None)
    # chain rate
    chain_cases = [r for r in cases if r["is_chain_case"] and r["kind"] == "vulnerable"]
    chain_ok = sum(1 for r in chain_cases if r.get("chain_identified"))
    chain_rate = wilson(chain_ok, len(chain_cases)) if chain_cases else (None, None, None)
    return {"per_class": per_class, "overall": overall,
            "safe_cases": len(safe), "safe_fp_cases": safe_fp_cases, "fpr": fpr,
            "chain_cases": len(chain_cases), "chain_identified": chain_ok,
            "chain_rate": chain_rate}


def verdict(agg: dict, n_vuln: int, n_chain: int) -> dict:
    rec = agg["overall"]["recall"]
    fpr = agg["fpr"]
    chain = agg["chain_rate"]
    conclusive = n_vuln >= 10 and n_chain >= 3
    crit = {
        "recall>=0.70 & CIlo>=0.50": bool(rec[0] is not None and rec[0] >= 0.70 and rec[1] >= 0.50),
        "chain_rate>=0.50": bool(chain[0] is not None and chain[0] >= 0.50),
        "fpr<=0.30": bool(fpr[0] is not None and fpr[0] <= 0.30),
    }
    n_met = sum(crit.values())
    label = "PASS" if n_met == 3 else ("PARTIAL" if n_met >= 1 else "FAIL/UNPROVEN")
    return {"conclusive": conclusive, "criteria": crit, "n_criteria_met": n_met,
            "label": label,
            "note": ("结论性要求 N_vuln>=10 且链式案例>=3；否则标注为探索性(样本不足)，"
                     "只报实测数字，不宣称普适能力。") if not conclusive else
                    "样本量满足结论性阈值。"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="round1")
    a = ap.parse_args()
    raw_dir = HERE / "results" / a.round / "raw"
    if not raw_dir.exists():
        print(f"no raw outputs at {raw_dir}; run blind_run.py first", file=sys.stderr)
        return 1
    cases = []
    for raw_f in sorted(raw_dir.glob("*.json")):
        ans_f = ANSWERS / raw_f.name
        if not ans_f.exists():
            print(f"WARN no answer for {raw_f.name}; skipping", file=sys.stderr)
            continue
        cases.append(score_case(json.loads(raw_f.read_text()), json.loads(ans_f.read_text())))
    n_vuln = sum(1 for r in cases if r["kind"] == "vulnerable")
    n_chain = sum(1 for r in cases if r["is_chain_case"] and r["kind"] == "vulnerable")
    agg = aggregate(cases)
    vd = verdict(agg, n_vuln, n_chain)
    out = {"round": a.round, "n_cases": len(cases), "n_vulnerable": n_vuln,
           "n_safe_control": agg["safe_cases"], "n_chain_cases": n_chain,
           "aggregate": agg, "verdict": vd, "per_case": cases}
    out_dir = HERE / "results" / a.round
    (out_dir / "scored.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"cases={len(cases)} vulnerable={n_vuln} safe={agg['safe_cases']} chain={n_chain}")
    print(f"overall recall={agg['overall']['recall']} precision={agg['overall']['precision']}")
    print(f"FPR(safe)={agg['fpr']} chain_rate={agg['chain_rate']}")
    print(f"verdict={vd['label']} conclusive={vd['conclusive']} criteria={vd['criteria']}")
    print(f"scored -> {out_dir/'scored.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
