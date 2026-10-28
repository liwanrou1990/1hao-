"""持仓成本 / 止损 / 潜在亏损计算（纯算术，含手续费）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import cfg


@dataclass
class Tranche:
    price: float
    amount_usdt: float


@dataclass
class PositionPlan:
    tranches: list[Tranche] = field(default_factory=list)
    stop_loss_pct: float = 0.15           # 相对平均成本的止损幅度
    fee_bps: float | None = None
    slippage_bps: float | None = None

    def compute(self, current_price: float) -> dict:
        c = cfg()["costs"]
        fee = (c["taker_fee_bps"] if self.fee_bps is None else self.fee_bps) / 1e4
        slip = (c["slippage_bps_market"] if self.slippage_bps is None else self.slippage_bps) / 1e4
        buy_cost = fee + slip
        total_usdt = sum(t.amount_usdt for t in self.tranches)
        qty = sum(t.amount_usdt * (1 - buy_cost) / t.price for t in self.tranches) if self.tranches else 0.0
        avg_cost = total_usdt / qty if qty else None                       # 含买入成本的持仓单价
        breakeven = avg_cost / (1 - buy_cost) if avg_cost else None         # 卖出也要扣费
        stop_price = avg_cost * (1 - self.stop_loss_pct) if avg_cost else None
        value_now = qty * current_price * (1 - buy_cost)
        pnl_now = value_now - total_usdt
        loss_at_stop = (qty * stop_price * (1 - buy_cost) - total_usdt) if stop_price else None
        return {"total_usdt": total_usdt, "quantity": qty, "avg_cost": avg_cost, "breakeven_price": breakeven,
                "stop_price": stop_price, "loss_at_stop_usdt": loss_at_stop, "loss_at_stop_pct": (loss_at_stop / total_usdt) if (total_usdt and loss_at_stop is not None) else None,
                "value_now_after_fee": value_now, "pnl_now_usdt": pnl_now, "pnl_now_pct": pnl_now / total_usdt if total_usdt else None,
                "current_vs_cost": current_price / avg_cost - 1 if avg_cost else None,
                "fee_bps": fee * 1e4, "slippage_bps": slip * 1e4}


def scenario_table(current_price: float, amount_usdt: float, discounts_pct=(0, 1, 3, 5, 8, 10), stop_loss_pcts=(0.10, 0.15, 0.20)) -> list[dict]:
    """不同买入价（相对现价折价）× 不同止损幅度 → 持仓成本、止损价、止损亏损额。"""
    rows = []
    for d in discounts_pct:
        px = current_price * (1 - d / 100)
        for sl in stop_loss_pcts:
            r = PositionPlan([Tranche(px, amount_usdt)], stop_loss_pct=sl).compute(current_price)
            rows.append({"折价%": d, "买入价": px, "止损幅度": sl, "持仓成本": r["avg_cost"], "保本价": r["breakeven_price"], "止损价": r["stop_price"],
                         "止损亏损USDT": r["loss_at_stop_usdt"], "止损亏损%": r["loss_at_stop_pct"], "现价相对成本": r["current_vs_cost"]})
    return rows
