"""PortfolioAllocator：趋势状态 + 风险决策 → 目标比例（BTC/ETH/USDT 之和恒为 1）→ 订单（先卖后买）。

只使用调用时刻可得的输入；再平衡触发：目标权重向量变化，或任一资产实际权重偏离目标 ≥ drift_band；
最小再平衡金额 max(min_rebalance_usdt, 权益 × min_rebalance_frac)；买入不得超过卖出后的可用 USDT × (1 − cash_buffer)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Allocation:
    target_weights: dict            # {BTCUSDT: w, ETHUSDT: w, USDT: w}，和为 1
    orders: list                    # [{symbol, side, qty, est_notional, est_cost, reason}]
    reason: str
    rebalance: bool
    est_cost_usdt: float
    inputs: dict = field(default_factory=dict)


class PortfolioAllocator:
    def __init__(self, model_weights: dict, symbols: list[str], params: dict | None = None, fee_bps: float = 0.0, slip_bps: float = 0.0):
        self.mw = model_weights; self.symbols = list(symbols); p = params or {}
        self.drift = float(p.get("drift_band", 0.10)); self.min_usdt = float(p.get("min_rebalance_usdt", 10)); self.min_frac = float(p.get("min_rebalance_frac", 0.005)); self.cash_buffer = float(p.get("cash_buffer", 0.001))
        self.fee, self.slip = float(fee_bps), float(slip_bps)
        self.last_target: dict | None = None
        self.max_total = float(self.mw.get("max_total_exposure", 1.0))     # 模型级硬性现货敞口上限（模型 C = 0.8）

    def model_targets(self, trend: dict) -> dict:
        b = bool(trend.get("BTCUSDT")); e = bool(trend.get("ETHUSDT"))
        key = "both" if (b and e) else "btc_only" if b else "eth_only" if e else "none"
        w = {s: float(self.mw.get(key, {}).get(s, 0.0)) for s in self.symbols}
        return w, key

    def allocate(self, trend: dict, holdings: dict, usdt: float, prices: dict, risk, equity: float | None = None) -> Allocation:
        """risk: 具有 max_exposure / force_exit / state 属性的风险决策。"""
        equity = float(equity if equity is not None else usdt + sum(holdings.get(s, 0.0) * prices[s] for s in self.symbols))
        w_model, key = self.model_targets(trend)
        cap = 0.0 if getattr(risk, "force_exit", False) else float(getattr(risk, "max_exposure", 1.0))
        tot = sum(w_model.values())
        w = {s: w_model[s] * (min(cap, tot) / tot if tot > 0 else 0.0) for s in self.symbols}      # 现货总权重 ≤ cap，比例不变
        w["USDT"] = max(0.0, 1.0 - sum(w[s] for s in self.symbols))
        cur = {s: (holdings.get(s, 0.0) * prices[s] / equity if equity > 0 else 0.0) for s in self.symbols}
        changed = self.last_target is None or any(abs(w[s] - self.last_target.get(s, 0.0)) > 1e-12 for s in self.symbols)
        drift = max(abs(cur[s] - w[s]) for s in self.symbols) if self.symbols else 0.0
        reason = []
        if changed:
            reason.append(f"target_changed[{key}|{getattr(risk, 'state', 'NORMAL')}|cap={cap:.2f}]")
        if drift >= self.drift:
            reason.append(f"drift {drift:.3f} ≥ {self.drift}")
        cur_total = sum(cur.values())
        if cur_total > self.max_total + 1e-9:
            reason.append(f"exposure {cur_total:.3f} > hard_cap {self.max_total}")
        orders = []; est_cost = 0.0
        if reason:
            thr = max(self.min_usdt, equity * self.min_frac)
            sells = []; buys = []
            for s in self.symbols:
                diff_val = w[s] * equity - holdings.get(s, 0.0) * prices[s]
                if diff_val < -thr:
                    q = min(-diff_val / prices[s], holdings.get(s, 0.0)); sells.append({"symbol": s, "side": "SELL", "qty": q, "est_notional": q * prices[s], "reason": ";".join(reason)})
                elif diff_val > thr:
                    buys.append({"symbol": s, "side": "BUY", "amount": diff_val, "est_notional": diff_val, "reason": ";".join(reason)})
            cash_after = usdt + sum(o["est_notional"] * (1 - self.slip / 1e4) * (1 - self.fee / 1e4) for o in sells)
            avail = cash_after * (1 - self.cash_buffer)
            tot_buy = sum(o["amount"] for o in buys)
            if tot_buy > avail and tot_buy > 0:
                for o in buys:
                    o["amount"] *= avail / tot_buy; o["est_notional"] = o["amount"]; o["reason"] += ";scaled_to_cash"
            buys = [o for o in buys if o["amount"] >= thr]
            orders = sells + buys
            for o in orders:
                o["est_cost"] = o["est_notional"] * (self.fee + self.slip) / 1e4; est_cost += o["est_cost"]
            if orders:
                self.last_target = dict(w)
            elif changed:
                self.last_target = dict(w)        # 目标变化但金额低于阈值：记录目标，不下单
        return Allocation(w, orders, ";".join(reason) if reason else "hold", bool(orders), est_cost, {"trend": dict(trend), "key": key, "cap": cap, "cur_weights": cur, "usdt": usdt, "equity": equity, "risk_state": getattr(risk, "state", None)})
