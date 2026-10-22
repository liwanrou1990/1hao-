"""分批成交模拟：把一笔名义金额拆到连续若干根K线上，每根受流动性参与率上限约束，每个子单经成本模型成交。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel


@dataclass
class ChildFill:
    idx: int
    ts_ny: str
    ref_price: float
    exec_price: float
    notional_usd: float
    qty: float
    cost_usd: float


@dataclass
class BatchFill:
    side: str
    fills: list[ChildFill] = field(default_factory=list)
    requested_usd: float = 0.0

    @property
    def filled_usd(self) -> float:
        return sum(f.notional_usd for f in self.fills)

    @property
    def qty(self) -> float:
        return sum(f.qty for f in self.fills)

    @property
    def avg_exec(self) -> float:
        return self.filled_usd / self.qty if self.qty > 0 else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(f.cost_usd for f in self.fills)

    @property
    def avg_ref(self) -> float:
        """成交量加权参考价（不含价差/滑点/冲击）：止损应相对参考价衡量，否则成本本身会触发止损。"""
        q = self.qty
        return sum(f.qty * f.ref_price for f in self.fills) / q if q > 0 else 0.0

    @property
    def start_idx(self) -> int:
        return self.fills[0].idx if self.fills else -1

    @property
    def end_idx(self) -> int:
        return self.fills[-1].idx if self.fills else -1

    @property
    def partial(self) -> bool:
        return self.filled_usd < self.requested_usd - 1e-6

    def scaled(self, keep_ratio: float) -> "BatchFill":
        """按比例保留未卖出部分（部分平仓后剩余持仓）。"""
        out = BatchFill(side=self.side, requested_usd=self.requested_usd * keep_ratio)
        for f in self.fills:
            out.fills.append(ChildFill(f.idx, f.ts_ny, f.ref_price, f.exec_price, f.notional_usd * keep_ratio, f.qty * keep_ratio, f.cost_usd * keep_ratio))
        return out

    def to_dict(self) -> dict:
        return {"side": self.side, "requested_usd": self.requested_usd, "filled_usd": self.filled_usd, "qty": self.qty,
                "avg_exec": self.avg_exec, "cost_usd": self.cost_usd, "start_idx": self.start_idx, "end_idx": self.end_idx,
                "n_children": len(self.fills), "partial": self.partial,
                "children": [f.__dict__ for f in self.fills]}


def bar_liquidity(bars: pd.DataFrame, participation_rate: float) -> np.ndarray:
    amt = bars["amount"].to_numpy(dtype=float) if "amount" in bars else np.full(len(bars), np.nan)
    if not (np.isfinite(amt).any() and np.nansum(amt) > 0):
        return np.full(len(bars), np.inf)        # 流动性未知：不施加上限（调用方应标记不可验证）
    return np.where(np.isfinite(amt), amt * participation_rate, 0.0)


def batch_fill(bars: pd.DataFrame, start_idx: int, side: str, notional_usd: float, cost: CostModel, liquidity: np.ndarray,
               max_bars: int, price_col: str = "close", qty_to_sell: float | None = None) -> BatchFill:
    """从 start_idx 起连续成交，直到名义金额完成或用完 max_bars 根。卖出时按 qty_to_sell 折算金额。"""
    out = BatchFill(side=side, requested_usd=float(notional_usd))
    prices = bars[price_col].to_numpy(dtype=float)
    ts = bars["ts_ny"].astype(str).to_numpy()
    n = len(bars)
    remaining_usd = float(notional_usd)
    remaining_qty = qty_to_sell
    for i in range(start_idx, min(n, start_idx + max_bars)):
        if remaining_usd <= cost.p.min_tx_usd and (remaining_qty is None or remaining_qty <= 1e-12):
            break
        cap = float(liquidity[i])
        ref = prices[i]
        if side == "sell" and remaining_qty is not None:
            want_usd = remaining_qty * ref
        else:
            want_usd = remaining_usd
        child_usd = min(want_usd, cap)
        if child_usd < cost.p.min_tx_usd:
            continue
        f = cost.execute(side, ref, child_usd)
        if not f.filled:
            continue
        if side == "sell" and remaining_qty is not None:
            qty = min(remaining_qty, f.notional_usd / ref)
            notional = qty * f.exec_price
            remaining_qty -= qty
        else:
            qty = f.notional_usd / f.exec_price
            notional = f.notional_usd
        remaining_usd -= f.notional_usd
        out.fills.append(ChildFill(int(i), ts[i], float(ref), float(f.exec_price), float(notional), float(qty), float(f.total_cost_usd)))
    return out
