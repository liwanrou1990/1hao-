"""三层现货趋势系统回测引擎（第六阶段）。

每根执行 K 线 i：
  1) 开盘 o[i]：执行上一根收盘时产生的待执行订单（下一根开盘成交；含手续费/滑点）；
  2) 收盘 cl[i]：按收盘标记权益；若本根收盘对应新的已收盘日线（htf['1d']['idx'] 变化），则
     趋势层给出每个资产 ON/OFF，风险层给出 max_exposure / reduce_to / force_exit，仓位层据此生成 BUY / HOLD / REDUCE / EXIT 订单，留到下一根开盘执行。
约束：现金与持仓不为负；无杠杆、无做空；单资产目标 = 权重 × max_exposure × 权益；买入金额 ≤ 可用现金 × (1 − cash_buffer)。
策略 A + 风险模式 none 必须与第五阶段 sma200_benchmark 逐根相同（audit.py 验证）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..spot.ledger import SpotLedger
from .risk_layer import make_risk
from .trend_layer import TrendLayer


@dataclass
class TrendSysResult:
    equity: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    decisions: pd.DataFrame
    risk_log: pd.DataFrame
    transitions: list
    final: dict
    meta: dict = field(default_factory=dict)


class TrendSystemBacktester:
    def __init__(self, strategy: str, risk_mode: str, c: dict, symbols: list[str], exec_tf: str, start, end, fee_bps: float = 0.0, slippage_bps: float = 0.0, confirm_days_override: int | None = None, initial: float | None = None):
        tc = c["trendsys"]; self.tc = tc; self.c = c
        self.strategy, self.risk_mode, self.symbols, self.exec_tf = strategy, risk_mode, list(symbols), exec_tf
        self.start, self.end = start, end
        self.fee, self.slip = float(fee_bps), float(slippage_bps)
        self.initial = float(initial if initial is not None else tc["initial_cash_usdt"])
        self.sma = int(tc["trend"]["sma"])
        self.confirm = int(confirm_days_override if confirm_days_override is not None else tc["trend"]["strategies"][strategy]["confirm_days"])
        self.cash_buffer = float(tc["position"].get("cash_buffer", 0.001)); self.min_order = float(tc["position"].get("min_order_usdt", 10))
        self.weights = {s: 1.0 / len(self.symbols) for s in self.symbols}

    def _mask(self, ts: pd.Series) -> np.ndarray:
        m = np.ones(len(ts), bool)
        if self.start is not None:
            m &= (ts >= pd.Timestamp(self.start, tz="UTC")).to_numpy()
        if self.end is not None:
            m &= (ts <= pd.Timestamp(self.end, tz="UTC")).to_numpy()
        return m

    def run(self, contexts: dict) -> TrendSysResult:
        t0 = time.time()
        mcs = {s: contexts[s] for s in self.symbols}
        ref = mcs[self.symbols[0]]; ts = ref.ts; n = ref.n
        for s, mc in mcs.items():
            if mc.n != n or not (mc.ts.to_numpy() == ts.to_numpy()).all():
                raise ValueError("多资产执行 K 线时间轴必须一致")
        layers = {s: TrendLayer(mc, self.sma, self.confirm) for s, mc in mcs.items()}
        risk = make_risk(self.risk_mode, self.tc["risk"])
        L = SpotLedger(self.initial, self.fee, self.slip)
        idx1d = ref.htf["1d"]["idx"]
        rows = []; decisions = []; trade_rows = []
        pending: list[dict] = []          # 待下一根开盘执行的订单
        j_prev = -2; sel = np.flatnonzero(self._mask(ts))
        O = {s: mc.o for s, mc in mcs.items()}; C = {s: mc.cl for s, mc in mcs.items()}
        risk_state = "NORMAL"; cap = 1.0; dd_ref = 0.0; dd_all = 0.0
        bar_sec = ref.bar_seconds
        for k, i in enumerate(sel):
            t = ts.iloc[i]
            # 1) 开盘执行待执行订单
            for od in pending:
                s = od["symbol"]; o = float(O[s][i])
                if od["side"] == "SELL":
                    q = L.qty(s) if od.get("qty") is None else min(od["qty"], L.qty(s))
                    if q > 1e-12 and q * o >= 0:
                        rec = L.sell(s, q, o, t, od["reason"])
                        trade_rows.append(self._trade_row(s, rec, t))
                elif od["side"] == "BUY":
                    amt = min(float(od["amount"]), L.cash * (1 - self.cash_buffer))
                    px = o * (1 + self.slip / 1e4) * (1 + self.fee / 1e4)
                    q = amt / px
                    if q * o >= self.min_order and q > 0:
                        L.buy(s, q, o, t, od["reason"], 0.0, 0.0, 0.0)
            pending = []
            # 2) 收盘标记
            prices = {s: float(C[s][i]) for s in self.symbols}
            equity = L.equity(prices); expo = L.exposure(prices)
            j = int(idx1d[i])
            tr_on = {s: layers[s].at(i) for s in self.symbols}
            data_fault = False
            if i > 0 and (ts.iloc[i] - ts.iloc[i - 1]).total_seconds() > 3 * bar_sec:
                data_fault = True            # 执行 K 线缺口 > 3 个周期：安全态（禁止新买入）
            if j != j_prev and j >= 0:
                # 新的已收盘日线 → 三层决策
                risk.set_data_fault(data_fault, "exec_gap" if data_fault else "")
                drop = min(tr_on[s]["daily_drop"] for s in self.symbols)
                d = risk.evaluate(t, equity, drop, expo)
                risk_state, cap, dd_ref, dd_all = d.state, d.max_exposure, d.dd_ref, d.dd_all
                for s in self.symbols:
                    q = L.qty(s); val = q * prices[s]; on = tr_on[s]["on"]
                    target = self.weights[s] * (1.0 if on else 0.0) * d.max_exposure * equity
                    act, reason = "HOLD", ""
                    if d.force_exit and q > 1e-12:
                        act, reason = "EXIT", "emergency_halt"; pending.append({"symbol": s, "side": "SELL", "qty": None, "reason": reason})
                    elif (not on) and q > 1e-12:
                        act, reason = "EXIT", "trend_exit"; pending.append({"symbol": s, "side": "SELL", "qty": None, "reason": reason})
                    elif on and d.reduce_to is not None and q > 1e-12:
                        tgt_val = self.weights[s] * d.reduce_to * equity
                        if val - tgt_val > max(self.min_order, 0.01 * equity):
                            act, reason = "REDUCE", f"risk_reduce_to_{d.reduce_to:.2f}"; pending.append({"symbol": s, "side": "SELL", "qty": (val - tgt_val) / prices[s], "reason": reason})
                    elif on and d.max_exposure > 0 and not d.data_fault and (target - val) > max(self.min_order, 0.05 * equity) and L.cash > self.min_order:
                        act, reason = "BUY", "trend_entry" if q <= 1e-12 else "top_up"
                        pending.append({"symbol": s, "side": "BUY", "amount": target - val, "reason": reason + (f"|{d.state}" if d.state != "NORMAL" else "")})
                    decisions.append({"ts": t, "symbol": s, "j": j, "trend_on": on, "above_sma": tr_on[s]["above"], "close_1d": tr_on[s]["close_1d"], "sma": tr_on[s]["sma"], "risk_state": d.state, "max_exposure": d.max_exposure, "equity": equity, "qty": q, "action": act, "reason": reason, "data_fault": d.data_fault})
                j_prev = j
            row = {"ts": t, "equity": equity, "cash": L.cash, "exposure": expo, "risk_state": risk_state, "cap": cap, "dd_ref": dd_ref, "dd_all": dd_all, "halted": risk_state in ("HALTED", "EMERGENCY_HALT", "COOLDOWN"), "data_fault": data_fault}
            for s in self.symbols:
                row[f"qty_{s}"] = L.qty(s); row[f"trend_{s}"] = tr_on[s]["on"]
            rows.append(row)
        eq = pd.DataFrame(rows); fills = pd.DataFrame(L.trade_log); trades = pd.DataFrame(trade_rows)
        prices = {s: float(C[s][sel[-1]]) for s in self.symbols} if len(sel) else {}
        final = L.snapshot(prices) | {"n_fills": len(fills), "n_sells": int((fills["side"] == "SELL").sum()) if not fills.empty else 0}
        return TrendSysResult(eq, fills, trades, pd.DataFrame(decisions), pd.DataFrame(risk.log), getattr(risk, "transitions", []), final,
                              {"strategy": self.strategy, "risk_mode": self.risk_mode, "confirm_days": self.confirm, "symbols": self.symbols, "exec_tf": self.exec_tf, "fee_bps": self.fee, "slippage_bps": self.slip, "elapsed_s": round(time.time() - t0, 2)})

    def _trade_row(self, s: str, rec: dict, t) -> dict:
        cl = rec["closed_lots"]; q = sum(x["qty"] for x in cl)
        entry = sum(x["qty"] * x["entry"] for x in cl) / q if q > 0 else np.nan
        entry_ts = min(pd.Timestamp(x["entry_ts"]) for x in cl) if cl else t
        return {"symbol": s, "strategy": self.strategy, "entry_ts": entry_ts, "exit_ts": t, "entry": entry, "exit": rec["price"], "qty": q, "gross_pnl": rec["gross_pnl"], "fee": rec["fee"], "net_pnl": rec["net_pnl"],
                "ret_pct": rec["price"] / entry - 1 if entry else np.nan, "r_multiple": np.nan, "exit_reason": rec["reason"], "hold_days": (pd.Timestamp(t) - entry_ts).total_seconds() / 86400}
