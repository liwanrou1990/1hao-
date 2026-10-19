"""交易成本与成交模型：手续费、价差、Gas、滑点、价格冲击、成交失败与部分成交。

成本按单边计算；买入成交价 = 参考价 × (1 + 单边比例成本) ，卖出 = 参考价 × (1 − 单边比例成本)，再加固定 Gas。
价格冲击：impact = impact_coeff × daily_vol × sqrt(order_usd / depth_usd)（平方根冲击模型，Almgren 类）。
容量：order_usd > max_tx_usd 需拆单；order_usd > depth_usd × 0.1 标记为“不可验证”。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from ..config import load_yaml


@dataclass(slots=True)
class CostProfile:
    name: str
    fee_bps: float
    spread_bps: float
    gas_usd: float
    slippage_bps: float
    impact_coeff: float
    depth_usd: float
    max_tx_usd: float
    min_tx_usd: float
    fill_prob: float
    partial_fill_prob: float
    partial_fill_min_ratio: float
    daily_vol: float = 0.02     # 冲击模型使用的日波动率（可由实际数据覆盖）
    description: str = ""

    def scaled(self, cost_mult: float = 1.0, slippage_mult: float = 1.0, depth_mult: float = 1.0,
               fill_prob_override: float | None = None) -> "CostProfile":
        d = asdict(self)
        d["fee_bps"] *= cost_mult
        d["spread_bps"] *= cost_mult
        d["gas_usd"] *= cost_mult
        d["slippage_bps"] *= slippage_mult
        d["impact_coeff"] *= slippage_mult
        d["depth_usd"] *= depth_mult
        if fill_prob_override is not None:
            d["fill_prob"] = fill_prob_override
        return CostProfile(**d)


def load_cost_profile(name: str | None = None) -> CostProfile:
    cfg = load_yaml("costs.yaml")
    name = name or cfg["default_profile"]
    p = cfg["profiles"][name]
    return CostProfile(name=name, **{k: v for k, v in p.items()})


@dataclass(slots=True)
class Fill:
    side: str
    ref_price: float
    exec_price: float
    qty: float
    notional_usd: float
    fee_usd: float
    spread_usd: float
    slippage_usd: float
    impact_usd: float
    gas_usd: float
    filled: bool
    fill_ratio: float
    unverifiable: bool
    note: str = ""

    @property
    def total_cost_usd(self) -> float:
        return self.fee_usd + self.spread_usd + self.slippage_usd + self.impact_usd + self.gas_usd


class CostModel:
    def __init__(self, profile: CostProfile, rng: np.random.Generator | None = None, deterministic: bool = True):
        self.p = profile
        self.rng = rng or np.random.default_rng(7)
        self.deterministic = deterministic  # True: 不抽样成交失败（用于 DP 与可复现回测），False: 蒙特卡洛

    # ---- 比例成本（单边，不含冲击、Gas） ----
    def proportional_side_cost(self) -> float:
        return (self.p.fee_bps + self.p.spread_bps + self.p.slippage_bps) / 1e4

    def impact_fraction(self, order_usd: float) -> float:
        if order_usd <= 0 or self.p.depth_usd <= 0:
            return 0.0
        return self.p.impact_coeff * self.p.daily_vol * math.sqrt(order_usd / self.p.depth_usd)

    def round_trip_fraction(self, order_usd: float) -> float:
        """买+卖合计比例成本（含冲击，不含 Gas）。"""
        return 2 * (self.proportional_side_cost() + self.impact_fraction(order_usd))

    def break_even_move(self, order_usd: float) -> float:
        """达到盈亏平衡所需的价格涨幅（比例）。"""
        return self.round_trip_fraction(order_usd) + (2 * self.p.gas_usd / order_usd if order_usd > 0 else 0.0)

    def is_unverifiable(self, order_usd: float) -> bool:
        return order_usd > 0.1 * self.p.depth_usd

    def n_child_orders(self, order_usd: float) -> int:
        return max(1, math.ceil(order_usd / self.p.max_tx_usd)) if order_usd > 0 else 0

    # ---- 单笔成交 ----
    def execute(self, side: str, ref_price: float, notional_usd: float) -> Fill:
        side = side.lower()
        assert side in ("buy", "sell")
        if notional_usd < self.p.min_tx_usd or ref_price <= 0:
            return Fill(side, ref_price, ref_price, 0, 0, 0, 0, 0, 0, 0, False, 0.0, False, "低于最小成交额")
        n_child = self.n_child_orders(notional_usd)
        impact = self.impact_fraction(notional_usd)     # 冲击按总订单规模（拆单不降低总冲击，只增加 Gas）
        unver = self.is_unverifiable(notional_usd)
        fill_ratio = 1.0
        filled = True
        note = ""
        if not self.deterministic:
            if self.rng.random() > self.p.fill_prob:
                return Fill(side, ref_price, ref_price, 0, 0, 0, 0, 0, 0, self.p.gas_usd * n_child, False, 0.0, unver, "成交失败（已付 Gas）")
            if self.rng.random() < self.p.partial_fill_prob:
                fill_ratio = float(self.rng.uniform(self.p.partial_fill_min_ratio, 1.0))
                note = "部分成交"
        sgn = 1 if side == "buy" else -1
        frac = (self.p.fee_bps + self.p.spread_bps + self.p.slippage_bps) / 1e4 + impact
        exec_price = ref_price * (1 + sgn * frac)
        filled_usd = notional_usd * fill_ratio
        qty = filled_usd / ref_price
        if unver:
            note = (note + "；" if note else "") + f"订单 {notional_usd:,.0f} USD 超过流动性深度 10%，成交不可验证"
        return Fill(side=side, ref_price=ref_price, exec_price=exec_price, qty=qty, notional_usd=filled_usd,
                    fee_usd=filled_usd * self.p.fee_bps / 1e4, spread_usd=filled_usd * self.p.spread_bps / 1e4,
                    slippage_usd=filled_usd * self.p.slippage_bps / 1e4, impact_usd=filled_usd * impact,
                    gas_usd=self.p.gas_usd * n_child, filled=filled, fill_ratio=fill_ratio, unverifiable=unver, note=note)

    def describe(self) -> dict:
        return asdict(self.p)
