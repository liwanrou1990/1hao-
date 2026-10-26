"""V0.15 read-only audit baseline built only on Slither's own capabilities.

Two separate outputs, never mixed:

  findings            Slither built-in detector results whose subject is a
                      governance / permission / asset-control defect, mapped to
                      VULN classes. These are the scored baseline.
  heuristic_findings  signals derived from Slither's is_protected(), conditional
                      reads and data dependency. Status HEURISTIC_ONLY or
                      INCONCLUSIVE: they are NOT claims of an exploitable bug.

Not covered by any Slither capability (reported as INCONCLUSIVE coverage):
voting-power snapshots (VULN-2), timelock / proposal-execution conditions
(VULN-3), custom authorization semantics, proxy-initialization state and
cross-contract permission relations. Nothing here is a proof of safety.
"""
from __future__ import annotations
import inspect
import os
from typing import Dict, List, Optional, Tuple

from slither.analyses.data_dependency.data_dependency import is_dependent
from slither.core.declarations.solidity_variables import SolidityVariableComposed
from slither.detectors import all_detectors
from slither.detectors.abstract_detector import AbstractDetector
from slither.slithir.operations import LowLevelCall, SolidityCall

# Slither detector -> VULN class (governance / permission / asset control only)
DETECTOR_CLASS = {
    "unprotected-upgrade": "VULN-1",
    "tx-origin": "VULN-1",
    "protected-vars": "VULN-1",
    "suicidal": "VULN-4",
    "arbitrary-send-eth": "VULN-4",
    "arbitrary-send-erc20": "VULN-4",
    "arbitrary-send-erc20-permit": "VULN-4",
    "controlled-delegatecall": "VULN-4",
}
NOT_COVERED = {
    "VULN-2": "no Slither capability for voting-power snapshot / weight source analysis",
    "VULN-3": "no Slither capability for timelock / proposal-execution condition analysis",
}
MS = SolidityVariableComposed("msg.sender")


def _detector_classes():
    out = []
    for d in vars(all_detectors).values():
        if inspect.isclass(d) and issubclass(d, AbstractDetector) and d.ARGUMENT in DETECTOR_CLASS:
            out.append(d)
    return out


class Loc:
    def __init__(self, cb):
        self.cb = cb

    def of(self, sm) -> Dict:
        try:
            ab = sm.filename.absolute
        except Exception:
            return {"file": "", "line": None}
        rel = os.path.relpath(ab, self.cb.root) if self.cb.root else ab
        lines = sm.lines
        return {"file": self.cb.unit_to_dataset.get(rel, rel), "line": lines[0] if lines else None,
                "in_dataset": rel in self.cb.unit_to_dataset}


def _deployable(c) -> bool:
    return not (c.is_interface or c.is_library) and c.is_fully_implemented


def _entry_points(c):
    for f in c.functions_entry_points:
        if f.is_constructor or f.view or f.pure:
            continue
        yield f


# ---------------------------------------------------------------- detectors
def run_detectors(sl, loc: Loc) -> List[Dict]:
    for d in _detector_classes():
        sl.register_detector(d)
    out = []
    for results in sl.run_detectors():
        for r in results:
            cls = DETECTOR_CLASS.get(r.get("check"))
            if not cls:
                continue
            fn_el = next((e for e in r.get("elements", []) if e.get("type") == "function"), None)
            el = fn_el or (r.get("elements") or [{}])[0]
            parent = (el.get("type_specific_fields") or {}).get("parent") or {}
            contract = parent.get("name") if fn_el else \
                ((parent.get("type_specific_fields") or {}).get("parent") or {}).get("name")
            sm = el.get("source_mapping") or {}
            out.append({"rule_id": cls, "severity": r.get("impact", "").lower(),
                        "confidence": r.get("confidence", "").lower(),
                        "status": "SLITHER_DETECTOR", "detector": r.get("check"),
                        "contract": contract, "function": el.get("name") if fn_el else None,
                        "_abs": sm.get("filename_absolute"), "line": (sm.get("lines") or [None])[0],
                        "title": r.get("check"), "evidence": (r.get("description") or "").strip()[:600]})
    for f in out:
        ab = f.pop("_abs", None)
        if ab and loc.cb.root:
            rel = os.path.relpath(ab, loc.cb.root)
            f["file"] = loc.cb.unit_to_dataset.get(rel, rel)
            f["in_dataset"] = rel in loc.cb.unit_to_dataset
    return out


