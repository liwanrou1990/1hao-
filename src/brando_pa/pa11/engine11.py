"""第十一阶段组合回测引擎：BTC/ETH 现货，日收盘决策（BUY/WAIT/HOLD/REDUCE/EXIT + 理由），次日开盘成交；T1/T2 为入场时已定的限价单（当日最高触及即按目标价成交，
开盘跳空越过则按开盘价）；失效 / 结构 / 跟踪退出按收盘判定、次日开盘成交；开盘跳空低于失效价按开盘价止损。禁止杠杆 / 负现金。复用 SpotLedger 与 liquidation_value。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..holding.engine import HoldResult
from ..spot.ledger import SpotLedger
from ..usdt.engine import liquidation_value
from .macro import MacroEventFilter
from .signals import BreakoutRetestDetector, EntryRiskRewardEvaluator, FailedBreakdownDetector, PullbackConfirmation
from .structure import DailyStructureDetector, WeeklyTrendDetector, structure_state, swings
from .zones import SupportResistanceZones

SYMS = ["BTCUSDT", "ETHUSDT"]


@dataclass
class Trade:
    symbol: str
    entry_idx: int
    entry_ts: pd.Timestamp
    entry_price: float
    qty: float
    plan: dict
    strategy: str
    stop: float
    t1_done: bool = False
    peak_close: float = 0.0
    entry_fee: float = 0.0
    days_held: int = 0
    planned_risk_usdt: float = 0.0


@dataclass
class Config:
    strategies: tuple = ("A",)              # 子集 of ("A","B","C")
    exit_mode: str = "B"                     # "A" | "B" | "C"
    risk_pct: float = 0.01
    max_alloc: float = 0.70
    dd_limit: float | None = 0.30            # None = 无回撤约束
    macro_mode: str = "none"                 # none | pre_event | post_event
    min_rr: float = 1.5
    min_rr_c: float = 2.0
    combined_risk_mult: float = 1.5
    vol_adj: bool = True
    name: str = "V2"


class MarketContext:
    """与配置无关的逐日状态（周线趋势、日线结构、区域、A/B/C 检测结果），只算一次，供全部比较组共享。第 i 日的内容只依赖 ≤ i 的数据。"""

    def __init__(self, bars: dict):
        idx = None
        for s in SYMS:
            t = pd.DatetimeIndex(pd.to_datetime(bars[s]["ts"], utc=True)); idx = t if idx is None else idx.intersection(t)
        self.idx = idx.sort_values(); self.n = len(self.idx)
        self.d = {}; self.w = {}; self.z = {}; self.open = {}; self.high = {}; self.low = {}; self.close = {}; self.wt = {}; self.ds = {}; self.zones = {}; self.st = {}; self.det_res = {}
        det = {"A": PullbackConfirmation(), "B": BreakoutRetestDetector(), "C": FailedBreakdownDetector()}
        for s in SYMS:
            b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.set_index("ts").loc[self.idx].reset_index(); self.d[s] = DailyStructureDetector(b); self.w[s] = WeeklyTrendDetector(b)
            wsw = self.w[s].sw; week_end = self.w[s].week_end
            def w2d(j, _we=week_end): return int(np.searchsorted(self.idx, _we[min(j, len(_we) - 1)], side="right")) - 1
            self.z[s] = SupportResistanceZones(self.d[s].sw, wsw, w2d, self.d[s].atr)
            self.open[s] = b["open"].to_numpy(float); self.high[s] = b["high"].to_numpy(float); self.low[s] = b["low"].to_numpy(float); self.close[s] = b["close"].to_numpy(float)
            self.d[s].atr_pct_q80 = pd.Series(self.d[s].atr / self.close[s]).rolling(365, min_periods=200).quantile(0.8).to_numpy()
            self.wt[s] = [self.w[s].at(i) for i in range(self.n)]; self.ds[s] = [self.d[s].at(i) for i in range(self.n)]
            self.zones[s] = [self.z[s].zones_at(i, self.close[s][i]) for i in range(self.n)]; self.st[s] = [structure_state(self.d[s].sw, i) for i in range(self.n)]
            self.det_res[s] = {k: [det[k].check(self.d[s], self.z[s], i, self.zones[s][i]) if i >= 30 else {"signal": False, "reason": "数据不足"} for i in range(self.n)] for k in det}


class PriceActionBacktester:
    def __init__(self, bars: dict, cfg: Config, fee_bps: float, slip_bps: float, start, end, initial: float = 10000.0, events: pd.DataFrame | None = None, ctx: MarketContext | None = None):
        self.cfg = cfg; self.fee, self.slip = float(fee_bps), float(slip_bps); self.initial = float(initial); self.c1 = (self.fee + self.slip) / 1e4
        self.start = pd.Timestamp(start, tz="UTC"); self.end = pd.Timestamp(end, tz="UTC")
        self.ctx = ctx or MarketContext(bars); c = self.ctx
        self.idx = c.idx; self.n = c.n; self.d = c.d; self.w = c.w; self.z = c.z; self.open = c.open; self.high = c.high; self.low = c.low; self.close = c.close
        self.macro = MacroEventFilter(events, cfg.macro_mode); self.rr = EntryRiskRewardEvaluator(cfg.min_rr); self.rr_c = EntryRiskRewardEvaluator(cfg.min_rr_c)
        self.L = SpotLedger(self.initial, self.fee, self.slip); self.pos: dict[str, Trade] = {}; self.pending: list[dict] = []
        self.fills = []; self.trades = []; self.nav_rows = []; self.decisions = []; self.signals = []; self.peak = self.initial; self.b_state = {s: None for s in SYMS}

    # ---------- 执行 ----------
    def _fill_sell(self, s: str, i: int, qty: float, ref: float, reason: str, partial: bool) -> None:
        ts = self.idx[i]; held = self.L.qty(s); q = min(qty, held)
        if q <= 1e-12:
            return
        rec = self.L.sell(s, q, ref, ts, reason); p = self.pos[s]; efee = 0.0 if partial else p.entry_fee
        self.fills.append({"ts": ts, "symbol": s, "side": "SELL", "qty": q, "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (ref - rec["price"]) * q, "reason": reason})
        self.trades.append({"symbol": s, "strategy": p.strategy, "entry_ts": p.entry_ts, "exit_ts": ts, "entry_price": p.entry_price, "exit_price": rec["price"], "qty": q, "hold_days": p.days_held, "gross_pnl": rec["gross_pnl"], "fees": rec["fee"] + efee, "net_pnl": rec["net_pnl"] - efee, "reason": reason, "partial": partial,
                            "planned_invalidation": p.plan["invalidation"], "planned_t1": p.plan["target_1"], "planned_t2": p.plan.get("target_2"), "planned_rr": p.plan["expected_net_reward_risk"], "r_multiple": (rec["price"] - p.entry_price) / max(p.entry_price - p.plan["invalidation"], 1e-9)})
        if not partial or self.L.qty(s) <= 1e-12:
            self.pos.pop(s, None)

    def _execute_open(self, i: int) -> None:
        """开盘：执行昨日收盘决策（先卖后买），并检查跳空止损。"""
        ts = self.idx[i]
        for o in sorted(self.pending, key=lambda o: 0 if o["side"] == "SELL" else 1):
            s = o["symbol"]; op = self.open[s][i]
            if o["side"] == "SELL":
                self._fill_sell(s, i, o["qty"], op, o["reason"], o.get("partial", False))
            else:
                amt = min(o["amount"], self.L.cash * 0.999); px = op * (1 + self.slip / 1e4) * (1 + self.fee / 1e4); q = amt / px
                if q <= 0 or q * op < 10:
                    self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": "买入金额不足最小下单 10 USDT（现金/配置限制）"}); continue
                lot = self.L.buy(s, q, op, ts, o["strategy"], 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]
                self.fills.append({"ts": ts, "symbol": s, "side": "BUY", "qty": q, "ref_price": op, "fill_price": lot.price, "fee": fee, "slippage": (lot.price - op) * q, "reason": o["strategy"]})
                plan = dict(o["plan"]); plan["actual_entry"] = lot.price
                self.pos[s] = Trade(s, i, ts, lot.price, q, plan, o["strategy"], plan["invalidation"], peak_close=lot.price, entry_fee=fee, planned_risk_usdt=o["planned_risk_usdt"])
        self.pending = []
        # 跳空止损：开盘 < 当前止损
        for s in list(self.pos):
            p = self.pos[s]
            if self.open[s][i] < p.stop and self.L.qty(s) > 0:
                self._fill_sell(s, i, self.L.qty(s), self.open[s][i], "EXIT_gap_below_stop", False)

    def _intraday_targets(self, i: int) -> None:
        """入场时已挂的 T1/T2 限价单：当日最高价触及 → 按目标价成交（开盘已越过 → 开盘价）。"""
        for s in list(self.pos):
            p = self.pos[s]
            if p.entry_idx == i or self.cfg.exit_mode == "C":
                continue
            hi = self.high[s][i]; op = self.open[s][i]
            if not p.t1_done and hi >= p.plan["target_1"]:
                px = max(op, p.plan["target_1"]) if op >= p.plan["target_1"] else p.plan["target_1"]
                if self.cfg.exit_mode == "A":
                    self._fill_sell(s, i, self.L.qty(s), px, "EXIT_target_1", False)
                else:
                    self._fill_sell(s, i, self.L.qty(s) * 0.5, px, "REDUCE_target_1_half", True)
                    if s in self.pos:
                        self.pos[s].t1_done = True; self.pos[s].stop = max(self.pos[s].stop, self.pos[s].entry_price)
                        self.decisions.append({"ts": self.idx[i], "symbol": s, "action": "REDUCE", "reason": f"触及第一目标 {p.plan['target_1']:.0f}：卖出 50%，止损上移至入场价 {self.pos[s].entry_price:.0f}"})
                continue
            if p.t1_done and p.plan.get("target_2") and hi >= p.plan["target_2"] and self.cfg.exit_mode == "B":
                px = op if op >= p.plan["target_2"] else p.plan["target_2"]
                self._fill_sell(s, i, self.L.qty(s), px, "EXIT_target_2", False)

    # ---------- 决策 ----------
    def _dd_factor(self, eq: float) -> tuple[float, str]:
        if self.cfg.dd_limit is None:
            return 1.0, ""
        dd = eq / self.peak - 1
        if dd <= -self.cfg.dd_limit:
            return 0.0, f"回撤 {dd:.1%} 超过约束 {self.cfg.dd_limit:.0%}：不新开仓（现金保护）"
        if dd <= -self.cfg.dd_limit / 2:
            return 0.5, f"回撤 {dd:.1%} 超过约束一半：新仓位减半"
        return 1.0, ""

    def _decide_entry(self, s: str, i: int, eq: float, cl: dict) -> None:
        ts = self.idx[i]; d = self.d[s]; wt = self.ctx.wt[s][i]; ds = self.ctx.ds[s][i]
        zones = self.ctx.zones[s][i]; st = self.ctx.st[s][i]
        cands = []
        for k in self.cfg.strategies:
            if k in ("A", "B") and not (wt["trend"] == "UP" and ds["sma_filter_ok"]):
                cands.append((k, {"signal": False, "reason": f"前置不满足：周线趋势 {wt['trend']}，日线 SMA200 过滤 {'通过' if ds['sma_filter_ok'] else '未通过'}"})); continue
            if k == "C" and wt["trend"] == "DOWN":
                cands.append((k, {"signal": False, "reason": "周线下行，不做假跌破"})); continue
            cands.append((k, self.ctx.det_res[s][k][i]))
        hit = [(k, r) for k, r in cands if r["signal"]]
        if not hit:
            self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": " | ".join(f"{k}: {r['reason']}" for k, r in cands)}); return
        k, r = hit[0]; z = r["zone"]; ev = self.rr_c if k == "C" else self.rr
        plan = ev.plan(cl[s], r["invalidation"], zones, st, self.c1, (z.low, z.high), r["reason"])
        if not plan["ok"]:
            self.signals.append({"ts": ts, "symbol": s, "strategy": k, "accepted": False, **{kk: vv for kk, vv in plan.items() if kk != "ok"}}); self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": plan["reason"]}); return
        ok_m, why_m = self.macro.allows_new_entry(ts)
        if not ok_m:
            self.signals.append({"ts": ts, "symbol": s, "strategy": k, "accepted": False, "reason": why_m, **{kk: vv for kk, vv in plan.items() if kk not in ("ok", "reason")}}); self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": why_m + "；" + plan["reason"]}); return
        ddf, why_dd = self._dd_factor(eq)
        if ddf == 0.0:
            self.signals.append({"ts": ts, "symbol": s, "strategy": k, "accepted": False, "reason": why_dd, **{kk: vv for kk, vv in plan.items() if kk not in ("ok", "reason")}}); self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": why_dd}); return
        volf = 1.0
        if self.cfg.vol_adj and np.isfinite(d.atr_pct_q80[i]) and (d.atr[i] / cl[s]) > d.atr_pct_q80[i]:
            volf = 0.5
        risk_budget = self.cfg.risk_pct * eq * ddf * volf
        other = sum(p.planned_risk_usdt for p in self.pos.values()); cap_risk = self.cfg.combined_risk_mult * self.cfg.risk_pct * eq
        if other + risk_budget > cap_risk:
            risk_budget = max(0.0, cap_risk - other)
        qty = risk_budget / plan["risk_per_unit"]; value = qty * cl[s]
        exposure_v = sum(self.L.qty(x) * cl[x] for x in SYMS); room = self.cfg.max_alloc * eq - exposure_v
        value = min(value, room, self.L.cash * 0.999)
        if value < 10:
            self.decisions.append({"ts": ts, "symbol": s, "action": "WAIT", "reason": f"仓位不足：风险预算 {risk_budget:.0f} USDT / 配置余量 {room:.0f} USDT" + (f"；{why_dd}" if why_dd else "")}); return
        est_cost = value * self.c1 * 2
        plan |= {"position_size_usdt": float(value), "position_size_pct": float(value / eq), "estimated_total_cost_usdt": float(est_cost), "planned_risk_usdt": float(min(risk_budget, value / cl[s] * plan["risk_per_unit"])), "dd_factor": ddf, "vol_factor": volf, "decision_reason": plan["reason"], "weekly": wt["trend"], "signal_ts": str(ts)}
        self.signals.append({"ts": ts, "symbol": s, "strategy": k, "accepted": True, **{kk: vv for kk, vv in plan.items() if kk != "ok"}})
        self.decisions.append({"ts": ts, "symbol": s, "action": "BUY", "reason": f"{plan['reason']}；失效 {plan['invalidation']:.0f}，T1 {plan['target_1']:.0f}，T2 {plan['target_2'] if plan['target_2'] is None else round(plan['target_2'])}，净盈亏比 {plan['expected_net_reward_risk']:.2f}，仓位 {value:.0f} USDT（{value / eq:.0%}）" + (f"；{why_dd}" if why_dd else "") + ("；高波动仓位减半" if volf < 1 else "")})
        self.pending.append({"symbol": s, "side": "BUY", "amount": value, "plan": plan, "strategy": k, "planned_risk_usdt": plan["planned_risk_usdt"]})

    def _manage(self, s: str, i: int, cl: dict) -> None:
        p = self.pos[s]; ts = self.idx[i]; p.days_held += 1; p.peak_close = max(p.peak_close, cl[s]); d = self.d[s]; st = self.ctx.st[s][i]; a = d.atr[i]
        reason = None
        if cl[s] < p.stop:
            reason = f"EXIT_invalidation：收盘 {cl[s]:.0f} < 止损/失效 {p.stop:.0f}"
        elif self.cfg.exit_mode == "C":
            trail = p.peak_close - 3 * a if np.isfinite(a) else -np.inf; p.stop = max(p.stop, trail)
            if st["last_low"] and st["last_low_idx"] is not None and st["last_low_idx"] > p.entry_idx and cl[s] < st["last_low"]:
                reason = f"EXIT_structure：收盘 {cl[s]:.0f} < 最近已确认摆动低点 {st['last_low']:.0f}"
            elif cl[s] < trail:
                reason = f"EXIT_trailing：收盘 {cl[s]:.0f} < 跟踪止盈 {trail:.0f}（最高收盘 − 3 ATR）"
        elif self.cfg.exit_mode == "B" and p.t1_done and st["last_low"] and st["last_low_idx"] is not None and st["last_low_idx"] > p.entry_idx and cl[s] < st["last_low"]:
            reason = f"EXIT_structure：T1 后收盘 {cl[s]:.0f} < 最近已确认更高低点 {st['last_low']:.0f}"
        if reason:
            self.pending.append({"symbol": s, "side": "SELL", "qty": self.L.qty(s), "reason": reason.split("：")[0], "partial": False}); self.decisions.append({"ts": ts, "symbol": s, "action": "EXIT", "reason": reason})
        else:
            self.decisions.append({"ts": ts, "symbol": s, "action": "HOLD", "reason": f"持有第 {p.days_held} 日；止损 {p.stop:.0f}，T1 {p.plan['target_1']:.0f}{'（已部分获利）' if p.t1_done else ''}，趋势未失效"})

    def run(self) -> HoldResult:
        i0 = int(np.searchsorted(self.idx, self.start)); i1 = int(np.searchsorted(self.idx, self.end, side="right"))
        assert i0 < i1 <= self.n, "窗口内无数据"
        for i in range(i0, i1):
            self._execute_open(i); self._intraday_targets(i)
            cl = {s: self.close[s][i] for s in SYMS}; eq = self.L.equity(cl); self.peak = max(self.peak, eq)
            for s in SYMS:
                if s in self.pos:
                    self._manage(s, i, cl)
                else:
                    self._decide_entry(s, i, eq, cl)
            self.nav_rows.append({"ts": self.idx[i], "equity": eq, "liquidation": liquidation_value(self.L, cl), "cash": self.L.cash, "exposure": self.L.exposure(cl), "drawdown": eq / self.peak - 1, "risk_state": "NORMAL", "holdings": {s: self.L.qty(s) for s in SYMS}, "closes": cl})
        nav = pd.DataFrame(self.nav_rows); last = nav.iloc[-1]; cl = last["closes"]
        buy_fees = float(sum(f["fee"] for f in self.fills if f["side"] == "BUY")); fees = float(self.L.fees_paid); slippage = float(sum(f["slippage"] for f in self.fills)) if self.fills else 0.0
        realized = float(self.L.realized) - buy_fees; unreal = float(self.L.unrealized(cl)); liq = float(last["liquidation"]); eqf = float(last["equity"])
        final = {"end_ts": str(last["ts"]), "equity": eqf, "cash": float(self.L.cash), "holdings": {s: self.L.qty(s) for s in SYMS}, "realized_net_usdt": realized, "unrealized_usdt": unreal, "buy_fees_usdt": buy_fees, "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - self.initial, "equity_profit_usdt": eqf - self.initial,
                 "fees_usdt": fees, "slippage_usdt": slippage, "hypothetical_exit_cost_usdt": eqf - liq, "n_fills": len(self.fills), "n_round_trips": int(sum(1 for t in self.trades if not t["partial"])), "open_positions": len(self.pos), "identity_ok": abs((eqf - self.initial) - (realized + unreal)) < 1e-6 * max(1.0, eqf),
                 "n_signals_accepted": int(sum(1 for x in self.signals if x["accepted"])), "n_signals_rejected": int(sum(1 for x in self.signals if not x["accepted"]))}
        r = HoldResult(self.cfg.name, self.cfg.__dict__, self.fee, self.slip, self.initial, nav, pd.DataFrame(self.trades), pd.DataFrame(self.fills), final)
        r.decisions = pd.DataFrame(self.decisions); r.signals = pd.DataFrame(self.signals); return r
