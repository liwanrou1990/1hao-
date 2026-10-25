"""Deterministic unit tests for the hourly event-study engine (no network).
Tests categorization, reaction labeling, slippage/cost scaling, and that
capital-scaled net shrinks as size grows. Synthetic inputs test arithmetic
only; live study uses real data or returns INSUFFICIENT_DATA."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.market.events_study import (  # noqa: E402
    classify, _bull_bear, _slippage_bps, _net_by_size)


def test_categorization_priority():
    assert classify("Adjust LDO emissions and burn", "token supply change") == "tokenomics"
    assert classify("Liquidity incentive for pool", "add gauge rewards") == "liquidity_incentive"
    assert classify("Treasury diversification", "allocate budget grant") == "treasury_major"
    assert classify("Elect a delegate", "procedural vote") == "governance_generic"


def test_reaction_labeling():
    assert _bull_bear(0.02, 100) == "bullish"     # +200bps > +100
    assert _bull_bear(-0.02, 100) == "bearish"    # -200bps < -100
    assert _bull_bear(0.005, 100) == "neutral"    # +50bps within band


def test_slippage_scales_with_size():
    s1 = _slippage_bps(10_000, 5, 100_000)
    s2 = _slippage_bps(100_000, 5, 100_000)
    s3 = _slippage_bps(1_000_000, 5, 100_000)
    assert s1 < s2 < s3
    assert round(s2, 2) == 5.0  # at reference size slippage == base


def test_net_shrinks_with_capital():
    cost = {"dex_fee_bps": 30, "slippage_base_bps": 5,
            "slippage_ref_usd": 100_000, "gas_usd_assumption": 15}
    rows = _net_by_size(0.03, [10_000, 100_000, 1_000_000], cost)  # 300bps gross edge
    nets = [r["net_bps"] for r in rows]
    # larger size -> more slippage -> lower net (but less gas drag); slippage dominates at 1M
    assert nets[2] < nets[1]
    # gas drag huge at small size
    assert rows[0]["gas_bps"] > rows[2]["gas_bps"]
