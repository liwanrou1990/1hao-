"""Smoke tests for the dao_audit tool: authorization boundary, static scan,
report assembly. Run with: PYTHONPATH=tool pytest dao_audit/tests -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.authorization import Authorization, AuthorizationError  # noqa: E402
from dao_audit.static_scan import scan_authorized  # noqa: E402
from dao_audit.report import build_findings  # noqa: E402
from dao_audit.rules import RULES  # noqa: E402
import pytest  # noqa: E402


def _auth():
    return Authorization(ROOT, ROOT / "config/authorization.yaml")


def test_authorized_scope_lists_targets_and_fixed():
    files = _auth().authorized_sol_files()
    assert any("targets/VulnX100Governor.sol" in str(f) for f in files)
    assert len(files) >= 8


def test_unauthorized_file_refused():
    auth = _auth()
    with pytest.raises(AuthorizationError):
        auth.assert_file_authorized(ROOT / "foundry.toml")


def test_mainnet_rpc_refused():
    auth = _auth()
    auth.assert_local_rpc("http://127.0.0.1:8545")  # ok
    for bad in ("https://mainnet.infura.io/v3/x", "https://eth.llamarpc.com"):
        with pytest.raises(AuthorizationError):
            auth.assert_local_rpc(bad)


def test_static_scan_flags_all_five_classes():
    hits = scan_authorized(_auth())
    flagged = {h.rule_id for h in hits if "/targets/" in h.file}
    assert flagged == {"VULN-1", "VULN-2", "VULN-3", "VULN-4", "VULN-5"}


def test_build_findings_without_dynamic_evidence_marks_suspected():
    hits = scan_authorized(_auth())
    findings = build_findings(hits, {})  # no forge results
    assert len(findings) == len(RULES)
    for f in findings:
        assert f["status"] in ("SUSPECTED", "NOT_OBSERVED")
        assert f["locations"], f"{f['id']} should have a target location"


def test_findings_sorted_by_severity():
    hits = scan_authorized(_auth())
    findings = build_findings(hits, {})
    sev = [f["severity"] for f in findings]
    assert sev[0] == "Critical"
