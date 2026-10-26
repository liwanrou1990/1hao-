"""Source sets built on Slither's data dependency, plus the two gaps that the
capability tests (tests/perm/test_slither_dd.py) showed Slither does not cover:

  G1  mapping/array index keys:  m[msg.sender]  -> lookup(m, keys={sender})
  G2  control-dependent internal returns: a callee that returns a constant
      only after checking its arguments / state / msg.sender. The callee's
      condition sources are attached to the call result (control dependence),
      with callee parameters substituted by the caller's arguments.

Everything else (transitive dependency inside a function, data returns of
internal calls, aliases) is taken from Slither's get_dependencies().
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from slither.analyses.data_dependency.data_dependency import get_dependencies
from slither.core.declarations import Function
from slither.core.declarations.solidity_variables import SolidityVariable
from slither.core.variables.local_variable import LocalVariable
from slither.core.variables.state_variable import StateVariable
from slither.slithir.operations import (Condition, HighLevelCall, Index, InternalCall, LibraryCall,
                                        Member, Return, SolidityCall)
from slither.slithir.variables import Constant, ReferenceVariable

_TIME = {"block.timestamp", "now", "block.number"}


class ExtCall:
    def __init__(self, op, dest: "SrcSet", args: List["SrcSet"]):
        self.op, self.dest, self.args = op, dest, args

    @property
    def sig(self) -> str:
        f = getattr(self.op, "function", None)
        return getattr(f, "solidity_signature", None) or str(getattr(self.op, "function_name", ""))


class SrcSet:
    __slots__ = ("sender", "origin", "this", "time", "msgdata", "state", "params",
                 "lookups", "ext", "consts", "fields")

    def __init__(self):
        self.sender = self.origin = self.this = self.time = self.msgdata = False
        self.state: set = set()
        self.params: set = set()
        self.lookups: List[Tuple[StateVariable, "SrcSet"]] = []
        self.ext: List[ExtCall] = []
        self.consts: set = set()
        self.fields: set = set()          # (state var, member name)

    def merge(self, o: "SrcSet") -> "SrcSet":
        for a in ("sender", "origin", "this", "time", "msgdata"):
            setattr(self, a, getattr(self, a) or getattr(o, a))
        self.state |= o.state; self.params |= o.params; self.consts |= o.consts
        self.fields |= o.fields
        self.lookups += [l for l in o.lookups if l not in self.lookups]
        self.ext += [e for e in o.ext if e not in self.ext]
        return self

    def substitute(self, args: List["SrcSet"]) -> "SrcSet":
        """Express a callee-relative set in the caller: param i -> args[i]."""
        out = SrcSet()
        for a in ("sender", "origin", "this", "time", "msgdata"):
            setattr(out, a, getattr(self, a))
        out.state = set(self.state); out.consts = set(self.consts); out.fields = set(self.fields)
        for i in self.params:
            if i < len(args):
                out.merge(args[i])
        out.lookups = [(sv, k.substitute(args)) for sv, k in self.lookups]
        out.ext = [ExtCall(e.op, e.dest.substitute(args), [x.substitute(args) for x in e.args])
                   for e in self.ext]
        return out

    def sender_reached(self) -> bool:
        """msg.sender reaches this value directly, as a lookup key, or as an
        argument of an external call."""
        return (self.sender or any(k.sender_reached() for _, k in self.lookups)
                or any(a.sender_reached() for e in self.ext for a in e.args))

    def all_state(self) -> set:
        s = set(self.state) | {sv for sv, _ in self.lookups}
        for _, k in self.lookups:
            s |= k.all_state()
        for e in self.ext:
            s |= e.dest.all_state()
        return s

    def describe(self) -> str:
        parts = []
        if self.sender: parts.append("msg.sender")
        if self.origin: parts.append("tx.origin")
        if self.this: parts.append("this")
        if self.time: parts.append("time")
        parts += [f"param#{i}" for i in sorted(self.params)]
        parts += sorted(v.name for v in self.state)
        parts += [f"{sv.name}[{k.describe()}]" for sv, k in self.lookups]
        parts += [f"ext:{e.sig}" for e in self.ext]
        return ",".join(parts)


class Deps:
    """Per-compilation-unit cache of producers and callee summaries."""

    def __init__(self):
        self._prod: Dict[int, Dict[str, object]] = {}
        self._summ: Dict[int, Optional[SrcSet]] = {}

    def producers(self, fn: Function) -> Dict[str, object]:
        k = id(fn)
        if k not in self._prod:
            d = {}
            for n in fn.nodes:
                for ir in n.irs:
                    lv = getattr(ir, "lvalue", None)
                    if lv is not None and not isinstance(ir, Member):
                        d.setdefault(str(lv), ir)
                    elif isinstance(ir, Member):
                        d.setdefault(str(lv), ir)
            self._prod[k] = d
        return self._prod[k]

    # ------------------------------------------------------------------
    def leaf(self, v, fn: Function, s: SrcSet) -> None:
        if isinstance(v, Constant):
            s.consts.add(str(v.value))
        elif isinstance(v, StateVariable):
            (s.consts.add(v.name) if v.is_constant else s.state.add(v))
        elif isinstance(v, SolidityVariable):
            n = v.name
            if n == "msg.sender": s.sender = True
            elif n == "tx.origin": s.origin = True
            elif n == "this": s.this = True
            elif n in _TIME: s.time = True
            elif n in ("msg.data", "msg.sig"): s.msgdata = True
        elif isinstance(v, LocalVariable) and v in fn.parameters:
            s.params.add(fn.parameters.index(v))

    def sources(self, fn: Function, var, depth: int = 0) -> SrcSet:
        s = SrcSet()
        if var is None:
            return s
        try:
            deps = set(get_dependencies(var, fn))
        except Exception:
            deps = set()
        deps.add(var)
        prod = self.producers(fn)
        for v in deps:
            self.leaf(v, fn, s)
            op = prod.get(str(v))
            if op is None or depth > 6:
                continue
            if isinstance(op, Index):                                # G1
                root = _root_state(op.variable_left, prod)
                keys = self.sources(fn, op.variable_right, depth + 1)
                if root is not None:
                    s.lookups.append((root, keys))
                else:
                    s.merge(keys)
            elif isinstance(op, Member):
                root = _root_state(op.variable_left, prod)
                if root is not None:
                    s.fields.add((root, str(op.variable_right)))
            elif isinstance(op, LibraryCall) and op.function is not None and op.function.nodes:
                s.merge(self._call(fn, op, depth))
            elif isinstance(op, HighLevelCall):
                s.ext.append(ExtCall(op, self.sources(fn, op.destination, depth + 1),
                                     [self.sources(fn, a, depth + 1) for a in op.arguments]))
            elif isinstance(op, InternalCall) and isinstance(op.function, Function):
                s.merge(self._call(fn, op, depth))                   # G2
        return s

    def _call(self, fn, op, depth) -> SrcSet:
        summ = self.summary(op.function)
        if summ is None:
            return SrcSet()
        args = [self.sources(fn, a, depth + 1) for a in op.arguments]
        return summ.substitute(args)

    def summary(self, callee: Function) -> Optional[SrcSet]:
        """Sources of the callee's result: its return values plus the sources
        of every condition inside it (control dependence), callee-relative."""
        k = id(callee)
        if k in self._summ:
            return self._summ[k]
        self._summ[k] = None                      # recursion guard
        s = SrcSet()
        for n in callee.nodes:
            for ir in n.irs:
                if isinstance(ir, Condition):
                    s.merge(self.sources(callee, ir.value, 1))
                elif isinstance(ir, SolidityCall) and ir.function.name.startswith(("require", "assert")):
                    s.merge(self.sources(callee, ir.arguments[0], 1))
                elif isinstance(ir, Return):
                    for v in ir.values:
                        s.merge(self.sources(callee, v, 1))
        self._summ[k] = s
        return s


def _root_state(v, prod) -> Optional[StateVariable]:
    for _ in range(8):
        if isinstance(v, StateVariable):
            return v
        if isinstance(v, ReferenceVariable):
            po = getattr(v, "points_to_origin", None)
            if isinstance(po, StateVariable):
                return po
            op = prod.get(str(v))
            if isinstance(op, (Index, Member)):
                v = op.variable_left
                continue
        return None
    return None