# ---------------------------------------------------------------- heuristics
def _sender_checked_vars(c) -> Dict:
    """State vars read in conditions of functions whose conditions also read msg.sender."""
    out = {}
    for g in c.functions_and_modifiers:
        try:
            if MS not in g.all_conditional_solidity_variables_read(include_loop=False):
                continue
            for v in g.all_conditional_state_variables_read(include_loop=False):
                out.setdefault(v, g)
        except Exception:
            continue
    return out


def _protection(f) -> Dict:
    sender_cond = MS in f.all_conditional_solidity_variables_read(include_loop=False)
    name_only = (not sender_cond) and "onlyOwner" in [m.name for m in f.modifiers]
    return {"is_protected": f.is_protected(), "sender_in_conditions": sender_cond,
            "protected_only_by_modifier_name": name_only}


def heuristics(c, loc: Loc) -> List[Dict]:
    out = []
    checked = _sender_checked_vars(c)
    for f in _entry_points(c):
        prot = _protection(f)
        if prot["is_protected"]:
            continue
        written = set(f.all_state_variables_written())
        principal = [v for v in written if v in checked]
        if principal:
            self_guard = bool(written & set(f.all_conditional_state_variables_read(include_loop=False)))
            out.append(dict(
                rule_id="VULN-1", severity="medium",
                status="INCONCLUSIVE" if self_guard else "HEURISTIC_ONLY",
                kind="unprotected-writer-of-sender-checked-state",
                contract=f.contract_declarer.name, function=f.name, **loc.of(f.source_mapping),
                protection=prot, principals=sorted(v.name for v in principal),
                checked_in=sorted({checked[v].canonical_name for v in principal}),
                note=("function also checks a variable it writes (possible one-shot initializer); "
                      "init/proxy state not analysable by baseline") if self_guard else
                     "is_protected()=False; writes state used in msg.sender checks elsewhere"))
        for n in f.all_nodes():
            for ir in n.irs:
                dest = None
                if isinstance(ir, LowLevelCall):
                    dest, kind = ir.destination, ir.function_name
                elif isinstance(ir, SolidityCall) and ir.function.name.startswith(("delegatecall(", "call(", "callcode(")):
                    if len(ir.arguments) > 1:
                        dest, kind = ir.arguments[1], ir.function.name.split("(")[0]
                if dest is None:
                    continue
                ctl = [p.name for p in f.parameters if _dep(dest, p, n.function)]
                if ctl or _dep(dest, SolidityVariableComposed("msg.data"), n.function):
                    out.append(dict(
                        rule_id="VULN-4", severity="medium", status="HEURISTIC_ONLY",
                        kind=f"unprotected-{kind}-to-parameter-derived-target",
                        contract=f.contract_declarer.name, function=f.name, **loc.of(n.source_mapping),
                        protection=prot, target_from=ctl or ["msg.data"],
                        note="target depends on caller input; whitelist / custom checks not analysed"))
    return out


def _dep(v, src, ctx) -> bool:
    try:
        return is_dependent(v, src, ctx)
    except Exception:
        return False


