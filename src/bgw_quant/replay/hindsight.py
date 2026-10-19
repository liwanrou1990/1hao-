"""模式 A：事后最优复盘（允许看完整行情）。

动态规划：状态 ∈ {空仓, 持仓}，每根K线可在其收盘价成交一次；买入成交价 = close×(1+c)，卖出 = close×(1−c)，
每次成交另付 Gas。目标：最大化日终现金（必须日终空仓）。O(n)。
结果是“扣除所有交易成本后的理论收益上限”，仅供对照，不代表可实现收益。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel


@dataclass
class HindsightResult:
    symbol: str
    capital_usd: float
    final_cash_usd: float
    gross_return: float           # 不扣费的同路径收益（对照）
    net_return: float
    n_trades: int
    total_cost_usd: float
    trades: list[dict] = field(default_factory=list)
    buy_idx: list[int] = field(default_factory=list)
    sell_idx: list[int] = field(default_factory=list)
    note: str = "事后最优（看过全部行情），理论上限，不可实现"

    def to_dict(self) -> dict:
        return {k: (v if not isinstance(v, np.generic) else v.item()) for k, v in self.__dict__.items()}


def optimal_trades(bars: pd.DataFrame, cost: CostModel, capital_usd: float, price_col: str = "close",
                   symbol: str | None = None) -> HindsightResult:
    """全额仓位、多头、无限次数的最优买卖点（DP）。"""
    if bars.empty:
        return HindsightResult(symbol or "", capital_usd, capital_usd, 0.0, 0.0, 0, 0.0)
    p = bars[price_col].to_numpy(dtype=float)
    n = len(p)
    c = cost.proportional_side_cost() + cost.impact_fraction(capital_usd)
    gas = cost.p.gas_usd * cost.n_child_orders(capital_usd)
    # DP 以“现金”和“持仓股数等价现金”为状态；用 log 空间避免数量级问题 —— 这里用现金值直接做（n≤几千）。
    cash = np.full(n, -np.inf)        # 空仓时的最大现金
    hold = np.full(n, -np.inf)        # 持仓时的最大“持有股数”（以股数衡量）
    from_cash = np.zeros(n, dtype=np.int64)  # 回溯：cash[i] 来自 (0: 继续空仓, 1: 在 i 卖出)
    from_hold = np.zeros(n, dtype=np.int64)  # hold[i] 来自 (0: 继续持有, 1: 在 i 买入)
    cash[0] = capital_usd
    buy_px0 = p[0] * (1 + c)
    hold[0] = (capital_usd - gas) / buy_px0 if capital_usd > gas else -np.inf
    from_hold[0] = 1
    for i in range(1, n):
        buy_px = p[i] * (1 + c)
        sell_px = p[i] * (1 - c)
        # 空仓：继续空仓 或 在 i 卖出
        sell_val = hold[i - 1] * sell_px - gas if np.isfinite(hold[i - 1]) else -np.inf
        if sell_val > cash[i - 1]:
            cash[i], from_cash[i] = sell_val, 1
        else:
            cash[i], from_cash[i] = cash[i - 1], 0
        # 持仓：继续持有 或 在 i 买入
        buy_qty = (cash[i - 1] - gas) / buy_px if cash[i - 1] > gas else -np.inf
        if buy_qty > hold[i - 1]:
            hold[i], from_hold[i] = buy_qty, 1
        else:
            hold[i], from_hold[i] = hold[i - 1], 0
    # 回溯（日终必须空仓）
    i, state = n - 1, "cash"
    sells, buys = [], []
    while i >= 0:
        if state == "cash":
            if from_cash[i] == 1:
                sells.append(i)
                state = "hold"
                i -= 1
            else:
                i -= 1
        else:
            if from_hold[i] == 1:
                buys.append(i)
                state = "cash"
                i -= 1
            else:
                i -= 1
    buys.reverse()
    sells.reverse()
    trades = []
    gross = capital_usd
    total_cost = 0.0
    cash_now = capital_usd
    ts = bars["ts"].to_numpy()
    for b, s in zip(buys, sells):
        buy_px, sell_px = p[b] * (1 + c), p[s] * (1 - c)
        qty = (cash_now - gas) / buy_px
        proceeds = qty * sell_px - gas
        cost_usd = qty * p[b] * c + qty * p[s] * c + 2 * gas
        total_cost += cost_usd
        gross *= p[s] / p[b]
        trades.append({"buy_idx": int(b), "sell_idx": int(s), "buy_ts": pd.Timestamp(ts[b]).isoformat(),
                       "sell_ts": pd.Timestamp(ts[s]).isoformat(), "buy_ref": float(p[b]), "sell_ref": float(p[s]),
                       "buy_exec": float(buy_px), "sell_exec": float(sell_px), "qty": float(qty),
                       "cost_usd": float(cost_usd), "net_pnl_usd": float(proceeds - cash_now),
                       "net_ret": float(proceeds / cash_now - 1)})
        cash_now = proceeds
    return HindsightResult(symbol=symbol or str(bars["symbol"].iloc[0]), capital_usd=capital_usd,
                           final_cash_usd=float(cash_now), gross_return=float(gross / capital_usd - 1),
                           net_return=float(cash_now / capital_usd - 1), n_trades=len(trades),
                           total_cost_usd=float(total_cost), trades=trades, buy_idx=buys, sell_idx=sells)
