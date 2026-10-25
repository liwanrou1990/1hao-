"""Deterministic unit tests for the real-data market engine.

These test the ENGINE ARITHMETIC (cost/decision/backtest/dashboard) with known
synthetic inputs. Synthetic numbers here are test fixtures for the calculator
only; they are never fed into live scans/dashboards, which use real data or
mark it UNAVAILABLE. Network is not touched by these tests.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.market.costs import CostInputs, compute  # noqa: E402
from dao_audit.market.decision import decide, TRADE, NO_TRADE  # noqa: E402
from dao_audit.market.backtest import Event, run_backtest  # noqa: E402
from dao_audit.market import dashboard as DASH  # noqa: E402


def _full_inputs(gross_bps, notional=10000, liq=5_000_000):
    return CostInputs(notional_usd=notional, expected_gross_bps=gross_bps,
                      dex_fee_bps=30, slippage_bps=10, gas_limit=180000,
                      gas_price_gwei=20.0, eth_price_usd=3000.0,
                      pool_liquidity_usd=liq, round_trip=True)


def test_cost_missing_real_input_is_insufficient_data():
    ci = _full_inputs(100); ci.gas_price_gwei = None
    r = compute(ci)
    assert r.status == "INSUFFICIENT_DATA"
    assert "gas_price_gwei" in r.missing


def test_cost_math_is_deterministic():
    r = compute(_full_inputs(gross_bps=100))  # 1.00% gross
    # fees 2*30bps, slippage 2*10bps, impact 2*(10000/5e6)=2*2bps, gas computed
    assert r.status == "OK"
    assert round(r.fee_usd, 2) == 60.0      # 10000*0.0030*2
    assert round(r.slippage_usd, 2) == 20.0 # 10000*0.0010*2
    assert r.gas_usd and r.gas_usd > 0
    assert r.net_bps is not None


def test_decision_unavailable_forces_no_trade():
    r = compute(_full_inputs(1000))
    d = decide(r, evidence_sources=3, data_status="UNAVAILABLE",
               min_net_bps=15, min_evidence_sources=2)
    assert d.decision == NO_TRADE and "UNAVAILABLE" in d.reason


def test_decision_insufficient_sources_no_trade():
    r = compute(_full_inputs(1000))
    d = decide(r, evidence_sources=1, data_status="OK",
               min_net_bps=15, min_evidence_sources=2)
    assert d.decision == NO_TRADE


def test_decision_nonpositive_net_no_trade():
    r = compute(_full_inputs(gross_bps=1))  # tiny gross -> net negative after costs
    d = decide(r, evidence_sources=2, data_status="OK",
               min_net_bps=15, min_evidence_sources=2)
    assert d.decision == NO_TRADE


def test_decision_trade_only_when_everything_passes():
    r = compute(_full_inputs(gross_bps=200))  # 2% gross, well above costs
    d = decide(r, evidence_sources=2, data_status="OK",
               min_net_bps=15, min_evidence_sources=2)
    assert d.decision == TRADE and d.net_bps and d.net_bps >= 15


def test_backtest_no_lookahead_and_splits():
    # deterministic synthetic price path (engine arithmetic only)
    prices = {("X", t): 100.0 + t for t in range(0, 100)}  # +1 per second
    def price_at(coin, ts):
        return prices.get((coin, ts))
    events = [Event(ts=t, coin_key="X", label="e") for t in range(0, 40, 4)]
    bt = run_backtest(events, horizon_s=2, cost_bps=0.0, price_at=price_at)
    assert bt["status"] == "OK"
    assert bt["no_lookahead"] is True
    assert set(bt["splits"]) == {"train", "validation", "oos"}
    # every window had priced trades; rising path -> positive returns
    assert bt["splits"]["train"]["n_trades"] >= 1


def test_backtest_insufficient_data_when_prices_missing():
    events = [Event(ts=t, coin_key="X", label="e") for t in range(0, 12)]
    bt = run_backtest(events, horizon_s=2, cost_bps=0.0,
                      price_at=lambda c, ts: None)  # no real data
    assert bt["status"] == "INSUFFICIENT_DATA"


def test_dashboard_shows_no_trade_banner_when_no_real_data():
    scan = {"generated_at": "t", "chains": {"ethereum": {"status": "UNAVAILABLE",
            "error": "403"}}, "daos": [], "tokens": [], "opportunities": [],
            "summary": {"any_real_data": False, "trade_signals": 0, "no_trade": 0}}
    html = DASH.render(scan)
    assert "NO TRADE" in html and "模拟行情" in html
