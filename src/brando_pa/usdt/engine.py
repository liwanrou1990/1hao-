"""资金配置回测引擎：两资产 + USDT，统一账本（SpotLedger），日线收盘决策 → 下一根执行 K 线开盘先卖后买。
输出逐根权益（含各资产价值与权重）、成交、配置日志、风险日志、期末清算价值、按资产的盈亏贡献、成本统计。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..spot.ledger import SpotLedger
from ..trendsys.risk_layer import make_risk
from ..trendsys.trend_layer import TrendLayer
from .allocator import PortfolioAllocator


@dataclass
class AllocResult:
    equity: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    alloc_log: pd.DataFrame
    risk_log: pd.DataFrame
    transitions: list
    final: dict
    meta: dict = field(default_factory=dict)


def liquidation_value(L: SpotLedger, prices: dict) -> float:
    """期末全部清仓、扣除该轮次手续费与滑点后的 USDT。"""
    v = L.cash
    for s in list(L.lots):
        q = L.qty(s)
        if q > 0:
            px = prices[s] * (1 - L.slippage_bps / 1e4); v += px * q * (1 - L.fee_bps / 1e4)
    return float(v)


class AllocationBacktester:
    def __init__(self, model: str, risk_mode: str, c: dict, start, end, fee_bps: float = 0.0, slippage_bps: float = 0.0, initial: float | None = None, symbols: list[str] | None = None, exec_tf: str | None = None):
        uc = c["usdt"]; self.c = c; self.uc = uc; self.model, self.risk_mode = model, risk_mode
        self.symbols = list(symbols or uc["symbols"]); self.exec_tf = exec_tf or uc["exec_tf"]; self.start, self.end = start, end
        self.fee, self.slip = float(fee_bps), float(slippage_bps); self.initial = float(initial if initial is not None else uc["initial_cash_usdt"])
        self.min_order = 10.0

    def _mask(self, ts):
        m = np.ones(len(ts), bool)
        if self.start is not None:
            m &= (ts >= pd.Timestamp(self.start, tz="UTC")).to_numpy()
        if self.end is not None:
            m &= (ts <= pd.Timestamp(self.end, tz="UTC")).to_numpy()
        return m

    def run(self, contexts: dict) -> AllocResult:
        t0 = time.time(); mcs = {s: contexts[s] for s in self.symbols}; ref = mcs[self.symbols[0]]; ts = ref.ts; n = ref.n
        for mc in mcs.values():
            if mc.n != n or not (mc.ts.to_numpy() == ts.to_numpy()).all():
                raise ValueError("多资产执行 K 线时间轴必须一致")
        tl = {s: TrendLayer(mc, int(self.uc["trend"]["sma"]), int(self.uc["trend"]["confirm_days"])) for s, mc in mcs.items()}
        risk = make_risk(self.risk_mode, self.c["trendsys"]["risk"])
        alloc = PortfolioAllocator(self.uc["models"][self.model], self.symbols, self.uc["allocator"], self.fee, self.slip)
        L = SpotLedger(self.initial, self.fee, self.slip); idx1d = ref.htf["1d"]["idx"]
        O = {s: mc.o for s, mc in mcs.items()}; C = {s: mc.cl for s, mc in mcs.items()}
        rows = []; alog = []; trade_rows = []; pending = []; j_prev = -2; sel = np.flatnonzero(self._mask(ts))
        state, cap, dd_ref, dd_all = "NORMAL", 1.0, 0.0, 0.0; bar_sec = ref.bar_seconds; turnover = 0.0; slip_paid = 0.0
        for i in sel:
            t = ts.iloc[i]
            # 1) 开盘执行：先卖后买
            for od in sorted(pending, key=lambda o: 0 if o["side"] == "SELL" else 1):
                s = od["symbol"]; o = float(O[s][i])
                if od["side"] == "SELL":
                    q = min(float(od["qty"]), L.qty(s))
                    if q > 1e-12 and q * o >= 1.0:
                        rec = L.sell(s, q, o, t, od["reason"]); trade_rows.append(self._trade_row(s, rec, t)); turnover += rec["price"] * q; slip_paid += o * q * self.slip / 1e4
                else:
                    amt = min(float(od["amount"]), L.cash * (1 - alloc.cash_buffer)); px = o * (1 + self.slip / 1e4) * (1 + self.fee / 1e4); q = amt / px
                    if q > 0 and q * o >= self.min_order:
                        lot = L.buy(s, q, o, t, od["reason"], 0.0, 0.0, 0.0); turnover += lot.price * q; slip_paid += o * q * self.slip / 1e4
            pending = []
            prices = {s: float(C[s][i]) for s in self.symbols}; equity = L.equity(prices); expo = L.exposure(prices)
            j = int(idx1d[i]); tr = {s: tl[s].at(i) for s in self.symbols}
            gap = i > 0 and (ts.iloc[i] - ts.iloc[i - 1]).total_seconds() > 3 * bar_sec
            if j != j_prev and j >= 0:
                risk.set_data_fault(gap, "exec_gap" if gap else "")
                d = risk.evaluate(t, equity, min(x["daily_drop"] for x in tr.values()), expo)
                state, cap, dd_ref, dd_all = d.state, d.max_exposure, d.dd_ref, d.dd_all
                if d.data_fault:
                    d.max_exposure = min(d.max_exposure, expo)     # 数据失败：不新增敞口
                a = alloc.allocate({s: tr[s]["on"] for s in self.symbols}, {s: L.qty(s) for s in self.symbols}, L.cash, prices, d, equity)
                pending = list(a.orders)
                alog.append({"ts": t, "j": j, **{f"trend_{s}": tr[s]["on"] for s in self.symbols}, "risk_state": d.state, "cap": d.max_exposure, "equity": equity, "usdt": L.cash, **{f"w_cur_{s}": a.inputs["cur_weights"][s] for s in self.symbols},
                             **{f"w_tgt_{s}": a.target_weights[s] for s in self.symbols}, "w_tgt_USDT": a.target_weights["USDT"], "rebalance": a.rebalance, "n_orders": len(a.orders), "est_cost": a.est_cost_usdt, "reason": a.reason, "orders": str([(o["symbol"], o["side"], round(o.get("est_notional", 0), 2)) for o in a.orders])})
                j_prev = j
            row = {"ts": t, "equity": equity, "cash": L.cash, "exposure": expo, "risk_state": state, "cap": cap, "dd_ref": dd_ref, "dd_all": dd_all, "halted": state in ("HALTED", "EMERGENCY_HALT", "COOLDOWN")}
            for s in self.symbols:
                row[f"qty_{s}"] = L.qty(s); row[f"val_{s}"] = L.qty(s) * prices[s]; row[f"w_{s}"] = row[f"val_{s}"] / equity if equity > 0 else 0.0; row[f"trend_{s}"] = tr[s]["on"]
            row["w_USDT"] = L.cash / equity if equity > 0 else 1.0
            rows.append(row)
        eq = pd.DataFrame(rows); fills = pd.DataFrame(L.trade_log); trades = pd.DataFrame(trade_rows)
        prices = {s: float(C[s][sel[-1]]) for s in self.symbols}
        liq = liquidation_value(L, prices); yrs = max((ts.iloc[sel[-1]] - ts.iloc[sel[0]]).total_seconds() / 86400, 1) / 365
        contrib = self._contrib(L, fills, prices)
        final = L.snapshot(prices) | {"liquidation_usdt": liq, "net_profit_usdt": L.equity(prices) - self.initial, "realizable_profit_usdt": liq - self.initial, "fees_usdt": L.fees_paid, "slippage_usdt": slip_paid, "turnover_notional": turnover,
                                      "turnover_per_year": turnover / max(eq["equity"].mean(), 1) / yrs, "n_fills": int(len(fills)), "n_sells": int((fills["side"] == "SELL").sum()) if not fills.empty else 0, "n_buys": int((fills["side"] == "BUY").sum()) if not fills.empty else 0, "contrib": contrib}
        return AllocResult(eq, fills, trades, pd.DataFrame(alog), pd.DataFrame(risk.log), getattr(risk, "transitions", []), final, {"model": self.model, "risk_mode": self.risk_mode, "symbols": self.symbols, "exec_tf": self.exec_tf, "fee_bps": self.fee, "slippage_bps": self.slip, "initial": self.initial, "elapsed_s": round(time.time() - t0, 2)})

    def _contrib(self, L: SpotLedger, fills: pd.DataFrame, prices: dict) -> dict:
        """按资产的 USDT 盈亏贡献 = 已实现毛利 − 该资产手续费 + 未实现。"""
        out = {}
        for s in self.symbols:
            f = fills[fills["symbol"] == s] if not fills.empty else fills
            realized = float(f.loc[f["side"] == "SELL", "gross_pnl"].sum()) if len(f) else 0.0
            fees = float(f["fee"].sum()) if len(f) else 0.0
            unreal = float(sum((prices[s] - l.price) * l.qty for l in L.lots.get(s, [])))
            out[s] = {"realized_gross": realized, "fees": fees, "unrealized": unreal, "total": realized - fees + unreal}
        return out

    def _trade_row(self, s, rec, t):
        cl = rec["closed_lots"]; q = sum(x["qty"] for x in cl); entry = sum(x["qty"] * x["entry"] for x in cl) / q if q > 0 else np.nan
        ets = min(pd.Timestamp(x["entry_ts"]) for x in cl) if cl else t
        return {"symbol": s, "strategy": self.model, "entry_ts": ets, "exit_ts": t, "entry": entry, "exit": rec["price"], "qty": q, "gross_pnl": rec["gross_pnl"], "fee": rec["fee"], "net_pnl": rec["net_pnl"], "ret_pct": rec["price"] / entry - 1 if entry else np.nan, "r_multiple": np.nan, "exit_reason": rec["reason"], "hold_days": (pd.Timestamp(t) - ets).total_seconds() / 86400}
