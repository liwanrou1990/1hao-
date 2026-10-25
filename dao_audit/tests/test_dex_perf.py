"""Deterministic tests for the Tier-1 flagging rule (no network).

These lock in the SAFE-UPPER-BOUND property the fast scanner relies on: a pair is
only ever dropped when it is PROVABLY unprofitable, and any data gap keeps the
pair (INSUFFICIENT_DATA), never reads as "no arb"."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.dex.perf import flag_decision, GROSS_FLOOR_BPS  # noqa: E402


def test_positive_gross_always_flagged():
    # any profitable path has positive gross -> must be flagged
    flagged, status, _ = flag_decision(12.5, True, False, False)
    assert flagged and status == "OK"


def test_near_breakeven_within_floor_flagged():
    # slightly negative gross but within the floor -> kept for Tier-2
    flagged, status, _ = flag_decision(-5.6, True, False, False)
    assert flagged and status == "OK"
    # exactly at the floor boundary -> still flagged (>=)
    assert flag_decision(-GROSS_FLOOR_BPS, True, False, False)[0] is True


def test_deeply_negative_gross_safely_dropped():
    # far below the floor across ALL venues -> safe to drop
    flagged, status, reason = flag_decision(-120.0, True, False, False)
    assert flagged is False and status == "OK"
    assert "安全过滤" in reason


def test_floor_exceeds_plausible_cost_margin():
    # the floor must be well below any realistic breakeven gross so a true
    # candidate (net>0 => gross>0) is never anywhere near the drop threshold
    assert GROSS_FLOOR_BPS >= 20.0
    # a path that is net-positive has gross>0, which is >= -floor by a wide margin
    assert flag_decision(0.1, True, False, False)[0] is True


def test_insufficient_data_never_reads_as_no_arb():
    # no usable round-trip quote for the pair -> keep + INSUFFICIENT_DATA
    flagged, status, reason = flag_decision(None, False, False, True)
    assert flagged is True and status == "INSUFFICIENT_DATA"
    assert "无套利" in reason or "数据不足" in reason
    # even a deeply-negative gross is kept if the pair is marked insufficient
    flagged2, status2, _ = flag_decision(-999.0, True, False, True)
    assert flagged2 is True and status2 == "INSUFFICIENT_DATA"


def test_deferred_venue_conservatively_kept():
    # a venue we could not price in-batch -> cannot prove unprofitable -> keep
    flagged, status, _ = flag_decision(-500.0, True, True, False)
    assert flagged is True and status == "OK"


def test_missing_gross_without_insufficient_flag_still_kept():
    # gross unknown but not marked insufficient -> still conservatively kept
    flagged, status, _ = flag_decision(None, False, False, False)
    assert flagged is True and status == "INSUFFICIENT_DATA"
