#!/usr/bin/env python3
"""V0.17: dev-set regression V0.16 vs V0.17, the 25-item miss plan, and the
A/B/C/D coverage counts. Automatic, review-queue and manual numbers are kept
in separate fields and never summed. Writes eval/v17/{regression,miss_plan,
abcd_coverage}.json."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))
from evaluate import ANSWERS, _located, wilson  # noqa: E402
from evaluate_v15 import score_track  # noqa: E402

DIM = {"VULN-1": "A", "VULN-2": "B", "VULN-3": "C", "VULN-4": "D"}
SETS = {"r123": ("dev16_r123", "dev17_r123"), "r4": ("dev16_r4", "dev17_r4")}


def load(rnd):
    raws, ans = {}, {}
    for f in sorted((EVAL / "results" / rnd / "raw").glob("*.json")):
        raws[f.stem] = json.loads(f.read_text()); ans[f.stem] = json.loads((ANSWERS / f.name).read_text())
    return raws, ans


def tier_stats(raws, ans, key):
    tp = fp = 0
    for c, r in raws.items():
        for x in r.get(key, []):
            if any(_located(x, t) for t in ans[c].get("truth", [])):
                tp += 1
            else:
                fp += 1
    return tp, fp


def regression():
    out = {}
    for name, (v16, v17) in SETS.items():
        for ver, rnd in (("V0.16", v16), ("V0.17", v17)):
            raws, ans = load(rnd)
            s = score_track(raws, ans, "primary")
            o = s["aggregate"]["overall"]
            pairs = s["fixed_discrimination"]["pairs"]
            loc_disc = 0
            for p in pairs:          # location-level: alert at a labelled site disappears in the fixed version
                vr, fr = raws[p["vulnerable"]], raws[p["fixed"]]
                for key in ("findings", "review_queue"):
                    hits = [x for x in vr.get(key, []) if any(_located(x, t) for t in ans[p["vulnerable"]]["truth"])]
                    if hits and not any((h["contract"], h["function"]) == (y["contract"], y["function"])
                                        for h in hits for y in fr.get(key, [])):
                        loc_disc += 1; break
            rq_tp, rq_fp = tier_stats(raws, ans, "review_queue")
            rf_tp, rf_fp = tier_stats(raws, ans, "refuted")
            out[f"{name}:{ver}"] = {
                "cases": len(raws), "unanalysable": sum(1 for r in raws.values() if r.get("data_status") != "OK"),
                "auto": {"tp": o["tp"], "fn": o["fn"], "fp": o["fp"], "recall": o["recall"], "precision": o["precision"]},
                "safe_cases_with_auto_alert": f"{s['safe_fpr']['with_gov_finding']}/{s['safe_fpr']['safe_cases']}",
                "safe_fpr": s["safe_fpr"]["rate"],
                "fixed_discrimination_strict": f"{s['fixed_discrimination']['discriminated']}/{len(pairs)}",
                "fixed_discrimination_location_level": f"{loc_disc}/{len(pairs)}",
                "review_queue": {"tp": rq_tp, "fp": rq_fp}, "refuted": {"tp": rf_tp, "fp": rf_fp},
            }
    return out


PLAN = {
    "NO_RULE": ("no Slither rule", "evaluate a mature external static-analysis ruleset for the specific pattern on the dev item; "
                "accept only if it fires on the vulnerable item AND is silent on the fixed version and on the secure controls; "
                "otherwise keep NOT_COVERED and add the pattern to the manual checklist"),
    "PARSE_FAIL": ("code not analysable", "toolchain fix + regression; dependencies must be verifiable from the repository at the "
                   "commit, never guessed; afterwards re-classify the item by what the analysis then shows"),
    "SEMANTICS_UNDETERMINED": ("permission semantics undetermined", "manual review with the V0.17 evidence record (entry points, "
                               "msg.sender conditions, signature checks); an automatic verified-semantics module is not available "
                               "(its development was stopped by the platform safety check) -> stays manual"),
    "INSUFFICIENT_EVIDENCE": ("evidence insufficient", "keep in the manual review queue with the evidence record; a reviewer conclusion "
                              "(with code location) is required before it counts anywhere, and only in the manual layer"),
}
PATTERN = {
    ("bancor_network", "safeTransferFrom"): "public helper issuing transferFrom via encoded low-level call",
    ("bancor_network", "safeTransfer"): "public helper moving the contract's own tokens",
    ("bancor_network", "safeApprove"): "public helper setting allowances of the contract's tokens",
    ("daostack_ucontroller", "newOrganization"): "check-then-use of values re-read from a caller-supplied contract",
    ("lifi_cbridge_facet", "swapAndStartBridgeTokensViaCBridge"): "arbitrary low-level call target/calldata from caller (entry)",
    ("lifi_cbridge_facet", "swap"): "arbitrary low-level call target/calldata from caller (library)",
    ("llama_relative_quorum", "validateActionCreation"): "unrestricted overwrite of quorum snapshot (B)",
    ("moonwell_temporal_governor", "fastTrackProposalExecution"): "emergency execution path without pause/delay condition (C)",
    ("nomad_replica", "initialize"): "trusted default/zero value pre-approved at initialisation",
    ("nomad_replica", "process"): "message acceptance of default root value",
    ("party_veto_proposal", "voteToVeto"): "missing per-voter record (double counting, B)",
    ("pickle_controller", "swapExactJarForJar"): "unvalidated contract parameters used as call targets",
}


def miss_plan():
    fa = json.loads((EVAL / "v16" / "round4_failure_analysis.json").read_text())
    r17, a17 = load("dev17_r4")
    items = []
    for m in fa["missed"]:
        raw = r17[m["case"]]
        t = next(t for t in a17[m["case"]]["truth"] if t["function"] == m["function"] and t["vuln"] == m["vuln"])
        where = [k for k in ("findings", "review_queue", "refuted", "heuristic_findings")
                 if any(_located(x, t) for x in raw.get(k, []))]
        status = ("AUTO_DETECTED" if "findings" in where else "IN_REVIEW_QUEUE" if "review_queue" in where
                  else "HEURISTIC_SIGNAL_ONLY" if "heuristic_findings" in where
                  else "ANALYSIS_INCOMPLETE" if raw.get("data_status") != "OK" else "NOT_DETECTED")
        title, plan = PLAN[m["category"]]
        items.append({"case": m["case"], "vuln": m["vuln"], "dimension": DIM[m["vuln"]],
                      "function": f"{m['contract']}.{m['function']}", "lines": m["lines"],
                      "v16_category": m["category"], "category_title": title, "root_cause": m["reason"],
                      "pattern": PATTERN.get((m["case"], m["function"])),
                      "handling_plan": plan, "v17_status": status,
                      "build_status_v17": raw.get("data_status"),
                      "source_type": m.get("source_type"), "source_certainty": m.get("source_certainty")})
    by = {}
    for i in items:
        b = by.setdefault(i["v16_category"], {})
        b[i["v17_status"]] = b.get(i["v17_status"], 0) + 1
    return {"n": len(items), "by_category_v17_status": by, "items": items}


def abcd():
    rq = json.loads((HERE / "review_queue.json").read_text())
    gov = json.loads((EVAL / "v16" / "governance_check_report.json").read_text())
    res = {d: {"truth_items": 0, "auto_detected_v16": 0, "auto_detected_v17": 0, "in_review_queue_v17": 0,
               "review_labeled_defect": 0, "manual_cells_defect_r4": 0, "manual_cells_protected_r4": 0,
               "uncovered_by_tools": d in ("B", "C")} for d in "ABCD"}
    for name, (v16, v17) in SETS.items():
        r16, a = load(v16); r17, _ = load(v17)
        for c, an in a.items():
            for t in an.get("truth", []):
                d = DIM[t["vuln"]]
                res[d]["truth_items"] += 1
                res[d]["auto_detected_v16"] += any(_located(x, t) for x in r16[c].get("findings", []))
                res[d]["auto_detected_v17"] += any(_located(x, t) for x in r17[c].get("findings", []))
                res[d]["in_review_queue_v17"] += any(_located(x, t) for x in r17[c].get("review_queue", []))
    for i in rq["items"]:
        if i["review_conclusion"] == "LABELED_DEFECT" and i["historical_label"]:
            for l in i["historical_label"]:
                res[DIM[l["vuln"]]]["review_labeled_defect"] += 1
    for row in gov["cases"]:
        for d, cell in row["dims"].items():
            st = cell["manual"]["status"]
            if st == "EVIDENCE_SUFFICIENT_DEFECT":
                res[d]["manual_cells_defect_r4"] += 1
            elif st == "EVIDENCE_SUFFICIENT_PROTECTED":
                res[d]["manual_cells_protected_r4"] += 1
    for d in res.values():
        n = d["truth_items"]
        d["auto_recall_v16"] = wilson(d["auto_detected_v16"], n)
        d["auto_recall_v17"] = wilson(d["auto_detected_v17"], n)
    return res


def main():
    reg = regression()
    (HERE / "regression.json").write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    mp = miss_plan()
    (HERE / "miss_plan.json").write_text(json.dumps(mp, ensure_ascii=False, indent=2))
    cov = abcd()
    (HERE / "abcd_coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=2))
    for k, v in reg.items():
        print(k, json.dumps({x: v[x] for x in ("unanalysable", "auto", "safe_cases_with_auto_alert",
                                               "fixed_discrimination_strict", "fixed_discrimination_location_level",
                                               "review_queue", "refuted")}))
    print("miss plan", json.dumps(mp["by_category_v17_status"]))
    for d, v in cov.items():
        print(d, {k: v[k] for k in ("truth_items", "auto_detected_v16", "auto_detected_v17", "in_review_queue_v17",
                                    "review_labeled_defect", "manual_cells_defect_r4", "manual_cells_protected_r4",
                                    "uncovered_by_tools")})


if __name__ == "__main__":
    main()
