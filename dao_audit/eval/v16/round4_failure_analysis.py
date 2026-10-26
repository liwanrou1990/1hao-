#!/usr/bin/env python3
"""V0.16 Round 4 failure analysis (post-hoc, Round 4 is a dev set from here on).

Inputs: the ORIGINAL blind outputs eval/results/round4/raw (never modified),
the dev rerun on the fixed toolchain eval/results/dev16_r4/raw, and the answers.
Output: eval/v16/round4_failure_analysis.json with, for every missed truth
item, every false positive and every unanalysable case: the technical root
cause category, the reason, code location and evidence (tool versions,
compiler, detector text).

Root-cause categories (exactly one per missed item):
  NO_RULE                 no Slither detector / capability covers the pattern
  PARSE_FAIL              the case could not be compiled (ANALYSIS_INCOMPLETE)
  SEMANTICS_UNDETERMINED  a relevant capability exists, but the permission
                          semantics could not be resolved (custom guard,
                          condition via internal call, chain through a role)
  INSUFFICIENT_EVIDENCE   a signal exists but only as HEURISTIC_ONLY /
                          INCONCLUSIVE (or wrong class), not a scored finding
Categories were assigned by manual review of code + labels (not blind).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))
from evaluate import _located  # noqa: E402

# (case, function) -> (category, reason). Manual review, Round 4 labels visible.
FN = {
    ("bancor_network", "safeTransferFrom"): ("NO_RULE", "transferFrom is issued as a low-level call with an encoded selector (execute(token, abi.encodeWithSelector(...))); arbitrary-send-erc20 only matches high-level transferFrom calls; no detector flags a public asset helper that should be internal"),
    ("bancor_network", "safeTransfer"): ("NO_RULE", "same encoded low-level call; no detector for a public helper moving the contract's own tokens"),
    ("bancor_network", "safeApprove"): ("NO_RULE", "same encoded low-level call; no detector for public approve helpers"),
    ("bond_fixed_expiry_teller", "redeem"): ("PARSE_FAIL", "vulnerable commit has no recorded submodules/lockfile for solmate and clones-with-immutable-args; dependency versions unknown -> ANALYSIS_INCOMPLETE (still after V0.16 toolchain fixes)"),
    ("daostack_ucontroller", "newOrganization"): ("NO_RULE", "permissionless registration that trusts values re-read from a caller-supplied contract; no detector models time-of-check/time-of-use on external getters"),
    ("daostack_ucontroller", "sendEther"): ("SEMANTICS_UNDETERMINED", "sendEther is guarded by a scheme-permission modifier; Slither treats it as protected and cannot tell that the permission itself is obtainable through newOrganization (chain through a role)"),
    ("lifi_cbridge_facet", "swapAndStartBridgeTokensViaCBridge"): ("NO_RULE", "no Slither detector for an arbitrary low-level call (callTo.call(callData)) without ETH value; baseline heuristic missed it because the target flows through a library function parameter (inter-procedural)"),
    ("lifi_cbridge_facet", "swap"): ("NO_RULE", "same: arbitrary call with caller-controlled target and calldata has no detector"),
    ("llama_relative_quorum", "validateActionCreation"): ("NO_RULE", "VULN-2: no Slither capability for quorum / voting-supply snapshot semantics"),
    ("moonwell_temporal_governor", "fastTrackProposalExecution"): ("NO_RULE", "VULN-3: no Slither capability for timelock / emergency-path conditions (fix adds whenPaused)"),
    ("mph88_deposit_nft", "init"): ("SEMANTICS_UNDETERMINED", "init is unprotected and writes _owner, but onlyOwner checks require(isOwner()) whose msg.sender comparison is inside an internal call; all_conditional_solidity_variables_read does not follow it, so _owner is not seen as a sender-checked variable"),
    ("mph88_deposit_nft", "mint"): ("SEMANTICS_UNDETERMINED", "onlyOwner-protected; the risk exists only through the init takeover (chain), which Slither cannot link"),
    ("mph88_deposit_nft", "burn"): ("SEMANTICS_UNDETERMINED", "same as mint"),
    ("nomad_replica", "initialize"): ("NO_RULE", "initialize is protected by an initializer guard; the defect is the trusted value (zero root pre-approved), which no detector reasons about"),
    ("nomad_replica", "process"): ("NO_RULE", "message-acceptance semantics (acceptableRoot on the default value) have no detector"),
    ("olympus_governance", "endorseProposal"): ("PARSE_FAIL", "blind run: foundry.toml remappings without trailing slash -> ANALYSIS_INCOMPLETE (fixed in V0.16 toolchain; on dev rerun it compiles, and VULN-2 is then NO_RULE)"),
    ("olympus_governance", "executeProposal"): ("PARSE_FAIL", "same build failure in the blind run (on dev rerun: VULN-3 NO_RULE)"),
    ("party_veto_proposal", "voteToVeto"): ("NO_RULE", "VULN-2 double counting: no detector for missing per-voter records"),
    ("pickle_controller", "swapExactJarForJar"): ("NO_RULE", "unvalidated contract parameters (_fromJar/_toJar) used as call targets; no detector for untrusted-contract parameters"),
    ("pickle_controller", "add_liquidity"): ("INSUFFICIENT_EVIDENCE", "baseline heuristic located it (unprotected call to parameter-derived target) but it is HEURISTIC_ONLY, not a scored finding"),
    ("sandbox_land", "_burn"): ("INSUFFICIENT_EVIDENCE", "heuristic flagged exactly _burn (public, unprotected writer of ownership state) but as VULN-1 and HEURISTIC_ONLY; label is VULN-4"),
    ("sushi_route_processor2", "swapUniV3"): ("PARSE_FAIL", "blind run: monorepo yarn.lock lookup installed OpenZeppelin 3.1.0 instead of declared 4.8.0 (fixed in V0.16; dev rerun compiles, swapUniV3 itself still has no detector)"),
    ("sushi_route_processor2", "uniswapV3SwapCallback"): ("PARSE_FAIL", "same build failure in the blind run; on the dev rerun arbitrary-send-erc20 DOES locate this function"),
    ("wormhole_core_impl", "initialize"): ("SEMANTICS_UNDETERMINED", "custom initializer guard keyed on the ERC1967 implementation slot; Slither unprotected-upgrade only activates for contracts named/inheriting 'Initializable' or '*upgradeable*' and only recognises a modifier named 'initializer'"),
    ("wormhole_core_impl", "upgradeImplementation"): ("INSUFFICIENT_EVIDENCE", "controlled-delegatecall fired, but the V0.15 baseline triage demoted it to INCONCLUSIVE because the guardian-signature verification reads block.timestamp (a time condition is not a timelock)"),
}

FP = {
    ("Avatar", "externalTokenTransferFrom"): ("PROTECTION_IGNORED", "arbitrary-send-erc20 ignores access control: the function is onlyOwner (Avatar.sol:101), owner is the Controller; caller cannot choose _from"),
    ("LibAsset", "transferFromERC20"): ("CALL_SITE_CONTEXT_IGNORED", "internal library helper reported in isolation; both call sites pass msg.sender as from (CBridgeFacet.sol:62, LibSwap.sol:35)"),
    ("BondBaseTeller", "_handleTransfers"): ("PROTECTION_IGNORED", "from = market owner returned by the auctioneer registry for market id_ (BondBaseTeller.sol:184-218), a pre-approved market maker, not a caller-chosen address"),
    ("GnosisSafe", "handlePayment"): ("PROTECTION_IGNORED", "private; reached only from execTransaction after checkSignatures (owner threshold, GnosisSafe.sol:145); refund parameters are part of the signed hash. Signature-based authorization is not modelled by Slither"),
    ("RouteProcessor2", "wrapNative"): ("PROTECTION_IGNORED", "dev rerun only: router sends native ETH to the route-specified recipient by design; amounts come from the caller's own input"),
}


def _versions(raw):
    b = raw.get("build") or {}
    comp = [u.get("compiler") for u in b.get("units", []) if u.get("compiler")]
    return [{"version_string": c.get("version_string"), "soljson_sha256": c.get("soljson_sha256"),
             "selected_by": c.get("selected_by")} for c in comp]


def main():
    out = {"source": "eval/results/round4/raw (blind, unchanged) + eval/results/dev16_r4/raw (dev rerun)",
           "missed": [], "false_positives": [], "unanalysable": [], "summary": {}}
    blind = EVAL / "results" / "round4" / "raw"
    dev = EVAL / "results" / "dev16_r4" / "raw"
    env = json.loads((EVAL / "results" / "round4" / "engine_freeze_round4.json").read_text())["environment"]
    out["tools"] = env
    for rf in sorted(blind.glob("*.json")):
        raw = json.loads(rf.read_text())
        ans = json.loads((EVAL / "answers" / rf.name).read_text())
        draw = json.loads((dev / rf.name).read_text()) if (dev / rf.name).exists() else {}
        case = raw["case"]
        if raw.get("data_status") != "OK":
            out["unanalysable"].append({"case": case, "kind": ans["kind"],
                                        "blind_reason": (raw.get("build") or {}).get("reason", "")[:300],
                                        "dev_rerun_status": draw.get("data_status")})
        for t in ans.get("truth", []):
            if any(_located(f, t) for f in raw.get("findings", [])):
                continue
            cat, why = FN.get((case, t["function"]), ("UNCLASSIFIED", ""))
            dev_hit = any(_located(f, t) for f in draw.get("findings", []))
            heur_hit = any(_located(f, t) for f in raw.get("heuristic_findings", []))
            out["missed"].append({"case": case, "vuln": t["vuln"], "contract": t["contract"],
                                  "function": t["function"], "lines": t["line"],
                                  "file_hint": "eval/dataset_r4/%s/contracts/" % case,
                                  "category": cat, "reason": why,
                                  "heuristic_track_located": heur_hit, "dev_rerun_located": dev_hit,
                                  "source_type": ans.get("source_type"),
                                  "source_certainty": ans.get("source_certainty"),
                                  "compiler": _versions(raw)})
        for f in raw.get("findings", []):
            if any(_located(f, t) for t in ans.get("truth", [])):
                continue
            cat, why = FP.get((f["contract"], f["function"]), ("UNCLASSIFIED", ""))
            out["false_positives"].append({"case": case, "kind": ans["kind"], "detector": f["detector"],
                                           "contract": f["contract"], "function": f["function"],
                                           "line": f["line"], "file": f.get("file"), "category": cat,
                                           "reason": why, "detector_text": f.get("evidence", "")[:300]})
    s = out["summary"]
    for k in ("missed", "false_positives"):
        c = {}
        for x in out[k]:
            c[x["category"]] = c.get(x["category"], 0) + 1
        s[k] = c
    s["unanalysable_blind"] = len(out["unanalysable"])
    s["unanalysable_after_v16_fixes"] = sum(1 for u in out["unanalysable"] if u["dev_rerun_status"] != "OK")
    (HERE / "round4_failure_analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
