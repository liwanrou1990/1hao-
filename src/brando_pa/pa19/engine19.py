"""第十九阶段长期策略引擎（预登记 §5）。只做 BTC 现货；第 t 日收盘决策、t+1 日开盘成交；买卖双侧手续费与滑点。
每个被采用的候选买入当前权益的 1/3，最多 3 批，间隔 ≥ 10 日，扣费后风险收益比 ≥ 1.5（否则暂停加仓）；已开始减仓的持仓不再加仓。
任何退出规则之外，周线“趋势失效”（连续两周收于 40 周均线下或状态转为长期下跌）都全部卖出。每笔买入记录完整退出计划。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..holding.engine import HoldResult
from ..spot.ledger import SpotLedger
from ..usdt.engine import liquidation_value

SYM = "BTCUSDT"
EXITS = ("X1", "X2", "X3", "X4")
RR_MIN = 1.5


class LongTermBacktester:
    def __init__(self, f: pd.DataFrame, cand: pd.DataFrame, entries: str, exit_: str, fee_bps: float, slip_bps: float, start, end=None, initial: float = 10000.0, name: str | None = None):
        self.f = f.reset_index(drop=True); self.entries = set(entries); self.exit = exit_; self.fee, self.slip = float(fee_bps), float(slip_bps); self.initial = float(initial)
        self.name = name or f"{entries}_{exit_}"; ts = pd.DatetimeIndex(self.f["ts"]); self.ts = ts
        self.i0 = int(np.searchsorted(ts, pd.Timestamp(start, tz="UTC"))); self.i1 = int(np.searchsorted(ts, pd.Timestamp(end, tz="UTC"), side="right")) if end is not None else len(ts)
        self.cand = {int(r["i"]): r for r in cand.to_dict("records") if r["type"] in self.entries} if len(cand) else {}
        g = lambda k: self.f[k].to_numpy()
        self.o, self.c = g("open").astype(float), g("close").astype(float); self.wk = g("is_week_close").astype(bool); self.wclose = g("w_close").astype(float); self.wsma20 = g("w_sma20").astype(float)
        self.wstate = g("w_state"); self.watr = g("w_atrw").astype(float); self.tfail = pd.Series(g("w_trend_failure")).fillna(False).astype(bool).to_numpy()
        self.L = SpotLedger(self.initial, self.fee, self.slip); self.pending: list[dict] = []; self.fills: list[dict] = []; self.trades: list[dict] = []; self.nav: list[dict] = []; self.pos = None; self._peak = self.initial

    def _execute(self, i):
        ts = self.ts[i]; ref = float(self.o[i])
        for o in sorted(self.pending, key=lambda x: 0 if x["side"] == "SELL" else 1):
            if o["side"] == "SELL":
                q = min(o["qty"], self.L.qty(SYM))
                if q <= 1e-12 or q * ref < 1.0:
                    continue
                rec = self.L.sell(SYM, q, ref, ts, o["reason"]); p = self.pos; p["proceeds"] += rec["price"] * q - rec["fee"]; p["sold"] = True; p["exit_reasons"].append(o["reason"]); p["last_exit"] = ts
                self.fills.append({"ts": ts, "symbol": SYM, "side": "SELL", "qty": q, "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (ref - rec["price"]) * q, "reason": o["reason"], "plan": None})
            else:
                amt = min(o["amount"], self.L.cash * 0.999); px = ref * (1 + self.slip / 1e4) * (1 + self.fee / 1e4)
                if amt < 10:
                    continue
                q = amt / px; lot = self.L.buy(SYM, q, ref, ts, o["reason"], 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]
                if self.pos is None:
                    self.pos = {"first": ts, "n": 0, "cost": 0.0, "qty": 0.0, "proceeds": 0.0, "plan": o["plan"], "inval": o["plan"]["invalidation"], "sold": False, "t1": False, "t2": False, "x2": False, "max_wclose": 0.0, "peak_qty": 0.0, "exit_reasons": [], "last_entry_i": i, "last_exit": None}
                p = self.pos; p["n"] += 1; p["cost"] += lot.price * q + fee; p["qty"] += q; p["inval"] = o["plan"]["invalidation"]; p["last_entry_i"] = i
                self.fills.append({"ts": ts, "symbol": SYM, "side": "BUY", "qty": q, "ref_price": ref, "fill_price": lot.price, "fee": fee, "slippage": (lot.price - ref) * q, "reason": o["reason"], "plan": o["plan"]})
        self.pending = []
        if self.pos is not None:
            self.pos["peak_qty"] = max(self.pos["peak_qty"], self.L.qty(SYM))
            if self.pos["sold"] and self.L.qty(SYM) <= 1e-10:
                p = self.pos; self.trades.append({"entry_ts": p["first"], "exit_ts": p["last_exit"], "hold_days": int((p["last_exit"] - p["first"]).days), "n_buys": p["n"], "avg_entry": p["cost"] / p["qty"], "net_pnl": p["proceeds"] - p["cost"], "reason": p["exit_reasons"][-1], "plan": p["plan"]})
                self.pos = None

    def _sell(self, q, reason):
        self.pending.append({"side": "SELL", "qty": q, "reason": reason})

    def _decide(self, i):
        held = self.L.qty(SYM); p = self.pos
        if p is not None and held > 1e-10:
            if self.wk[i] and np.isfinite(self.wclose[i]):
                p["max_wclose"] = max(p["max_wclose"], self.wclose[i])
            if self.wk[i] and self.tfail[i]:
                self._sell(held, "趋势失效：连续两周收于 40 周均线下或长期下跌"); return
            x = self.exit
            if x == "X1":
                q = 0.0
                if not p["t1"] and self.c[i] >= p["plan"]["target_1"]:
                    p["t1"] = True; q += p["peak_qty"] / 3
                if not p["t2"] and self.c[i] >= p["plan"]["target_2"]:
                    p["t2"] = True; q += p["peak_qty"] / 3
                if q > 0:
                    self._sell(min(q, held), "X1：到达预设阻力目标区分批止盈")
            elif x == "X2" and self.wk[i] and not p["x2"] and (self.wstate[i] == "DISTRIBUTION" or (np.isfinite(self.wsma20[i]) and self.wclose[i] < self.wsma20[i])):
                p["x2"] = True; self._sell(held * 0.5, "X2：周线衰退 / 跌破 20 周均线，减仓 50%")
            elif x == "X3" and self.c[i] < p["inval"]:
                self._sell(held, "X3：日收盘跌破结构失效价")
            elif x == "X4" and self.wk[i] and np.isfinite(self.watr[i]) and self.wclose[i] < p["max_wclose"] - 3 * self.watr[i]:
                self._sell(held, "X4：周线跟踪退出（最高周收盘 − 3 周 ATR）")
            if self.pending:
                return
        r = self.cand.get(i)
        if r is None or (p is not None and (p["sold"] or p["n"] >= 3 or i - p["last_entry_i"] < 10)):
            return
        if not np.isfinite(r["rr_net"]) or r["rr_net"] < RR_MIN:
            return
        eq = self.L.equity({SYM: self.c[i]})
        plan = {k: r[k] for k in ("kind", "entry_ref", "invalidation", "max_loss_pct", "target_1", "target_2", "target_basis", "rr_net", "zone_low", "zone_high", "structure", "volume")}
        plan["decision_date"] = str(self.ts[i].date()); plan["weekly_state"] = str(self.wstate[i])
        self.pending.append({"side": "BUY", "amount": eq / 3, "reason": f"候选 {r['type']} 买入（扣费后风险收益比 {r['rr_net']:.2f}）", "plan": plan})

    def run(self) -> HoldResult:
        for i in range(self.i0, self.i1):
            if self.pending:
                self._execute(i)
            self._decide(i)
            cl = {SYM: float(self.c[i])}; eq = self.L.equity(cl); self._peak = max(self._peak, eq)
            self.nav.append({"ts": self.ts[i], "equity": eq, "liquidation": liquidation_value(self.L, cl), "cash": self.L.cash, "exposure": self.L.exposure(cl), "drawdown": eq / self._peak - 1, "holdings": {SYM: self.L.qty(SYM)}, "closes": cl})
        nav = pd.DataFrame(self.nav); fills = pd.DataFrame(self.fills); last = {SYM: float(self.c[self.i1 - 1])}
        buy_fees = float(fills.loc[fills["side"] == "BUY", "fee"].sum()) if len(fills) else 0.0; eqf = float(nav["equity"].iloc[-1]); liq = float(nav["liquidation"].iloc[-1])
        unreal = float(self.L.unrealized(last)); realized = float(self.L.realized) - buy_fees
        final = {"end_ts": str(nav["ts"].iloc[-1]), "equity": eqf, "cash": float(self.L.cash), "holdings": {SYM: self.L.qty(SYM)}, "realized_net_usdt": realized, "unrealized_usdt": unreal, "buy_fees_usdt": buy_fees, "liquidation_usdt": liq,
                 "liquidation_net_profit_usdt": liq - self.initial, "equity_profit_usdt": eqf - self.initial, "fees_usdt": float(self.L.fees_paid), "slippage_usdt": float(fills["slippage"].sum()) if len(fills) else 0.0, "hypothetical_exit_cost_usdt": eqf - liq,
                 "n_fills": int(len(fills)), "n_round_trips": len(self.trades), "open_positions": int(self.L.qty(SYM) > 1e-10), "identity_ok": abs((eqf - self.initial) - (realized + unreal)) < 1e-6 * max(1.0, eqf)}
        trades = pd.DataFrame(self.trades) if self.trades else pd.DataFrame(columns=["hold_days", "net_pnl", "reason"])
        return HoldResult(self.name, {"assets": [SYM], "entries": sorted(self.entries), "exit": self.exit}, self.fee, self.slip, self.initial, nav, trades, fills, final)
