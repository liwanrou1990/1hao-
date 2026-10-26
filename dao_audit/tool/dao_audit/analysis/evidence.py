"""V0.17 standardised, read-only permission evidence for detector alerts.

For every Slither detector alert this module records FACTS taken from Slither
(call graph, IR, data dependency) plus the two Slither gaps filled and
unit-tested in perm/deps.py (mapping-index keys, control-dependent internal
returns). It never decides by identifier names and never declares code safe.

Facts per alert:
  entries            public/external entry points whose call tree reaches the
                     alerted function (the alerted function itself if public)
  sender_conditions  per entry: require/assert/if conditions in the entry, its
                     modifiers or its internal callees whose value depends on
                     msg.sender (data dependency incl. mapping keys / helper returns)
  signature_checks   per entry: ecrecover calls or ERC-1271 isValidSignature calls
  call_sites         for transferFrom-style alerts in internal functions: whether
                     the `from` argument at each call site is msg.sender / this

Status (exactly one):
  REFUTED_BY_DATAFLOW     the alert's premise (caller-chosen `from`) is false at
                          every call site, by data dependency
  PROTECTION_EVIDENCE     every entry has a msg.sender-dependent condition, a
                          signature check, or a condition on state the entry itself
                          writes (possible one-shot guard) -> INCONCLUSIVE, manual
                          review queue
                          (effectiveness of that protection is NOT verified)
  PREMISE_UNCONFIRMED     transferFrom-style alert whose `from` is not derived
                          from any caller-supplied address parameter (e.g. it comes
                          from an external registry call) -> INCONCLUSIVE, review
  NO_PROTECTION_EVIDENCE  some entry has neither -> automatic alert
  NOT_RESOLVED            alerted function not found in the IR
"""
from __future__ import annotations
from typing import Dict, List, Optional

from slither.analyses.data_dependency.data_dependency import is_dependent
from slither.core.declarations import Function
from slither.core.declarations.solidity_variables import SolidityVariable, SolidityVariableComposed
from slither.slithir.operations import (Condition, HighLevelCall, InternalCall, LibraryCall,
                                        SolidityCall)

from .perm.deps import Deps

MS = SolidityVariableComposed("msg.sender")
THIS = SolidityVariable("this")
_FROM_ALERTS = {"arbitrary-send-erc20", "arbitrary-send-erc20-permit"}


def _line(n) -> str:
    ls = n.source_mapping.lines
    return f"{n.function.contract_declarer.name}.{n.function.name}:L{ls[0] if ls else '?'}"


def _cond_vars(n):
    for ir in n.irs:
        if isinstance(ir, Condition):
            yield ir.value
        elif isinstance(ir, SolidityCall) and ir.function.name.startswith(("require", "assert")):
            yield ir.arguments[0]


def _functions_of(entry: Function) -> List[Function]:
    """Entry, its modifiers, and every internal callee (transitively)."""
    out, stack = [], [entry] + list(entry.modifiers)
    seen = set()
    while stack:
        f = stack.pop()
        if id(f) in seen or not isinstance(f, Function):
            continue
        seen.add(id(f)); out.append(f)
        for ir in [i for n in f.nodes for i in n.irs]:
            if isinstance(ir, InternalCall) and isinstance(ir.function, Function):
                stack.append(ir.function)
            elif isinstance(ir, LibraryCall) and isinstance(ir.function, Function) and ir.function.nodes:
                stack.append(ir.function)
    return out


def entry_facts(entry: Function, deps: Deps) -> Dict:
    sender, sigs = [], []
    for f in _functions_of(entry):
        for n in f.nodes:
            for v in _cond_vars(n):
                try:
                    if deps.sources(f, v).sender_reached():
                        sender.append({"at": _line(n), "expr": str(n.expression)[:160]})
                except Exception:
                    continue
            for ir in n.irs:
                if isinstance(ir, SolidityCall) and ir.function.name.startswith("ecrecover"):
                    sigs.append({"at": _line(n), "kind": "ecrecover"})
                elif isinstance(ir, HighLevelCall) and str(ir.function_name) == "isValidSignature":
                    sigs.append({"at": _line(n), "kind": "isValidSignature (ERC-1271)"})
    try:
        selfchk = sorted({v.name for v in entry.all_conditional_state_variables_read(include_loop=False)}
                         & {v.name for v in entry.all_state_variables_written()})
    except Exception:
        selfchk = []
    return {"entry": entry.canonical_name, "visibility": entry.visibility,
            "self_checked_state": selfchk,
            "modifiers": [m.name for m in entry.modifiers],
            "sender_conditions": sender[:8], "n_sender_conditions": len(sender),
            "signature_checks": sigs[:4], "n_signature_checks": len(sigs)}


def entries_reaching(contract, target: Function) -> List[Function]:
    out = []
    for f in contract.functions_entry_points:
        if f is target or any(g.canonical_name == target.canonical_name for g in _functions_of(f)):
            out.append(f)
    return out


def _transfer_from_calls(fn: Function):
    for n in fn.nodes:
        for ir in n.irs:
            if isinstance(ir, HighLevelCall):
                name = str(getattr(ir.function, "name", ir.function_name))
                if name.startswith(("transferFrom", "safeTransferFrom")):
                    args = list(ir.arguments)
                    frm = args[1] if isinstance(ir, LibraryCall) and len(args) >= 4 else (args[0] if args else None)
                    yield n, frm


