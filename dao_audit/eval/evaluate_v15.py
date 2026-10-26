#!/usr/bin/env python3
"""V0.15 independent scorer (reads answers; the detector never does).

Reuses the frozen per-case matching rule of evaluate.py (_located: same class,
same contract, function match or line within ±5) and adds, without changing
any earlier round's numbers:

  * two separate tracks, never mixed:
      primary          = raw["findings"] (Slither built-in detectors)
      with_heuristic   = findings + heuristic_findings (HEURISTIC_ONLY/INCONCLUSIVE)
  * unanalysable samples listed separately (data_status != OK), never dropped
  * fixed-version discrimination rate per (vulnerable, fixed) pair
  * ordinary permission-risk detection vs full-chain identification, separately
  * breakdown by answer source_type / source_certainty when present
  * class coverage: classes the engine cannot detect at all are reported

Usage: python3 eval/evaluate_v15.py --round round4
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import ANSWERS, GOV_CLASSES, _located, _norm, aggregate, score_case, wilson  # noqa: E402

HIGH = {"critical", "high"}


def _view(raw: dict, track: str) -> dict:
    v = dict(raw)
    fs = list(raw.get("findings", []))
    if track == "with_heuristic":
        fs += list(raw.get("heuristic_findings", []))
    v["findings"] = fs
    return v


def _pair_of(ans: dict):
    if ans.get("pair_of"):
        return ans["pair_of"]
    c = ans["case"]
    return c[: -len("_fixed")] if c.endswith("_fixed") else None


def score_track(raws: dict, answers: dict, track: str) -> dict:
    per = {c: score_case(_view(raws[c], track), answers[c]) for c in raws}
    for c, r in per.items():
        r["data_status"] = raws[c].get("data_status", "UNKNOWN")
        r["source_type"] = answers[c].get("source_type", "unspecified")
        r["source_certainty"] = answers[c].get("source_certainty", "unspecified")
        # high-severity governance findings (any severity counts for heuristic track)
        fs = _view(raws[c], track)["findings"]
        r["gov_findings"] = sum(1 for f in fs if f.get("rule_id") in GOV_CLASSES)
        r["gov_high_findings"] = sum(1 for f in fs if f.get("rule_id") in GOV_CLASSES
                                     and _norm(f.get("severity")) in HIGH)
    cases = list(per.values())
    agg = aggregate(cases)

    # ordinary detection (per vulnerable case: >=1 truth item located)
    vul = [r for r in cases if r["kind"] == "vulnerable"]
    case_hit = sum(1 for r in vul if r["located_classes"])
    # safe FPR, counting ANY governance finding for the heuristic track
    safe = [r for r in cases if r["kind"] in ("fixed", "secure_control")]
    key = "gov_high_findings" if track == "primary" else "gov_findings"
    safe_fp = sum(1 for r in safe if r[key] > 0)

    # fixed-version discrimination: vulnerable located >=1 AND fixed has no gov finding (per key)
    pairs = []
    for r in cases:
        if r["kind"] != "fixed":
            continue
        v = _pair_of(answers[r["case"]])
        if v in per:
            ok = bool(per[v]["located_classes"]) and per[r["case"]][key] == 0
            pairs.append({"vulnerable": v, "fixed": r["case"], "vuln_located": per[v]["located_classes"],
                          "fixed_gov_findings": per[r["case"]][key], "discriminated": ok})
    disc = sum(1 for p in pairs if p["discriminated"])

    # chains: located-based (frozen evaluate rule) + engine-claimed links
    chain = []
    for r in vul:
        if not r["is_chain_case"]:
            continue
        links = raws[r["case"]].get("chain_links", {}).get("detector" if track == "primary" else "heuristic", [])
        chain.append({"case": r["case"], "located_classes": r["located_classes"],
                      "chain_identified": r["chain_identified"], "engine_links": len(links),
                      "engine_link_status": "HEURISTIC_LINK" if links else "NONE"})
    false_links = [r["case"] for r in cases if not r["is_chain_case"] and
                   raws[r["case"]].get("chain_links", {}).get(
                       "detector" if track == "primary" else "heuristic")]

    by_src = {}
    for r in vul:
        k = f"{r['source_type']}/{r['source_certainty']}"
        b = by_src.setdefault(k, {"n": 0, "case_hit": 0})
        b["n"] += 1; b["case_hit"] += bool(r["located_classes"])

    return {
        "track": track, "aggregate": agg,
        "ordinary_detection": {"vulnerable_cases": len(vul), "cases_with_located_truth": case_hit,
                               "case_detection_rate": wilson(case_hit, len(vul))},
        "safe_fpr": {"safe_cases": len(safe), "with_gov_finding": safe_fp,
                     "rate": wilson(safe_fp, len(safe)), "counted": key},
        "fixed_discrimination": {"pairs": pairs, "discriminated": disc,
                                 "rate": wilson(disc, len(pairs))},
        "full_chain": {"chain_cases": chain,
                       "identified": sum(1 for x in chain if x["chain_identified"]),
                       "rate": wilson(sum(1 for x in chain if x["chain_identified"]), len(chain)),
                       "confirmed_by_engine": 0,
                       "engine_links_on_non_chain_cases": false_links},
        "by_source": by_src,
        "per_case": cases,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    a = ap.parse_args()
    raw_dir = HERE / "results" / a.round / "raw"
    raws, answers, missing = {}, {}, []
    for f in sorted(raw_dir.glob("*.json")):
        af = ANSWERS / f.name
        if not af.exists():
            missing.append(f.stem); continue
        raws[f.stem] = json.loads(f.read_text()); answers[f.stem] = json.loads(af.read_text())
    unanalysable = [{"case": c, "data_status": r.get("data_status"),
                     "reason": (r.get("build") or {}).get("reason") or r.get("error")}
                    for c, r in raws.items() if r.get("data_status") != "OK"]
    cover = {}
    for r in raws.values():
        for k, v in (r.get("not_covered") or {}).items():
            cover[k] = v
    out = {"round": a.round, "n_cases": len(raws), "missing_answers": missing,
           "unanalysable": unanalysable, "classes_not_covered": cover,
           "tracks": {t: score_track(raws, answers, t) for t in ("primary", "with_heuristic")}}
    (HERE / "results" / a.round / "scored_v15.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    for t, s in out["tracks"].items():
        ag = s["aggregate"]["overall"]
        print(f"[{t}] recall={ag['recall']} precision={ag['precision']} tp={ag['tp']} fn={ag['fn']} fp={ag['fp']}")
        print(f"   case_detection={s['ordinary_detection']['case_detection_rate']} "
              f"safeFPR={s['safe_fpr']['rate']} fixed_disc={s['fixed_discrimination']['rate']} "
              f"chain={s['full_chain']['rate']}")
        for c, pc in s["aggregate"]["per_class"].items():
            if pc["tp"] + pc["fn"] + pc["fp"]:
                print(f"   {c}: tp={pc['tp']} fn={pc['fn']} fp={pc['fp']}")
    print(f"unanalysable={len(unanalysable)} missing_answers={missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
