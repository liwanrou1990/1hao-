"""逐档容量模拟：仓位、流动性、价差、手续费、Gas、滑点、冲击、成交失败/部分成交；无法验证的规模标记为不可验证。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel, CostProfile
from .tiers import Tier, TierConfig


@dataclass
class CapacityRow:
    tier_id: str
    point: str
    capital_usd: float
    order_usd: float
    child_orders: int
    side_cost_pct: float
    impact_pct: float
    round_trip_pct: float
    gas_usd_round_trip: float
    break_even_move_pct: float
    pct_of_depth: float
    pct_of_adv: float | None
    unverifiable: bool
    target_low_pct: float
    target_high_pct: float
    period_days: int
    feasible_vs_breakeven: bool            # 目标下限 > 往返成本 才有可能达标
    notes: list[str] = field(default_factory=list)


def capacity_table(cfg: TierConfig, profile: CostProfile, adv_usd: float | None = None,
                   position_fraction: float = 1.0) -> pd.DataFrame:
    rows = []
    cm = CostModel(profile)
    for t in cfg.tiers:
        for pt, cap in t.representative_capitals(cfg.representative_points).items():
            order = cap * position_fraction
            n_child = cm.n_child_orders(order)
            impact = cm.impact_fraction(order)
            side = cm.proportional_side_cost()
            rt = 2 * (side + impact)
            gas_rt = 2 * profile.gas_usd * n_child
            be = rt + gas_rt / order
            unver = cm.is_unverifiable(order)
            notes = []
            if unver:
                notes.append(f"单日订单 {order:,.0f} USD 超过深度假设 {profile.depth_usd:,.0f} 的 10%，成交不可验证")
            if n_child > 1:
                notes.append(f"超过单笔上限 {profile.max_tx_usd:,.0f}，需拆 {n_child} 笔，Gas 相应增加")
            pct_adv = order / adv_usd if adv_usd else None
            if pct_adv is not None and pct_adv > 0.01:
                notes.append(f"订单占原生日成交额 {pct_adv:.2%}，代币池通常远小于此，容量不可验证")
                unver = True
            feasible = t.target_daily_pct[0] / 100 > be
            if not feasible:
                notes.append(f"目标下限 {t.target_daily_pct[0]}%/日 不足以覆盖单次往返盈亏平衡 {be:.2%}，单笔交易无法达标")
            rows.append(CapacityRow(t.id, pt, cap, order, n_child, side * 100, impact * 100, rt * 100, gas_rt, be * 100,
                                    order / profile.depth_usd, pct_adv, unver, *t.target_daily_pct, t.period_days, feasible, notes))
    return pd.DataFrame([r.__dict__ for r in rows])


def target_attainment(daily_returns: pd.Series, tier: Tier, accrual_note: str = "计息口径未知，按“单日净收益≥区间下限”统计") -> dict:
    """达标率：有多少交易日净收益 ≥ 目标下限；周期目标按单利/复利两种口径分别给出、并标注口径未知。"""
    lo, hi = tier.target_daily_pct[0] / 100, tier.target_daily_pct[1] / 100
    dr = daily_returns.dropna()
    n = len(dr)
    if n == 0:
        return {"tier": tier.id, "n_days": 0, "attained_days": 0, "attainment_rate": None, "met": False, "note": "无数据"}
    hit = int((dr >= lo).sum())
    simple_period = float(dr.tail(tier.period_days).sum()) if n >= tier.period_days else None
    compound_period = float((1 + dr.tail(tier.period_days)).prod() - 1) if n >= tier.period_days else None
    period_target_lo = lo * tier.period_days
    return {"tier": tier.id, "target_daily_pct": [lo * 100, hi * 100], "period_days": tier.period_days,
            "n_days": n, "attained_days": hit, "attainment_rate": hit / n,
            "mean_daily_ret_pct": float(dr.mean() * 100), "median_daily_ret_pct": float(dr.median() * 100),
            "period_simple_sum_pct": None if simple_period is None else simple_period * 100,
            "period_compound_pct": None if compound_period is None else compound_period * 100,
            "period_target_simple_pct": period_target_lo * 100,
            "met": bool(dr.mean() >= lo),
            "status": "达标" if dr.mean() >= lo else "未达标",
            "note": accrual_note}
