"""第十二阶段组合引擎：CoreTrendEngine（B2 / SMA200，规则不改）+ PullbackOpportunityEngine / BreakoutRetestEngine（只在核心已持仓时加仓）
+ StructuralExitEngine（trend_only / structure_reduce / vol_trail / t1_partial）+ PositionAllocator / RiskController + SignalExplainer。
日收盘决策、次日开盘成交；复用 SpotLedger、liquidation_value、CircuitBreaker、trend_signal、Phase 11 MarketContext（结构 / 区域 / A、B 检测）。
core_frac = 1 且 exit_mode = trend_only 且无 PA 时与 Phase 10 HoldingBacktester 的 B2 / S3 / S1 逐日一致（测试验证）。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..holding.engine import HoldResult
from ..pa11.engine11 import MarketContext
from ..pa11.zones import SupportResistanceZones
from ..spot.ledger import SpotLedger
from ..trendsys.risk_layer import make_risk
from ..trendsys.trend_layer import trend_signal
from ..usdt.engine import liquidation_value

SYMS = ["BTCUSDT", "ETHUSDT"]


@dataclass
class Cfg12:
    core: str = "B2"                       # B2 | SMA200_dual | SMA200_btc
    core_frac: float = 1.0                 # 核心目标 = core_frac × 熔断上限 × 等权份额
    pa_add: tuple = ()                     # () | ("A",) | ("B",) | ("A","B")  机会加仓触发器
    exit_mode: str = "trend_only"          # trend_only | structure_reduce | vol_trail | t1_partial
    risk_pct: float = 0.01                 # 机会仓位单笔风险上限（按失效价距离）
    atr_mult: float = 3.0                  # vol_trail 倍数（扰动变量）
    name: str = "A_B2"


@dataclass
class Opp:
    symbol: str
    entry_idx: int
    entry_ts: pd.Timestamp
    entry_price: float
    qty: float
    stop: float
    plan: dict
    trigger: str
    t1_done: bool = False
    days: int = 0


class CompositeBacktester:
    def __init__(self, bars: dict, cfg: Cfg12, fee_bps: float, slip_bps: float, start, end, initial: float = 10000.0, ctx: MarketContext | None = None, risk_params: dict | None = None, sma: int = 200):
        self.cfg = cfg; self.fee, self.slip = float(fee_bps), float(slip_bps); self.initial = float(initial); self.c1 = (self.fee + self.slip) / 1e4
        self.start = pd.Timestamp(start, tz="UTC"); self.end = pd.Timestamp(end, tz="UTC")
        self.ctx = ctx or MarketContext(bars); c = self.ctx; self.idx = c.idx; self.n = c.n; self.open, self.high, self.close = c.open, c.high, c.close
        self.assets = ["BTCUSDT"] if cfg.core == "SMA200_btc" else list(SYMS)
        self.on = {s: trend_signal(pd.DataFrame({"ts": self.idx, "close": self.close[s]}), sma, 1)["on"].to_numpy(bool) for s in self.assets}
        self.risk = make_risk("state_machine" if cfg.core == "B2" else "none", risk_params or {})
        self.L = SpotLedger(self.initial, self.fee, self.slip); self.pending = []; self.fills = []; self.trades = []; self.nav_rows = []; self.decisions = []; self.adds = []
        self.core_qty = {s: 0.0 for s in self.assets}; self.opp: dict[str, Opp] = {}; self.core_reduced = {s: False for s in self.assets}; self.core_entry_fee = {s: 0.0 for s in self.assets}; self.core_entry = {s: None for s in self.assets}
        self.peak_close = {s: 0.0 for s in self.assets}; self.prev_held: set = set(); self.prev_cap = 1.0

    # ---------- 执行 ----------
    def _sell(self, s: str, i: int, qty: float, reason: str, book: str, partial: bool, entry: tuple | None = None) -> None:
        ts = self.idx[i]; ref = self.open[s][i]; held = self.L.qty(s); q = min(qty, held)
        if q <= 1e-12 or q * ref < 1.0:
            return
        rec = self.L.sell(s, q, ref, ts, reason)
        self.fills.append({"ts": ts, "symbol": s, "side": "SELL", "qty": q, "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (ref - rec["price"]) * q, "reason": reason, "book": book})
        e_ts, e_px, e_fee, e_idx = entry or (None, None, 0.0, None)
        self.trades.append({"symbol": s, "book": book, "entry_ts": e_ts, "exit_ts": ts, "entry_price": e_px, "exit_price": rec["price"], "qty": q, "hold_days": (i - e_idx) if e_idx is not None else None, "gross_pnl": rec["gross_pnl"], "fees": rec["fee"] + (0.0 if partial else e_fee), "net_pnl": rec["net_pnl"] - (0.0 if partial else e_fee), "reason": reason, "partial": partial})

    def _buy(self, s: str, i: int, amount: float, reason: str, book: str) -> tuple[float, float, float]:
        ts = self.idx[i]; ref = self.open[s][i]; amt = min(amount, self.L.cash * (1 - 0.001)); px = ref * (1 + self.slip / 1e4) * (1 + self.fee / 1e4); q = amt / px
        if q <= 0 or q * ref < 10:
            return 0.0, 0.0, 0.0
        lot = self.L.buy(s, q, ref, ts, reason, 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]
        self.fills.append({"ts": ts, "symbol": s, "side": "BUY", "qty": q, "ref_price": ref, "fill_price": lot.price, "fee": fee, "slippage": (lot.price - ref) * q, "reason": reason, "book": book}); return q, lot.price, fee

    def _execute(self, i: int) -> None:
        for o in sorted(self.pending, key=lambda o: 0 if o["side"] == "SELL" else 1):
            s = o["symbol"]
            if o["side"] == "SELL":
                if o["book"] == "opp" and s in self.opp:
                    p = self.opp[s]; q = min(o["qty"], p.qty); partial = q < p.qty - 1e-12
                    self._sell(s, i, q, o["reason"], "opp", partial, (p.entry_ts, p.entry_price, p.plan.get("entry_fee", 0.0), p.entry_idx)); p.qty -= q
                    if p.qty <= 1e-12:
                        self.opp.pop(s)
                else:
                    q = min(o["qty"], self.core_qty[s]); partial = q < self.core_qty[s] - 1e-12
                    self._sell(s, i, q, o["reason"], "core", partial, (self.core_entry[s][0], self.core_entry[s][1], self.core_entry_fee[s], self.core_entry[s][2]) if self.core_entry[s] else None); self.core_qty[s] = max(0.0, self.core_qty[s] - q)
                    if self.core_qty[s] <= 1e-12:
                        self.core_entry[s] = None; self.core_entry_fee[s] = 0.0; self.core_reduced[s] = False
            else:
                q, px, fee = self._buy(s, i, o["amount"], o["reason"], o["book"])
                if q <= 0:
                    continue
                if o["book"] == "opp":
                    plan = dict(o["plan"]); plan["actual_entry_ts"] = str(self.idx[i]); plan["actual_entry_price"] = px; plan["entry_fee"] = fee; plan["qty"] = q
                    self.opp[s] = Opp(s, i, self.idx[i], px, q, plan["invalidation"], plan, o["trigger"]); self.adds.append(plan | {"symbol": s, "trigger": o["trigger"]})
                else:
                    self.core_qty[s] += q
                    if self.core_entry[s] is None:
                        self.core_entry[s] = (self.idx[i], px, i); self.core_entry_fee[s] = fee
                    else:
                        self.core_entry_fee[s] += fee
        self.pending = []
        for s in list(self.opp):                                                     # 机会仓位跳空止损
            p = self.opp[s]
            if self.open[s][i] < p.stop:
                self._sell(s, i, p.qty, "EXIT_opp_gap_below_stop", "opp", False, (p.entry_ts, p.entry_price, p.plan.get("entry_fee", 0.0), p.entry_idx)); self.opp.pop(s)
        if self.cfg.exit_mode == "t1_partial":                                      # 机会仓位 T1 限价（最高触及）
            for s in list(self.opp):
                p = self.opp[s]; t1 = p.plan.get("target_1")
                if t1 and not p.t1_done and p.entry_idx < i and self.high[s][i] >= t1:
                    px_ref = max(self.open[s][i], t1); q = p.qty * 0.5
                    held = self.L.qty(s); rec = self.L.sell(s, min(q, held), px_ref, self.idx[i], "REDUCE_opp_T1_half")
                    self.fills.append({"ts": self.idx[i], "symbol": s, "side": "SELL", "qty": min(q, held), "ref_price": px_ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (px_ref - rec["price"]) * min(q, held), "reason": "REDUCE_opp_T1_half", "book": "opp"})
                    self.trades.append({"symbol": s, "book": "opp", "entry_ts": p.entry_ts, "exit_ts": self.idx[i], "entry_price": p.entry_price, "exit_price": rec["price"], "qty": min(q, held), "hold_days": i - p.entry_idx, "gross_pnl": rec["gross_pnl"], "fees": rec["fee"], "net_pnl": rec["net_pnl"], "reason": "REDUCE_opp_T1_half", "partial": True})
                    p.qty -= min(q, held); p.t1_done = True; p.stop = max(p.stop, p.entry_price)

    # ---------- 决策 ----------
    def _core_targets(self, i: int, eq: float, cl: dict, dec) -> tuple[dict, dict, set]:
        held = sorted(s for s in self.assets if self.on[s][i])
        cap = max(0.0, min(1.0, float(dec.max_exposure)))
        if dec.reduce_to is not None:
            cap = min(cap, float(dec.reduce_to))
        if dec.force_exit:
            held = []
        w = (cap / len(held)) if held else 0.0; tgt = {}; opp_room = {}
        for s in self.assets:
            if s in held:
                core_t = w * eq * self.cfg.core_frac * (0.5 if self.core_reduced[s] else 1.0); tgt[s] = core_t; opp_room[s] = w * eq - core_t
            else:
                tgt[s] = 0.0; opp_room[s] = 0.0
        return tgt, opp_room, set(held)

    def run(self) -> HoldResult:
        i0 = int(np.searchsorted(self.idx, self.start)); i1 = int(np.searchsorted(self.idx, self.end, side="right")); assert i0 < i1 <= self.n
        prev_close = None
        for i in range(i0, i1):
            ts = self.idx[i]; self._execute(i)
            cl = {s: self.close[s][i] for s in self.assets}; eq = self.L.equity({**{x: 0.0 for x in SYMS}, **cl})
            drop = min((cl[s] / prev_close[s] - 1) for s in self.assets) if prev_close else 0.0
            dec = self.risk.evaluate(ts, eq, drop, self.L.exposure(cl)); tgt, opp_room, held = self._core_targets(i, eq, cl, dec)
            set_changed = held != self.prev_held or (dec.changed and dec.reduce_to is not None) or dec.force_exit
            orders = []
            for s in self.assets:
                st = self.ctx.st[s][i]; ds = self.ctx.ds[s][i]; a = ds["atr"] or np.nan; cur_core_v = self.core_qty[s] * cl[s]
                self.peak_close[s] = max(self.peak_close[s], cl[s]) if (self.core_qty[s] > 0 or s in self.opp) else cl[s]
                # 1) 核心：趋势退出 / 熔断
                if s not in held and self.core_qty[s] > 0:
                    orders.append({"symbol": s, "side": "SELL", "qty": self.core_qty[s], "reason": "EXIT_core_trend_off" if not dec.force_exit else "EXIT_core_circuit_breaker", "book": "core"})
                    self.decisions.append({"ts": ts, "symbol": s, "action": "EXIT", "book": "core", "reason": f"核心退出：{'熔断状态机全部卖出' if dec.force_exit else 'close < SMA200（趋势失效）'}"})
                    if s in self.opp:
                        orders.append({"symbol": s, "side": "SELL", "qty": self.opp[s].qty, "reason": "EXIT_opp_core_trend_off", "book": "opp"})
                    continue
                # 2) 结构 / 波动退出（对已持仓）
                if s in held and (self.core_qty[s] > 0 or s in self.opp) and self.cfg.exit_mode in ("structure_reduce", "vol_trail", "t1_partial"):
                    broke = False; why = ""
                    if self.cfg.exit_mode in ("structure_reduce", "t1_partial") and st["last_low"] and st["last_low_idx"] is not None and (self.core_entry[s] is None or st["last_low_idx"] > self.core_entry[s][2]) and cl[s] < st["last_low"]:
                        broke = True; why = f"收盘 {cl[s]:.0f} < 最近已确认摆动低点 {st['last_low']:.0f}"
                    if self.cfg.exit_mode == "vol_trail" and np.isfinite(a) and cl[s] < self.peak_close[s] - self.cfg.atr_mult * a:
                        broke = True; why = f"收盘 {cl[s]:.0f} < 最高收盘 {self.peak_close[s]:.0f} − {self.cfg.atr_mult} ATR"
                    if broke:
                        if s in self.opp:
                            orders.append({"symbol": s, "side": "SELL", "qty": self.opp[s].qty, "reason": "EXIT_opp_structure", "book": "opp"})
                        if not self.core_reduced[s] and self.core_qty[s] > 0:
                            orders.append({"symbol": s, "side": "SELL", "qty": self.core_qty[s] * 0.5, "reason": "REDUCE_core_structure", "book": "core"}); self.core_reduced[s] = True
                        self.decisions.append({"ts": ts, "symbol": s, "action": "REDUCE", "book": "core+opp", "reason": f"结构/波动退出：{why} → 机会仓位退出、核心减至 50%"}); continue
                    if self.core_reduced[s] and st["last_high"] and cl[s] > st["last_high"]:
                        self.core_reduced[s] = False; tgt[s] = tgt[s] * 2; opp_room[s] = max(0.0, opp_room[s] - tgt[s] / 2)
                        orders.append({"symbol": s, "side": "BUY", "amount": max(0.0, tgt[s] - cur_core_v), "reason": "REENTER_core_structure_restored", "book": "core"})
                        self.decisions.append({"ts": ts, "symbol": s, "action": "ADD", "book": "core", "reason": f"结构恢复：收盘 {cl[s]:.0f} > 最近已确认摆动高点 {st['last_high']:.0f} → 核心恢复满额"}); continue
                # 3) 核心建仓 / 调仓（只在持有集合或限仓变化时）
                if s in held:
                    thr = max(10.0, 0.005 * eq)
                    if self.core_qty[s] <= 1e-12 and tgt[s] >= 10:
                        orders.append({"symbol": s, "side": "BUY", "amount": tgt[s], "reason": "ENTER_core", "book": "core"}); self.decisions.append({"ts": ts, "symbol": s, "action": "BUY", "book": "core", "reason": f"核心入场：close > SMA200，目标 {tgt[s] / eq:.0%}（core_frac {self.cfg.core_frac}，熔断上限 {dec.max_exposure:.0%}）"})
                    elif set_changed and tgt[s] > cur_core_v + thr:
                        orders.append({"symbol": s, "side": "BUY", "amount": tgt[s] - cur_core_v, "reason": "REBALANCE_core_up", "book": "core"})
                    elif set_changed and cur_core_v > tgt[s] + thr:
                        orders.append({"symbol": s, "side": "SELL", "qty": (cur_core_v - tgt[s]) / cl[s], "reason": "RISK_REDUCE_core" if dec.reduce_to is not None else "REBALANCE_core_down", "book": "core"})
                    # 4) 机会加仓：核心已持仓、无机会仓位、触发器命中
                    if self.cfg.pa_add and s not in self.opp and self.core_qty[s] > 0 and opp_room[s] >= 10 and not self.core_reduced[s]:
                        for k in self.cfg.pa_add:
                            r = self.ctx.det_res[s][k][i]
                            if not r["signal"]:
                                continue
                            inval = float(r["invalidation"]); risk_u = cl[s] - inval
                            if risk_u <= 0:
                                continue
                            opp_cur = sum(self.opp[x].qty * cl[x] for x in self.opp)
                            budget = min(opp_room[s], (1 - self.cfg.core_frac) * eq - opp_cur, self.cfg.risk_pct * eq / risk_u * cl[s], self.L.cash * 0.999)
                            if budget < 10:
                                self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "book": "opp", "reason": f"{k} 信号触发但机会预算不足（预算 {budget:.0f} USDT）"}); break
                            zones = self.ctx.zones[s][i]; res = SupportResistanceZones.resistances_above(zones, cl[s]); t1 = float(res[0].low) if res else None
                            z = r["zone"]; plan = {"signal_ts": str(ts), "trigger": k, "market_state": {"weekly": self.ctx.wt[s][i]["trend"], "daily_sma200_rising": ds["sma200_rising"], "risk_state": dec.state}, "confirmed_support_zone": [z.low, z.high, z.strength], "expected_entry_zone": [z.high, cl[s]],
                                    "invalidation": inval, "expected_exit": {"target_1_if_any": t1, "dynamic_rule": self.cfg.exit_mode}, "net_potential_return_to_T1": ((t1 - cl[s]) / cl[s] - 2 * self.c1) if t1 else None, "net_risk_to_invalidation": -(risk_u / cl[s] + 2 * self.c1), "position_pct_of_equity": budget / eq, "target_1": t1, "reason": r["reason"]}
                            orders.append({"symbol": s, "side": "BUY", "amount": budget, "reason": f"ADD_opp_{k}", "book": "opp", "plan": plan, "trigger": k})
                            self.decisions.append({"ts": ts, "symbol": s, "action": "ADD", "book": "opp", "reason": f"机会加仓（{k}）：{r['reason']}；失效 {inval:.0f}，仓位 {budget / eq:.0%}，退出规则 {self.cfg.exit_mode}" + (f"，参考目标 {t1:.0f}" if t1 else "，上方无已确认阻力（动态退出）")}); break
                    # 5) 机会仓位自身止损
                    if s in self.opp:
                        p = self.opp[s]; p.days += 1
                        if cl[s] < p.stop:
                            orders.append({"symbol": s, "side": "SELL", "qty": p.qty, "reason": "EXIT_opp_invalidation", "book": "opp"}); self.decisions.append({"ts": ts, "symbol": s, "action": "EXIT", "book": "opp", "reason": f"机会仓位失效：收盘 {cl[s]:.0f} < {p.stop:.0f}"})
                    if not any(o["symbol"] == s for o in orders) and (self.core_qty[s] > 0):
                        self.decisions.append({"ts": ts, "symbol": s, "action": "HOLD", "book": "core" + ("+opp" if s in self.opp else ""), "reason": f"趋势有效（close > SMA200，状态 {dec.state}）"})
                else:
                    self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "book": "core", "reason": f"close ≤ SMA200 或熔断（状态 {dec.state}）：空仓等待"})
            self.pending = orders; self.prev_held = held
            cl_all = {**{x: 0.0 for x in SYMS}, **cl}
            self.nav_rows.append({"ts": ts, "equity": eq, "liquidation": liquidation_value(self.L, cl_all), "cash": self.L.cash, "exposure": self.L.exposure(cl_all), "core_exposure": sum(self.core_qty[s] * cl[s] for s in self.assets) / eq, "opp_exposure": sum(p.qty * cl[p.symbol] for p in self.opp.values()) / eq,
                                  "drawdown": eq / max([r["equity"] for r in self.nav_rows] + [eq, self.initial]) - 1, "risk_state": dec.state, "holdings": {s: self.L.qty(s) for s in SYMS}, "closes": cl_all})
            prev_close = cl
        nav = pd.DataFrame(self.nav_rows); last = nav.iloc[-1]; cl = last["closes"]
        buy_fees = float(sum(f["fee"] for f in self.fills if f["side"] == "BUY")); fees = float(self.L.fees_paid); slippage = float(sum(f["slippage"] for f in self.fills)) if self.fills else 0.0
        realized = float(self.L.realized) - buy_fees; unreal = float(self.L.unrealized(cl)); liq = float(last["liquidation"]); eqf = float(last["equity"])
        final = {"end_ts": str(last["ts"]), "equity": eqf, "cash": float(self.L.cash), "holdings": {s: self.L.qty(s) for s in SYMS}, "realized_net_usdt": realized, "unrealized_usdt": unreal, "buy_fees_usdt": buy_fees, "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - self.initial, "equity_profit_usdt": eqf - self.initial,
                 "fees_usdt": fees, "slippage_usdt": slippage, "hypothetical_exit_cost_usdt": eqf - liq, "n_fills": len(self.fills), "n_round_trips": int(sum(1 for t in self.trades if not t["partial"])), "open_positions": int(sum(1 for s in self.assets if self.L.qty(s) > 0)), "identity_ok": abs((eqf - self.initial) - (realized + unreal)) < 1e-6 * max(1.0, eqf), "n_opp_adds": len(self.adds)}
        r = HoldResult(self.cfg.name, self.cfg.__dict__, self.fee, self.slip, self.initial, nav, pd.DataFrame(self.trades), pd.DataFrame(self.fills), final); r.decisions = pd.DataFrame(self.decisions); r.adds = pd.DataFrame(self.adds); return r
