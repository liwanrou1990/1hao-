"""仓位计算：按账户净值 × 单笔风险预算、入场价、结构止损价得到数量；合约含杠杆、保证金、估算强平价与数量精度。

loss_at_stop = qty × (|entry − stop| + 往返成本/单位) ≤ equity × risk_pct  → qty。
合约：杠杆 = min(max_leverage, 所需)；逐仓强平价估算（多）≈ entry × (1 − 1/L + mmr)；要求 |entry−liq| ≥ |entry−stop| × (1 + liquidation_buffer)，
否则降杠杆（加保证金）；保证金超过可用净值则按可用净值缩减数量并如实报告“风险预算未用满”。
现货：只做多，名义额 ≤ 净值；无杠杆、无强平。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .costs import estimate_round_trip_cost


@dataclass
class SizingResult:
    ok: bool
    qty: float = 0.0
    notional: float = 0.0
    risk_usd: float = 0.0
    risk_pct_effective: float = 0.0
    leverage: float = 0.0
    margin: float = 0.0
    liq_price: float | None = None
    instrument: str = "perp"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def round_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


def size_position(equity: float, entry: float, stop: float, direction: str, symbol: str, risk_cfg: dict, costs_cfg: dict, instrument: str,
                  risk_pct: float | None = None, hold_bars: int = 96, bar_seconds: int = 900, funding_rate_8h: float | None = None) -> SizingResult:
    r = SizingResult(False, instrument=instrument)
    rp = float(risk_pct if risk_pct is not None else risk_cfg["risk_per_trade"])
    rp = min(max(rp, float(risk_cfg["risk_per_trade_min"])), float(risk_cfg["risk_per_trade_max"]))
    inst = risk_cfg["instruments"].get(symbol)
    if inst is None:
        r.reasons.append(f"{symbol} 未配置合约/现货规格"); return r
    if not inst.get(instrument, False):
        r.reasons.append(f"{symbol} 不支持 {instrument}"); return r
    if instrument == "spot" and direction == "short":
        r.reasons.append("现货不能做空"); return r
    dist = abs(entry - stop)
    if dist <= 0 or equity <= 0:
        r.reasons.append("止损距离或净值非正"); return r
    cost = estimate_round_trip_cost(entry, stop, costs_cfg, instrument, direction, hold_bars, bar_seconds, funding_rate_8h)
    risk_budget = equity * rp
    qty = risk_budget / (dist + cost["cost_per_unit"])
    # 合约：杠杆/保证金/强平
    lev = 1.0; liq = None
    if instrument == "perp":
        max_lev = float(risk_cfg["max_leverage"]); mmr = float(risk_cfg["maintenance_margin_rate"]); buf = float(risk_cfg["liquidation_buffer"])
        notional = qty * entry
        lev = min(max_lev, max(notional / equity, 1.0))
        # 强平距离需 ≥ 止损距离 × (1+buf)：1/L − mmr ≥ dist/entry × (1+buf) → L ≤ 1 / (dist/entry×(1+buf) + mmr)
        lev_cap = 1.0 / (dist / entry * (1 + buf) + mmr)
        if lev_cap < 1.0:
            r.reasons.append(f"止损距离 {dist/entry:.2%} 过大，即使 1 倍杠杆也无法满足强平缓冲"); return r
        lev = min(lev, lev_cap)
        margin = notional / lev
        if margin > equity:
            # 保证金不足：按净值缩减
            notional = equity * lev; qty = notional / entry
            margin = equity
            r.reasons.append("保证金受净值限制，数量已缩减（风险预算未用满）")
        liq = entry * (1 - 1 / lev + mmr) if direction == "long" else entry * (1 + 1 / lev - mmr)
    else:
        notional = qty * entry
        if notional > equity:
            qty = equity / entry; notional = equity
            r.reasons.append("现货名义额受净值限制，数量已缩减（风险预算未用满）")
        margin = notional
    qty = round_step(qty, float(inst.get("qty_step", 0.0001)))
    if qty <= 0:
        r.reasons.append("数量按精度取整后为 0"); return r
    notional = qty * entry
    if notional < float(inst.get("min_notional", 0)):
        r.reasons.append(f"名义额 {notional:.2f} 低于最小 {inst.get('min_notional')}"); return r
    r.ok = True
    r.qty, r.notional, r.leverage, r.margin, r.liq_price = qty, notional, lev, (notional / lev if instrument == "perp" else notional), liq
    r.risk_usd = qty * (dist + cost["cost_per_unit"])
    r.risk_pct_effective = r.risk_usd / equity
    r.reasons.append(f"风险预算 {rp:.2%} × 净值 {equity:.0f} = {risk_budget:.2f}；止损距离 {dist:.2f} + 成本 {cost['cost_per_unit']:.2f}/单位 → 数量 {qty:.6g}")
    return r