def chain_links(findings: List[Dict], units) -> List[Dict]:
    """HEURISTIC_LINK: a VULN-1-class function writes a state var that a
    VULN-4-class function reads. Never a confirmed chain."""
    links = []
    fns = {}
    for u in units:
        for c in u.slither.contracts:
            for f in c.functions:
                fns.setdefault((f.contract_declarer.name, f.name), f)
    a = [x for x in findings if x["rule_id"] == "VULN-1"]
    d = [x for x in findings if x["rule_id"] == "VULN-4"]
    for x in a:
        fa = fns.get((x["contract"], x["function"]))
        if fa is None:
            continue
        w = {v.name for v in fa.all_state_variables_written()}
        for y in d:
            fd = fns.get((y["contract"], y["function"]))
            if fd is None:
                continue
            shared = w & {v.name for v in fd.all_state_variables_read()}
            if shared:
                links.append({"status": "HEURISTIC_LINK", "from": f"{x['contract']}.{x['function']}",
                              "to": f"{y['contract']}.{y['function']}", "via": sorted(shared)})
    return links


_TIMEVARS = {"block.timestamp", "now", "block.number"}


def _entries_reaching(c, target) -> List:
    """Public entry points of c whose call tree reaches target (incl. itself)."""
    out = []
    for f in c.functions_entry_points:
        if f is target:
            out.append(f); continue
        try:
            reach = {getattr(x, "function", x) for x in f.all_internal_calls()}
        except Exception:
            reach = set()
        if target in reach:
            out.append(f)
    return out


def _time_gated(f) -> bool:
    try:
        return any(v.name in _TIMEVARS for v in f.all_conditional_solidity_variables_read(include_loop=False))
    except Exception:
        return False


def triage(findings: List[Dict], units) -> Tuple[List[Dict], List[Dict]]:
    """Keep a detector result in the scored track only if (a) it lies in the
    case's own contract files and (b) at least one public entry point reaching
    it has no time-dependent condition. Otherwise move it to the heuristic
    track as INCONCLUSIVE: Slither cannot evaluate timelock / proposal-state
    conditions, and dependency code is outside the audit scope."""
    fns = {}
    for u in units:
        for c in u.slither.contracts:
            if not _deployable(c):
                continue
            for f in c.functions:
                fns.setdefault((f.contract_declarer.name, f.name), []).append((c, f))
    keep, moved = [], []
    for x in findings:
        if x.get("in_dataset") is False:
            moved.append(dict(x, status="INCONCLUSIVE", triage="outside case contract files (dependency)"))
            continue
        entries = []
        for c, f in fns.get((x["contract"], x["function"]), []):
            entries += _entries_reaching(c, f)
        if entries and all(_time_gated(e) for e in entries):
            moved.append(dict(x, status="INCONCLUSIVE", severity="medium",
                              triage="every entry point is time/lifecycle-gated; timelock not analysable",
                              entries=sorted({e.canonical_name for e in entries})))
            continue
        keep.append(x)
    return keep, moved


def analyze(cb) -> Dict:
    """Run the baseline over a built case (dao_audit.compiler.build.CaseBuild)."""
    loc = Loc(cb)
    res = {"findings": [], "heuristic_findings": [], "chain_links": {}, "not_covered": NOT_COVERED}
    if not cb.units:
        return res
    seen, hseen = set(), set()
    for u in cb.units:
        if u.slither is None:
            continue
        for f in run_detectors(u.slither, loc):
            k = (f["rule_id"], f["detector"], f["contract"], f["function"], f["line"])
            if k not in seen:
                seen.add(k); res["findings"].append(f)
        for c in u.slither.contracts:
            if not _deployable(c):
                continue
            for h in heuristics(c, loc):
                k = (h["rule_id"], h["kind"], h["contract"], h["function"])
                if k not in hseen:
                    hseen.add(k); res["heuristic_findings"].append(h)
    ok_units = [u for u in cb.units if u.slither is not None]
    res["findings"], moved = triage(res["findings"], ok_units)
    res["heuristic_findings"] = moved + res["heuristic_findings"]
    res["chain_links"] = {
        "detector": chain_links(res["findings"], ok_units),
        "heuristic": chain_links(res["findings"] + res["heuristic_findings"], ok_units),
    }
    return res
