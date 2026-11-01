#!/usr/bin/env python3
"""M2 protocol economic model + M3 candidate property generation (prototype).

Facts come from Slither IR only (types, calls, data dependency, state reads /
writes). No identifier-name matching decides anything: the model classifies
state by STRUCTURE (an address-indexed uint mapping written in a function that
also moves an ERC20 is "accounting"; an external view call whose result flows
into a comparison that guards an outflow is a "valuation dependency"; ...).
Every generated property carries business assumptions, legitimate exceptions
and the evidence (file:line) it was instantiated from. Properties are
CANDIDATES; nothing here is a finding.

Usage: python3 econsec/model.py <sol files...> --solc <solc> --out model.json
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tool"))

from slither.analyses.data_dependency.data_dependency import is_dependent  # noqa: E402
from slither.core.declarations.solidity_variables import SolidityVariableComposed  # noqa: E402
from slither.core.solidity_types import MappingType, ElementaryType, UserDefinedType  # noqa: E402
from slither.slithir.operations import (Condition, HighLevelCall, LowLevelCall, SolidityCall,  # noqa: E402
                                        Send, Transfer)

MS = SolidityVariableComposed("msg.sender")
# ERC20 ABI shape (standard signatures, not names chosen by the project)
ERC20_OUT = {"transfer(address,uint256)"}
ERC20_IN = {"transferFrom(address,address,uint256)"}
ERC20_READ = {"balanceOf(address)", "totalSupply()"}


def load(files, solc):
    from crytic_compile import CryticCompile
    from crytic_compile.platform.solc_standard_json import SolcStandardJson
    from slither import Slither
    logging.disable(logging.CRITICAL)
    s = SolcStandardJson(None)
    for f in files:
        s.add_source_file(str(f))
    return Slither(CryticCompile(s, solc=solc))


def loc(x):
    sm = x.source_mapping
    return f"{Path(sm.filename.short).name}:{sm.lines[0] if sm.lines else '?'}"


def sig(ir):
    f = getattr(ir, "function", None)
    return getattr(f, "solidity_signature", None) or f"{ir.function_name}(...)"


def _dep(a, b, ctx):
    try:
        return is_dependent(a, b, ctx)
    except Exception:
        return False


def addr_uint_mappings(c):
    out = []
    for v in c.state_variables:
        t = v.type
        if isinstance(t, MappingType) and str(t.type_from) == "address" and isinstance(t.type_to, ElementaryType) \
                and str(t.type_to).startswith("uint"):
            out.append(v)
    return out


def nodes_with_callees(f, depth=3):
    """Nodes of f plus those of its internal callees (bounded), each tagged with the
    function whose context is used for data dependency."""
    out, seen, stack = [], set(), [(f, 0)]
    while stack:
        g, d = stack.pop()
        if id(g) in seen or d > depth:
            continue
        seen.add(id(g))
        for n in g.nodes:
            out.append((g, n))
        for h in g.internal_calls:
            h = getattr(h, "function", h)
            if hasattr(h, "nodes"):
                stack.append((h, d + 1))
    return out


def analyse_contract(c):
    m = {"contract": c.name, "file": Path(c.source_mapping.filename.short).name,
         "assets": {}, "accounting": [], "valuation_dependencies": [], "outflows": [],
         "privileged_entries": [], "unguarded_state_writers": [], "external_dependencies": []}
    if c.is_interface or c.is_library or not c.is_fully_implemented:
        m["skipped"] = "interface/library/abstract"
        return m
    maps = addr_uint_mappings(c)
    for f in c.functions_entry_points:
        if f.is_constructor:
            continue
        moves_in, moves_out, reads, ext, val_deps, cond_srcs = [], [], [], [], [], []
        ctx_nodes = nodes_with_callees(f)
        for g, n in ctx_nodes:
            for ir in n.irs:
                if isinstance(ir, HighLevelCall):
                    s = sig(ir)
                    dest = str(ir.destination)
                    rec = {"target": dest, "sig": s, "at": loc(n)}
                    if s in ERC20_IN:
                        moves_in.append(rec)
                    elif s in ERC20_OUT:
                        moves_out.append(rec)
                    elif s in ERC20_READ:
                        reads.append(rec)
                    else:
                        ext.append(rec)
                        # valuation dependency: a view-ish external result that flows into a condition
                        if ir.lvalue is not None and getattr(ir.function, "view", False):
                            for g2, n2 in ctx_nodes:
                                for ir2 in n2.irs:
                                    v = ir2.value if isinstance(ir2, Condition) else (
                                        ir2.arguments[0] if isinstance(ir2, SolidityCall) and ir2.function.name.startswith("require") else None)
                                    if v is not None and (_dep(v, ir.lvalue, g2) or
                                                          (g2 is not g and any(_dep(rv, ir.lvalue, g) for rv in g.return_values))):
                                        val_deps.append({"source": rec, "guards": loc(n2), "via": g.name})
                elif isinstance(ir, LowLevelCall) and ir.function_name in ("call", "delegatecall"):
                    moves_out.append({"target": str(ir.destination), "sig": f"low-level {ir.function_name}",
                                      "value": str(ir.call_value) if ir.call_value is not None else None, "at": loc(n)})
                elif isinstance(ir, (Send, Transfer)):
                    moves_out.append({"target": str(ir.destination), "sig": "native send/transfer", "at": loc(n)})
                v = None
                if isinstance(ir, Condition):
                    v = ir.value
                elif isinstance(ir, SolidityCall) and ir.function.name.startswith(("require", "assert")):
                    v = ir.arguments[0]
                if v is not None:
                    cond_srcs.append({"at": loc(n), "sender_dependent": _dep(v, MS, g),
                                      "state_read": sorted(sv.name for sv in n.state_variables_read)})
        written = {sv.name for sv in f.all_state_variables_written()}
        for mp in maps:
            if mp.name in written and (moves_in or moves_out):
                m["accounting"].append({"variable": mp.name, "written_in": f.name, "at": loc(f),
                                        "direction": "in" if moves_in else "out"})
        for rec in moves_in + moves_out:
            m["assets"].setdefault(rec["target"], {"moved_in": [], "moved_out": []})
            m["assets"][rec["target"]]["moved_in" if rec in moves_in else "moved_out"].append(f.name)
        if moves_out:
            m["outflows"].append({"function": f.name, "at": loc(f), "sinks": moves_out,
                                  "sender_conditions": [x for x in cond_srcs if x["sender_dependent"]],
                                  "other_conditions": [x for x in cond_srcs if not x["sender_dependent"]],
                                  "state_written": sorted(written)})
        if any(x["sender_dependent"] for x in cond_srcs):
            m["privileged_entries"].append({"function": f.name, "at": loc(f),
                                            "conditions": [x["at"] for x in cond_srcs if x["sender_dependent"]]})
        elif written and f.visibility in ("public", "external"):
            m["unguarded_state_writers"].append({"function": f.name, "at": loc(f), "writes": sorted(written)})
        weight_reads = [r for r in reads if r["sig"] == "balanceOf(address)"] + \
            [e for e in ext if e["sig"].startswith("getPastVotes(")]
        if weight_reads and written:
            m.setdefault("weight_sources", []).append({"function": f.name, "at": loc(f), "reads": weight_reads,
                                                       "snapshot_shaped": any(e["sig"].startswith("getPastVotes(") for e in weight_reads),
                                                       "writes": sorted(written)})
        m["valuation_dependencies"] += val_deps
        m["external_dependencies"] += [dict(e, function=f.name) for e in ext]
    return m


# ---------------------------------------------------------------- M3 templates
def properties(m):
    P = []
    c = m["contract"]
    acc_in = [a for a in m["accounting"] if a["direction"] == "in"]
    acc_out = [a for a in m["accounting"] if a["direction"] == "out"]
    for asset, mv in m["assets"].items():
        ins = [a for a in acc_in if a["written_in"] in mv["moved_in"]]
        if ins:
            P.append({"id": f"{c}.ASSET_CONSERVATION.{asset}", "family": "asset_conservation",
                      "statement": f"balanceOf({c}) of asset `{asset}` >= sum over users of `{ins[0]['variable']}` "
                                   f"minus assets legitimately paid out",
                      "assumptions": ["the asset is a standard ERC20 without fee-on-transfer/rebasing",
                                      f"`{ins[0]['variable']}` is the only accounting of user claims on this asset"],
                      "legitimate_exceptions": ["direct token donations to the contract (balance may exceed claims)"],
                      "evidence": [ins[0]["at"]] + [f"moved_in by {x}" for x in mv["moved_in"]],
                      "check": "foundry_invariant"})
    if acc_in and acc_out and m["valuation_dependencies"]:
        vd = m["valuation_dependencies"][0]
        P.append({"id": f"{c}.SOLVENCY", "family": "collateral_solvency",
                  "statement": f"after any operation sequence, sum of `{acc_out[0]['variable']}` <= LTV * value of "
                               f"sum of `{acc_in[0]['variable']}` at the valuation source's quiescent reading",
                  "assumptions": ["the valuation source reflects a fair price when no transaction is in flight",
                                  "LTV is the constant used in the guarding comparison"],
                  "legitimate_exceptions": ["price moves between operations by exogenous trading (bounded drift)"],
                  "evidence": [vd["source"]["at"], vd["guards"], acc_out[0]["at"]],
                  "risk_hint": f"valuation depends on external call `{vd['source']['sig']}` (manipulable if spot)",
                  "check": "foundry_invariant+halmos"})
    for o in m["outflows"]:
        if not o["sender_conditions"]:
            P.append({"id": f"{c}.OUTFLOW_AUTH.{o['function']}", "family": "permission_effectiveness",
                      "statement": f"`{o['function']}` moves assets out but has no msg.sender-dependent condition on "
                                   f"its path: every outflow must be authorised or user-bounded",
                      "assumptions": ["outflows to the caller bounded by the caller's own claims are legitimate"],
                      "legitimate_exceptions": ["withdrawal of the caller's own accounted balance"],
                      "evidence": [o["at"]] + [s["at"] for s in o["sinks"]], "check": "foundry_invariant"})
        else:
            caps = [x for x in o["other_conditions"] if x["state_read"]]
            P.append({"id": f"{c}.OUTFLOW_BOUND.{o['function']}", "family": "treasury_access_constraint",
                      "statement": f"`{o['function']}` is privileged; its outflow must additionally be bounded by a "
                                   f"state-defined policy (allowlist / cap / delay)",
                      "assumptions": ["a treasury has a spending policy expressible as state"],
                      "legitimate_exceptions": ["a protocol whose governance is itself the only policy (documented)"],
                      "evidence": [o["at"]] + [x["at"] for x in caps],
                      "policy_conditions_found": len(caps),
                      "structural_note": "NO state-defined bound on this privileged outflow" if not caps else None,
                      "check": "foundry_invariant" if caps else "structural_only"})
    for w in m.get("weight_sources", []):
        P.append({"id": f"{c}.VOTE_CONSERVATION.{w['function']}", "family": "vote_conservation",
                  "statement": f"total weight recorded by `{w['function']}` across all voters of one decision <= "
                               f"the token's total supply; the same token units are never counted twice",
                  "assumptions": ["the weight token is transferable", "one decision = one proposal id"],
                  "legitimate_exceptions": ["non-transferable / soulbound weight tokens"],
                  "evidence": [w["at"]] + [r["at"] for r in w["reads"]],
                  "risk_hint": None if w["snapshot_shaped"] else "weight read is live balance, not a snapshot-shaped call",
                  "check": "foundry_invariant"})
    for w in m["unguarded_state_writers"]:
        P.append({"id": f"{c}.STATE_WRITE_AUTH.{w['function']}", "family": "permission_effectiveness",
                  "statement": f"`{w['function']}` writes {w['writes']} without any msg.sender-dependent condition; "
                               f"if those variables define roles or accounting, writes must be bounded",
                  "assumptions": ["variables written by anyone must not define roles or others' balances"],
                  "legitimate_exceptions": ["self-service writes limited to msg.sender's own entries"],
                  "evidence": [w["at"]], "check": "manual_or_foundry"})
    return P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--solc", default="/root/.foundry/bin/solc")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sl = load([Path(f).resolve() for f in a.files], a.solc)
    models, props = [], []
    for c in sl.contracts_derived:
        m = analyse_contract(c)
        models.append(m)
        if "skipped" not in m:
            props += properties(m)
    Path(a.out).write_text(json.dumps({"models": models, "candidate_properties": props}, indent=2))
    print(f"{len(models)} contracts, {len(props)} candidate properties -> {a.out}")


if __name__ == "__main__":
    main()
