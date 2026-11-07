"""交易成本：手续费（taker）、滑点、止损额外滑点、资金费率（有数据用数据，无数据用保守假设）。单位统一为“每单位标的的价格量”。"""
from __future__ import annotations


def _c(costs: dict, instrument: str) -> dict:
    return costs.get(instrument, costs.get("perp", {}))


def estimate_round_trip_cost(entry: float, stop: float, costs: dict, instrument: str, direction: str, hold_bars: int = 96, bar_seconds: int = 900,
                             funding_rate_8h: float | None = None) -> dict:
    c = _c(costs, instrument)
    fee = float(c["taker_fee_bps"]) / 1e4
    slip = float(c["slippage_bps"]) / 1e4
    stop_slip = float(costs.get("stop_slippage_bps", 0)) / 1e4
    entry_cost = entry * (fee + slip)
    exit_cost_stop = stop * (fee + slip + stop_slip)
    funding = 0.0
    if instrument == "perp":
        f = costs.get("funding", {})
        rate = float(funding_rate_8h) if funding_rate_8h is not None else float(f.get("assumed_rate_8h", 0.0))
        n_periods = hold_bars * bar_seconds / (8 * 3600)
        if direction == "long" and f.get("long_pays", True):
            funding = max(rate, 0.0) * entry * n_periods
        elif direction == "short":
            funding = (max(-rate, 0.0) * entry * n_periods) if rate < 0 else (0.0 if not f.get("short_receives", False) else -rate * entry * n_periods)
    total = entry_cost + exit_cost_stop + max(funding, 0.0)
    return {"cost_per_unit": total, "cost_bps": total / entry * 1e4, "entry_cost": entry_cost, "exit_cost_stop": exit_cost_stop, "funding_est": funding}


def net_rr(entry: float, stop: float, tp: float, cost_per_unit: float) -> float:
    risk = abs(entry - stop) + cost_per_unit
    reward = abs(tp - entry) - cost_per_unit
    return reward / risk if risk > 0 else 0.0