def from_origin(fn: Function) -> Dict:
    """For every transferFrom-style call in fn: is `from` msg.sender / this, or
    derived from a caller-supplied address parameter?"""
    # caller-supplied sources of an address: address parameters, and bytes
    # parameters (an address can be abi-decoded from them)
    addr_params = [p for p in fn.parameters if str(p.type).startswith(("address", "bytes"))]
    calls = []
    for n, frm in _transfer_from_calls(fn):
        if frm is None:
            continue
        calls.append({"at": _line(n), "from": str(frm),
                      "from_is_sender": bool(frm == MS or _dep(frm, MS, fn)),
                      "from_is_this": bool(frm == THIS or _dep(frm, THIS, fn)),
                      "from_depends_on_address_params": [p.name for p in addr_params if frm == p or _dep(frm, p, fn)]})
    suspects = [c for c in calls if not (c["from_is_sender"] or c["from_is_this"])]
    return {"calls": calls, "n_suspect_calls": len(suspects),
            "premise_unconfirmed": bool(suspects) and all(not c["from_depends_on_address_params"] for c in suspects)}


def _from_param_index(fn: Function) -> Optional[int]:
    """Index of fn's parameter used as `from` of a transferFrom-style call in fn."""
    for n in fn.nodes:
        for ir in n.irs:
            if not isinstance(ir, HighLevelCall):
                continue
            name = str(getattr(ir.function, "name", ir.function_name))
            if not name.startswith(("transferFrom", "safeTransferFrom")):
                continue
            args = list(ir.arguments)
            frm = args[1] if isinstance(ir, LibraryCall) and len(args) >= 4 else (args[0] if args else None)
            if frm in fn.parameters:
                return fn.parameters.index(frm)
            return None
    return None


def call_site_facts(contracts, fn: Function) -> Optional[Dict]:
    idx = _from_param_index(fn)
    if idx is None or fn.visibility in ("public", "external"):
        return None
    sites = []
    callers = {id(g): g for c in contracts for g in c.functions_and_modifiers}
    for caller in callers.values():
        for n in caller.nodes:
            for ir in n.irs:
                if isinstance(ir, (InternalCall, LibraryCall)) and isinstance(ir.function, Function) and \
                        ir.function.canonical_name == fn.canonical_name and idx < len(ir.arguments):
                    a = ir.arguments[idx]
                    sites.append({"at": _line(n),
                                  "from_is_sender": bool(a == MS or _dep(a, MS, caller)),
                                  "from_is_this": bool(a == THIS or _dep(a, THIS, caller))})
    return {"from_param": fn.parameters[idx].name, "sites": sites,
            "all_sender_or_this": bool(sites) and all(s["from_is_sender"] or s["from_is_this"] for s in sites)}


def _dep(v, src, ctx) -> bool:
    try:
        return is_dependent(v, src, ctx)
    except Exception:
        return False


def alert_evidence(alert: Dict, units) -> Dict:
    deps = Deps()
    targets = []
    for u in units:
        for c in u.slither.contracts:
            if c.is_interface or not c.is_fully_implemented:
                continue
            for f in c.functions:
                if f.contract_declarer.name == alert.get("contract") and f.name == alert.get("function"):
                    targets.append((c, f))
    if not targets:
        return {"status": "NOT_RESOLVED", "note": "alerted function not found among deployable contracts"}
    c, fn = targets[0]
    allc = [cc for u in units for cc in u.slither.contracts]
    deployable = [cc for cc in allc if not (cc.is_interface or cc.is_library) and cc.is_fully_implemented]
    ev = {"function": fn.canonical_name, "visibility": fn.visibility, "detector": alert.get("detector")}
    if alert.get("detector") in _FROM_ALERTS:
        fo = from_origin(fn)
        ev["from_origin"] = fo
        if fo["premise_unconfirmed"]:
            ev["status"] = "PREMISE_UNCONFIRMED"
            ev["reason"] = ("`from` is not derived from any caller-supplied address parameter "
                            "(arbitrary-from premise unconfirmed) -> INCONCLUSIVE, manual review")
            return ev
        cs = call_site_facts(allc, fn)
        if cs:
            ev["call_sites"] = cs
            if cs["all_sender_or_this"]:
                ev["status"] = "REFUTED_BY_DATAFLOW"
                ev["reason"] = "at every call site `from` is msg.sender or this (data dependency)"
                return ev
    entries = []
    for cc in deployable:
        entries += entries_reaching(cc, fn)
    uniq = {e.canonical_name: e for e in entries}
    facts = [entry_facts(e, deps) for e in uniq.values()]
    ev["entries"] = facts
    if not facts:
        ev["status"] = "NO_PROTECTION_EVIDENCE"
        ev["reason"] = "no public entry point reaches the function"
    elif all(x["n_sender_conditions"] or x["n_signature_checks"] or x["self_checked_state"] for x in facts):
        ev["status"] = "PROTECTION_EVIDENCE"
        ev["reason"] = ("every entry point has a msg.sender-dependent condition, a signature check, or a "
                        "condition on state it writes itself (possible one-shot guard); effectiveness not "
                        "verified -> INCONCLUSIVE, manual review")
    else:
        ev["status"] = "NO_PROTECTION_EVIDENCE"
        ev["reason"] = "entry points without any protection evidence: " + \
            ", ".join(x["entry"] for x in facts if not (x["n_sender_conditions"] or x["n_signature_checks"]
                                                        or x["self_checked_state"]))[:300]
    return ev
