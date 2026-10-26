"""Solidity `pragma solidity` version-range evaluation (npm-semver subset that
solc itself accepts: ^ ~ >= > <= < = plain, partial versions, AND by space,
OR by ||)."""
from __future__ import annotations
import re
from typing import Iterable, List, Optional, Tuple

V = Tuple[int, int, int]
_CMP = re.compile(r"(\^|~|>=|<=|>|<|=)?\s*v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?")


def parse(v: str) -> V:
    a = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple((a + [0, 0, 0])[:3])  # type: ignore


def _expand(op: str, maj: int, mi: Optional[int], pa: Optional[int]) -> List[Tuple[str, V]]:
    lo = (maj, mi or 0, pa or 0)
    if op == "^":
        if maj > 0:
            return [(">=", lo), ("<", (maj + 1, 0, 0))]
        if mi is None:
            return [(">=", lo), ("<", (1, 0, 0))]
        if mi > 0 or pa is None:
            return [(">=", lo), ("<", (0, mi + 1, 0))]
        return [(">=", lo), ("<", (0, 0, pa + 1))]
    if op == "~":
        if mi is None:
            return [(">=", lo), ("<", (maj + 1, 0, 0))]
        return [(">=", lo), ("<", (maj, mi + 1, 0))]
    if op in ("", "="):
        if mi is None:
            return [(">=", lo), ("<", (maj + 1, 0, 0))]
        if pa is None:
            return [(">=", lo), ("<", (maj, mi + 1, 0))]
        return [("=", lo)]
    if op in ("<", ">=") or (pa is not None):
        return [(op, lo)]
    # partial with > / <= : >0.5 means >=0.6.0 ; <=0.5 means <0.6.0
    up = (maj + 1, 0, 0) if mi is None else (maj, mi + 1, 0)
    return [(">=", up)] if op == ">" else [("<", up)]


def _ok(v: V, op: str, b: V) -> bool:
    return {"=": v == b, ">=": v >= b, ">": v > b, "<": v < b, "<=": v <= b}[op]


def satisfies(version: str, pragma: str) -> bool:
    v = parse(version)
    for alt in pragma.split("||"):
        conds = []
        for m in _CMP.finditer(alt):
            op = m.group(1) or ""
            maj = int(m.group(2))
            mi = None if m.group(3) in (None, "x", "*") else int(m.group(3))
            pa = None if m.group(4) in (None, "x", "*") else int(m.group(4))
            conds += _expand(op, maj, mi, pa)
        if conds and all(_ok(v, op, b) for op, b in conds):
            return True
    return False


def satisfies_all(version: str, pragmas: Iterable[str]) -> bool:
    return all(satisfies(version, p) for p in pragmas)


def sort_desc(versions: Iterable[str]) -> List[str]:
    return sorted(set(versions), key=parse, reverse=True)
