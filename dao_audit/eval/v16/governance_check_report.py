#!/usr/bin/env python3
"""V0.16 read-only governance check report for the 28 Round 4 (now dev) cases.

For each case and each question
  A seats/admin controlled?   B voting power uses historical state/snapshot?
  C execution behind effective timelock + authorization?   D treasury operations access-controlled?
three independently scored layers are recorded, never merged:

  automatic   from the frozen-toolchain Slither baseline (dev16_r4 rerun):
              ANALYSIS_INCOMPLETE | EVIDENCE_INSUFFICIENT (+ signal counts)
              The baseline never yields EVIDENCE_SUFFICIENT: Slither facts do not
              prove that a protection is effective.
  manual      reviewer verdict with code evidence (labels were visible, so this is
              NOT a detection score): EVIDENCE_SUFFICIENT_DEFECT |
              EVIDENCE_SUFFICIENT_PROTECTED | EVIDENCE_INSUFFICIENT | NOT_REVIEWED
  isolated    isolated-environment confirmation: none performed (NOT_RUN)
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
DIM = {"VULN-1": "A", "VULN-2": "B", "VULN-3": "C", "VULN-4": "D"}
QUESTION = {"A": "治理席位/管理权限是否受到有效控制",
            "B": "投票权是否使用正确的历史状态与快照",
            "C": "提案执行是否受有效时间锁及授权限制",
            "D": "金库敏感操作是否具有有效访问控制"}

# Manual review (reviewer saw labels and fix diffs). Evidence = file:line or diff.
MANUAL = {
    ("bancor_network", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "TokenHandler.sol:18-47 safeApprove/safeTransfer/safeTransferFrom are public; fix diff makes them internal"),
    ("bancor_network_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "fix diff: the three helpers are internal (fix_diffs/bancor_network.diff)"),
    ("bond_fixed_expiry_teller", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "redeem accepts any token_; fix adds `token_ != bondTokens[underlying][expiry]` check (fix_diffs/bond_fixed_expiry_teller.diff)"),
    ("bond_fixed_expiry_teller_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "registry check on token_ present; flagged _handleTransfers pulls from the registered market owner (BondBaseTeller.sol:184-218)"),
    ("daostack_ucontroller", "A"): ("EVIDENCE_SUFFICIENT_DEFECT", "UController.newOrganization re-reads nativeToken()/nativeReputation() from a caller-supplied avatar; fix caches them once (fix_diffs/daostack_ucontroller.diff)"),
    ("daostack_ucontroller", "D"): ("EVIDENCE_INSUFFICIENT", "sendEther is permission-gated; the defect is reachable only after the A-step takeover; Avatar.externalTokenTransferFrom is onlyOwner (Avatar.sol:101)"),
    ("daostack_ucontroller_fixed", "A"): ("EVIDENCE_SUFFICIENT_PROTECTED", "values cached before checks and writes (fix diff)"),
    ("lifi_cbridge_facet", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "LibSwap.swap: callTo.call(callData) with caller-supplied target/calldata; fix requires dexWhitelist for approveTo and callTo"),
    ("lifi_cbridge_facet_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "dexWhitelist checks in CBridgeFacet (fix diff); flagged transferFromERC20 call sites pass msg.sender"),
    ("llama_relative_quorum", "B"): ("EVIDENCE_SUFFICIENT_DEFECT", "validateActionCreation external without caller check overwrites the quorum snapshot; fix adds msg.sender == llamaCore"),
    ("llama_relative_quorum_fixed", "B"): ("EVIDENCE_SUFFICIENT_PROTECTED", "`if (msg.sender != address(llamaCore)) revert OnlyLlamaCore();` (fix diff)"),
    ("moonwell_temporal_governor", "C"): ("EVIDENCE_SUFFICIENT_DEFECT", "fastTrackProposalExecution onlyOwner skips queue checks without whenPaused; fix adds whenPaused"),
    ("moonwell_temporal_governor_fixed", "C"): ("EVIDENCE_SUFFICIENT_PROTECTED", "fast track restricted to paused state (fix diff)"),
    ("mph88_deposit_nft", "A"): ("EVIDENCE_SUFFICIENT_DEFECT", "NFT.sol:39-57 init external, no guard, calls _transferOwnership(newOwner)"),
    ("mph88_deposit_nft", "D"): ("EVIDENCE_INSUFFICIENT", "mint/burn are onlyOwner; risk follows only from the A-step"),
    ("nomad_replica", "A"): ("EVIDENCE_SUFFICIENT_DEFECT", "initialize sets confirmAt[_committedRoot]=1 unconditionally; fix skips the zero root"),
    ("nomad_replica", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "acceptableRoot accepted the default root; fix rejects LEGACY_STATUS_NONE"),
    ("nomad_replica_fixed", "A"): ("EVIDENCE_SUFFICIENT_PROTECTED", "zero root no longer pre-approved (fix diff)"),
    ("nomad_replica_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "default root rejected (fix diff)"),
    ("olympus_governance", "B"): ("EVIDENCE_INSUFFICIENT", "label: live VOTES.balanceOf in endorseProposal; no fix pair; code not independently reviewed"),
    ("olympus_governance", "C"): ("EVIDENCE_INSUFFICIENT", "label: threshold relative to totalSupply; no fix pair; code not independently reviewed"),
    ("party_veto_proposal", "B"): ("EVIDENCE_SUFFICIENT_DEFECT", "voteToVeto has no per-voter record; fix adds hasVoted mapping and AlreadyVotedError"),
    ("party_veto_proposal_fixed", "B"): ("EVIDENCE_SUFFICIENT_PROTECTED", "hasVoted check (fix diff)"),
    ("pickle_controller", "D"): ("EVIDENCE_INSUFFICIENT", "label + heuristic signal at CurveProxyLogic.add_liquidity; no fix pair in dataset"),
    ("sandbox_land", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "ERC721BaseToken._burn public; fix makes it internal"),
    ("sandbox_land_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "_burn internal (fix diff)"),
    ("sushi_route_processor2", "D"): ("EVIDENCE_SUFFICIENT_DEFECT", "callback transfers from a `from` decoded from pool-supplied data; fix requires from == msg.sender and drops `from` from callback data"),
    ("sushi_route_processor2_fixed", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "fix diff as above; remaining wrapNative alert is a router payout by design"),
    ("wormhole_core_impl", "A"): ("EVIDENCE_INSUFFICIENT", "label: initializer guard reads the implementation slot of the logic contract's own storage; no fix pair; not independently reviewed"),
    ("safe_contracts", "D"): ("EVIDENCE_SUFFICIENT_PROTECTED", "handlePayment private, reached only after checkSignatures (GnosisSafe.sol:145); refund params are signed"),
}


def automatic(raw, dim):
    if raw.get("data_status") != "OK":
        return {"status": "ANALYSIS_INCOMPLETE",
                "reason": ((raw.get("build") or {}).get("reason") or raw.get("error") or "")[:200]}
    det = [f for f in raw.get("findings", []) if DIM.get(f.get("rule_id")) == dim]
    heu = [f for f in raw.get("heuristic_findings", []) if DIM.get(f.get("rule_id")) == dim]
    note = "no Slither capability for this question" if dim in ("B", "C") else \
        "Slither facts cannot prove that a protection is effective"
    return {"status": "EVIDENCE_INSUFFICIENT", "detector_alerts": len(det), "heuristic_signals": len(heu),
            "alerts": [f"{f['detector']} {f['contract']}.{f['function']}:{f['line']}" for f in det], "note": note}


def main():
    rows = []
    dev = EVAL / "results" / "dev16_r4" / "raw"
    for rf in sorted(dev.glob("*.json")):
        raw = json.loads(rf.read_text())
        ans = json.loads((EVAL / "answers" / rf.name).read_text())
        case = raw["case"]
        row = {"case": case, "kind": ans["kind"], "source_type": ans.get("source_type"),
               "source_certainty": ans.get("source_certainty"), "dims": {}}
        for d in "ABCD":
            m = MANUAL.get((case, d))
            row["dims"][d] = {"question": QUESTION[d], "automatic": automatic(raw, d),
                              "manual": {"status": m[0], "evidence": m[1]} if m else {"status": "NOT_REVIEWED"},
                              "isolated": {"status": "NOT_RUN"}}
        rows.append(row)
    summ = {"automatic": {}, "manual": {}, "isolated": {"NOT_RUN": len(rows) * 4}}
    for r in rows:
        for d in r["dims"].values():
            a, m = d["automatic"]["status"], d["manual"]["status"]
            summ["automatic"][a] = summ["automatic"].get(a, 0) + 1
            summ["manual"][m] = summ["manual"].get(m, 0) + 1
    out = {"generated_from": "eval/results/dev16_r4/raw + eval/answers (manual layer)", "summary": summ,
           "cases": rows}
    (HERE / "governance_check_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(summ, ensure_ascii=False))


if __name__ == "__main__":
    main()
