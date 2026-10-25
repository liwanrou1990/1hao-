"""Trade economics: gross, fees, gas, slippage, price impact, net.

Every input must be real. If any required input is missing (e.g. gas price or
ETH price unavailable because RPC/price egress is blocked), the result is
NO_TRADE with reason INSUFFICIENT_DATA — never a guessed number.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class CostInputs:
    notional_usd: float
    expected_gross_bps: float          # expected favorable move, from real signal
    dex_fee_bps: float
    slippage_bps: float
    gas_limit: int
    gas_price_gwei: Optional[float]    # real, from RPC
    eth_price_usd: Optional[float]     # real, from price source
    pool_liquidity_usd: Optional[float] = None  # real, for price impact
    round_trip: bool = True


@dataclass
class CostResult:
    status: str                        # OK | INSUFFICIENT_DATA
    reason: Optional[str]
    notional_usd: float
    gross_usd: Optional[float] = None
    fee_usd: Optional[float] = None
    slippage_usd: Optional[float] = None
    price_impact_usd: Optional[float] = None
    gas_usd: Optional[float] = None
    net_usd: Optional[float] = None
    net_bps: Optional[float] = None
    missing: Optional[list] = None

    def to_dict(self):
        return asdict(self)


def compute(ci: CostInputs) -> CostResult:
    missing = []
    if ci.gas_price_gwei is None:
        missing.append("gas_price_gwei")
    if ci.eth_price_usd is None:
        missing.append("eth_price_usd")
    if ci.pool_liquidity_usd is None:
        missing.append("pool_liquidity_usd")
    if missing:
        return CostResult(status="INSUFFICIENT_DATA",
                          reason=f"缺少真实输入: {', '.join(missing)}",
                          notional_usd=ci.notional_usd, missing=missing)

    n = ci.notional_usd
    gross = n * ci.expected_gross_bps / 1e4
    legs = 2 if ci.round_trip else 1
    fee = n * (ci.dex_fee_bps * legs) / 1e4
    slippage = n * (ci.slippage_bps * legs) / 1e4
    # constant-product price impact approximation: impact_bps ≈ notional/liquidity*1e4
    impact_bps = (n / ci.pool_liquidity_usd) * 1e4
    price_impact = n * impact_bps / 1e4 * legs
    gas_usd = ci.gas_limit * ci.gas_price_gwei * 1e-9 * ci.eth_price_usd
    net = gross - fee - slippage - price_impact - gas_usd
    return CostResult(status="OK", reason=None, notional_usd=n,
                      gross_usd=gross, fee_usd=fee, slippage_usd=slippage,
                      price_impact_usd=price_impact, gas_usd=gas_usd,
                      net_usd=net, net_bps=net / n * 1e4)
