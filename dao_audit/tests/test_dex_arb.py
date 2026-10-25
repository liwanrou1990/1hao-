"""Deterministic unit tests for the DEX arb cost model (no network).
Synthetic inputs test the arithmetic only; live scanning uses real on-chain
quotes or returns NO_TRADE."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.dex.scan import net_after_costs, _token_usd  # noqa: E402


COSTS = {"flashloan_bps": 5, "mev_haircut_bps": 2, "fail_probability": 0.15,
         "use_flashloan": False}


def test_token_usd_map():
    assert _token_usd("USDC", None) == 1.0
    assert _token_usd("USDT", None) == 1.0
    assert _token_usd("WETH", 2500.0) == 2500.0
    assert _token_usd("UNKNOWN", 2500.0) is None


def test_positive_gross_survives_only_if_above_costs():
    # tiny gross, cheap L2 gas -> net still positive when gross clearly exceeds costs
    cb = net_after_costs(gross_usd=10.0, n_usd=10000, gas_price_wei=6_000_000,
                         gas_units=150000, eth_price=2400, costs=COSTS)
    # mev = 10000*2/1e4 = 2.0 ; gas tiny ; net ~ 10 - ~2 > 0
    assert cb["net_usd"] > 0
    assert cb["mev_usd"] == 2.0


def test_costs_dominate_small_spread():
    cb = net_after_costs(gross_usd=0.5, n_usd=10000, gas_price_wei=20_000_000,
                         gas_units=800000, eth_price=2400, costs=COSTS)
    assert cb["net_usd"] < 0  # 0.5 gross < ~2 mev + gas -> negative


def test_flashloan_fee_applied_only_when_enabled():
    off = net_after_costs(100, 100000, 6_000_000, 150000, 2400, dict(COSTS, use_flashloan=False))
    on = net_after_costs(100, 100000, 6_000_000, 150000, 2400, dict(COSTS, use_flashloan=True))
    assert on["flash_usd"] == 50.0  # 100000*5/1e4
    assert off["flash_usd"] == 0.0
    assert on["net_usd"] < off["net_usd"]


def test_failure_cost_scales_with_gas():
    cb = net_after_costs(0, 1000, 20_000_000, 800000, 2400, COSTS)
    assert abs(cb["fail_usd"] - 0.15 * cb["gas_usd"]) < 1e-3  # within rounding


# --- ranked report logic (pure) ---
from dao_audit.dex.scan import rank_report  # noqa: E402


def _scan_rep(nets):
    return {"same_chain": [
        {"chain": "base", "pair": "WETH/USDC", "block": 1, "decision": "NO_TRADE",
         "best": {"net_usd": nets[0], "buy_on": "A", "sell_on": "B", "size_usd": 1000}},
        {"chain": "arbitrum", "pair": "USDC/USDT", "block": 2, "decision": "NO_TRADE",
         "best": {"net_usd": nets[1], "buy_on": "C", "sell_on": "D", "size_usd": 1000}},
    ]}


def test_rank_sorts_by_net_desc_and_no_trade_when_all_negative():
    rep = rank_report(_scan_rep([-0.64, -0.57]))
    assert rep["verdict"] == "NO_TRADE"
    assert rep["n_feasible"] == 0
    # -0.57 ranks above -0.64
    assert rep["ranked"][0]["pair"] == "USDC/USDT"


def test_positive_net_feasible_only_when_fork_verified():
    # positive net but NO fork verification -> not feasible
    rep = rank_report(_scan_rep([5.0, -0.57]), roundtrip_fork_verified=False)
    assert rep["n_feasible"] == 0 and rep["verdict"] == "NO_TRADE"
    # positive net AND fork verified -> feasible candidate
    rep2 = rank_report(_scan_rep([5.0, -0.57]), roundtrip_fork_verified=True)
    assert rep2["n_feasible"] == 1 and rep2["verdict"] == "CANDIDATES"
    assert rep2["ranked"][0]["simulated_feasible"] is True
