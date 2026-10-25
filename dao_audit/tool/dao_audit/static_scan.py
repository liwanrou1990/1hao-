"""Static scan surface over authorized Solidity sources.

This now delegates to the structural detector engine (analysis/detectors.py),
which understands contract/function structure rather than matching raw regex.
The legacy StaticHit shape is preserved so existing callers/tests keep working;
`analyze_file` / `analyze_authorized` expose the rich Finding objects.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .authorization import Authorization
from .analysis.solmodel import parse_source
from .analysis.detectors import run_detectors, Finding


@dataclass
class StaticHit:
    rule_id: str
    title: str
    severity: str
    file: str
    line: int
    snippet: str
    reason: str
    mitigated_hint: bool  # detectors fire only when NOT mitigated -> always False


def analyze_file(path: Path, auth: Authorization) -> List[Finding]:
    auth.assert_file_authorized(path)
    su = parse_source(path)
    return run_detectors(su)


def analyze_authorized(auth: Authorization) -> List[Finding]:
    findings: List[Finding] = []
    for f in auth.authorized_sol_files():
        su = parse_source(f)
        findings.extend(run_detectors(su))
    return findings


def _to_hit(f: Finding) -> StaticHit:
    return StaticHit(
        rule_id=f.rule_id, title=f.title, severity=f.severity,
        file=f.file, line=f.line, snippet=f.code, reason=f.root_cause,
        mitigated_hint=False,
    )


def scan_file(path: Path, auth: Authorization) -> List[StaticHit]:
    return [_to_hit(f) for f in analyze_file(path, auth)]


def scan_authorized(auth: Authorization) -> List[StaticHit]:
    return [_to_hit(f) for f in analyze_authorized(auth)]


def hits_as_dicts(hits: List[StaticHit]):
    return [asdict(h) for h in hits]
