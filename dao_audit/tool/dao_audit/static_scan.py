"""Heuristic static scanner over authorized Solidity sources.

Flags candidate locations for each of the five governance risk classes. This is
a signature-based triage layer, not a full semantic analyzer: every hit is
corroborated (or dismissed) by the dynamic PoC/regression evidence.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .rules import RULES, Rule
from .authorization import Authorization


@dataclass
class StaticHit:
    rule_id: str
    title: str
    severity: str
    file: str
    line: int
    snippet: str
    reason: str
    mitigated_hint: bool


def _strip_for_scan(text: str) -> List[str]:
    """Return lines with // comments blanked so annotations don't create hits."""
    out = []
    for ln in text.splitlines():
        # drop trailing line comments but keep code before them
        idx = ln.find("//")
        out.append(ln[:idx] if idx >= 0 else ln)
    return out


def scan_file(path: Path, auth: Authorization) -> List[StaticHit]:
    auth.assert_file_authorized(path)
    raw = path.read_text(encoding="utf-8")
    scan_lines = _strip_for_scan(raw)
    body = "\n".join(scan_lines)
    rel = str(path)
    hits: List[StaticHit] = []

    for rule in RULES:
        mitigated = any(re.search(mp, body, re.MULTILINE) for mp, _ in rule.mitigations)
        for pattern, why in rule.signatures:
            for m in re.finditer(pattern, body, re.MULTILINE):
                line_no = body.count("\n", 0, m.start()) + 1
                snippet = scan_lines[line_no - 1].strip()[:160]
                hits.append(StaticHit(
                    rule_id=rule.id, title=rule.title, severity=rule.severity,
                    file=rel, line=line_no, snippet=snippet, reason=why,
                    mitigated_hint=mitigated,
                ))
    return hits


def scan_authorized(auth: Authorization) -> List[StaticHit]:
    hits: List[StaticHit] = []
    for f in auth.authorized_sol_files():
        hits.extend(scan_file(f, auth))
    return hits


def hits_as_dicts(hits: List[StaticHit]):
    return [asdict(h) for h in hits]
