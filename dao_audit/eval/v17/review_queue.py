#!/usr/bin/env python3
"""V0.17 manual review queue (read-only). Builds eval/v17/review_queue.json from
the dev17 raw outputs: every alert in the INCONCLUSIVE tier with its source
location, reaching entry points, permission evidence, historical label and the
review conclusion. Labels are visible here, so conclusions are MANUAL and never
count as automatic detection. Unresolved items stay INCONCLUSIVE.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))
from evaluate import ANSWERS, _located  # noqa: E402

# (case, Contract.function) -> (conclusion, evidence). Conclusions:
#   LABELED_DEFECT            matches a historical label backed by a public report / fix;
#                             protection exists but is defeated elsewhere (recorded, not automatic)
#   FP_PROTECTION_CONFIRMED   reviewer confirmed an effective protection with code evidence
#   INCONCLUSIVE              not determined; never treated as safe
MANUAL = {
    ("parity_wallet", "WalletLibrary.kill"): ("LABELED_DEFECT", "owner-confirmation check exists (onlymanyowners); defeated because the shared library's owner set can be initialised by anyone (separate VULN-1 at initWallet)"),
    ("audius_governance", "Governance._executeTransaction"): ("LABELED_DEFECT", "guardian check exists on guardianExecuteTransaction; defeated by the re-initialisable guardian (proxy storage collision, VULN-1 at initialize)"),
    ("sushi_route_processor2", "RouteProcessor2.uniswapV3SwapCallback"): ("LABELED_DEFECT", "msg.sender == lastCalledPool check exists; lastCalledPool is set from the caller-supplied route, and `from` is decoded from pool-supplied data; fix (diff) requires from == msg.sender"),
    ("wormhole_core_impl", "Governance.upgradeImplementation"): ("LABELED_DEFECT", "reached only via a guardian-signed message (signature check present); defeated if the guardian set of the bare logic contract is initialised by anyone (VULN-1 at initialize)"),
    ("daostack_ucontroller", "Avatar.externalTokenTransferFrom"): ("FP_PROTECTION_CONFIRMED", "onlyOwner (Avatar.sol:101); owner is the Controller; _from is chosen only by the owner"),
    ("daostack_ucontroller_fixed", "Avatar.externalTokenTransferFrom"): ("FP_PROTECTION_CONFIRMED", "same code as vulnerable version: onlyOwner (Avatar.sol:101)"),
    ("bond_fixed_expiry_teller_fixed", "BondBaseTeller._handleTransfers"): ("FP_PROTECTION_CONFIRMED", "from = market owner returned by the auctioneer registry for market id_ (BondBaseTeller.sol:184-218), not a caller-chosen address"),
    ("safe_contracts", "GnosisSafe.handlePayment"): ("FP_PROTECTION_CONFIRMED", "private; called from execTransaction after checkSignatures (GnosisSafe.sol:145, refund parameters are signed) and from setup, which is guarded by a one-time threshold check"),
}


def main():
    items = []
    for rnd in ("dev17_r123", "dev17_r4"):
        for f in sorted((EVAL / "results" / rnd / "raw").glob("*.json")):
            raw = json.loads(f.read_text())
            ans = json.loads((ANSWERS / f.name).read_text())
            for a in raw.get("review_queue", []):
                ev = a.get("permission_evidence", {})
                key = (raw["case"], f"{a['contract']}.{a['function']}")
                lab = [t for t in ans.get("truth", []) if _located(a, t)]
                concl, why = MANUAL.get(key, ("INCONCLUSIVE", "not determined by review; protection evidence present but effectiveness unverified"))
                items.append({
                    "dev_set": rnd, "case": raw["case"], "case_kind": ans["kind"],
                    "detector": a["detector"], "function": key[1], "file": a.get("file"), "line": a.get("line"),
                    "evidence_status": ev.get("status"), "evidence_reason": ev.get("reason"),
                    "entries": [{"entry": e["entry"], "modifiers": e["modifiers"],
                                 "msg_sender_conditions": e["sender_conditions"][:4],
                                 "signature_checks": e["signature_checks"][:2],
                                 "self_checked_state": e["self_checked_state"]} for e in ev.get("entries", [])][:6],
                    "from_origin": ev.get("from_origin"),
                    "historical_label": [{"vuln": t["vuln"], "function": t["function"]} for t in lab] or None,
                    "source_type": ans.get("source_type"), "source_certainty": ans.get("source_certainty"),
                    "review_conclusion": concl, "review_evidence": why,
                    "isolated_confirmation": "NOT_RUN",
                })
    summ = {}
    for i in items:
        summ[i["review_conclusion"]] = summ.get(i["review_conclusion"], 0) + 1
    out = {"n_items": len(items), "summary": summ,
           "note": "manual layer; labels visible; not an automatic detection score", "items": items}
    (HERE / "review_queue.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps({"n_items": len(items), "summary": summ}))


if __name__ == "__main__":
    main()
