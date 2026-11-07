"""第五阶段现货引擎：趋势状态 + 三种研究模型 + 三种退出 + 硬性风险限制，一个现货账本（现金/持仓约束同第四阶段）。

决策仍只有 BUY / HOLD / SELL；信号在 1h 收盘确认、下一根开盘成交；全部持仓按最后收盘计价。
仓位“袖子”(sleeve)：core（趋势管理）/ tactical（Brando 价格行为管理）；袖子预算 = 当前净值 × 比例，逐笔检查现金与总敞口 ≤ 100%，禁止隐含杠杆。
退出模式（独立运行，不混合）：
  A_structure_stop：Brando 结构止损（战术）；趋势入场的“结构”= min(最近已确认 1h 摆动低, 日线 SMA50 − 1×ATR(1d)) − 0.2×ATR(1h)
  B_trend_failure ：日线状态离开 BULL 且持续 grace_days 个日线收盘 → 退出该袖子的全部持仓（战术袖子同样适用）
  C_atr_trailing  ：吊灯止损：自入场以来最高 1h 收盘 − atr_mult×ATR(1d)，只上移
硬性限制（任何模式都有）：账户回撤 ≥ 20% 暂停新买入（收窄到 10% 恢复）；单资产持仓 ≤ 上限；单日线跌幅 ≥ 15% 的异常风险退出（全部卖出）。
不再使用 1R 保本作为默认规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..backtest.runner import load_contexts
from ..config import cfg, deep_update
from ..spot.ledger import SpotConstraintError, SpotLedger
from ..spot.strategies import BreakdownReclaimBuy, BreakoutRetestBuy, ResistanceRejectionSell, SpotBar
from ..spot.structure import all_levels, last_confirmed_swing_low, local_levels
from .regime import TrendRegimeEngine

MODELS = ("A_trend_following", "B_core_satellite", "C_trend_filtered_brando", "BRANDO_original", "BENCH_sma200")
EXITS = ("A_structure_stop", "B_trend_failure", "C_atr_trailing")


@dataclass
class TrendResult:
    model: str; exit_mode: str; symbols: list; exec_tf: str; period: tuple
    trades: pd.DataFrame; fills: pd.DataFrame; equity: pd.DataFrame; decisions: pd.DataFrame
    final: dict; counters: dict = field(default_factory=dict); regime_series: pd.Series | None = None; notes: list = field(default_factory=list)


class TrendSpotBacktester:
    def __init__(self, model: str, exit_mode: str, c: dict | None = None, symbols: list[str] | None = None, exec_tf: str | None = None, start: str | None = None, end: str | None = None,
                 fee_bps: float = 0.0, slippage_bps: float = 0.0, override: dict | None = None):
        self.c = deep_update(c or cfg(), override or {}); tc = self.c["trend"]
        self.model, self.exit_mode = model, exit_mode
        self.symbols = symbols or tc["symbols"]; self.exec_tf = exec_tf or tc["exec_tf"]
        self.start = pd.Timestamp(start, tz="UTC") if start else None; self.end = pd.Timestamp(end, tz="UTC") if end else None
        self.fee, self.slip = float(fee_bps), float(slippage_bps)
        self.initial = float(tc["initial_cash_usdt"]); self.dual = len(self.symbols) > 1

    # ---------------- 主循环 ----------------
    def run(self, contexts: dict | None = None) -> TrendResult:
        c, tc = self.c, self.c["trend"]; sc = c["spot"]
        ctxs = {s: (contexts or load_contexts(self.symbols, self.exec_tf, c))[s] for s in self.symbols}
        reg = {s: TrendRegimeEngine(ctxs[s], tc["regime"]) for s in self.symbols}
        L = SpotLedger(self.initial, self.fee, self.slip)
        hl = tc["exits"]["hard_limits"]; xcfg = tc["exits"].get(self.exit_mode, {})
        max_pos = float(hl["max_position_pct_dual"] if self.dual else hl["max_position_pct"])
        mA, mB, mC = tc["models"]["A_trend_following"], tc["models"]["B_core_satellite"], tc["models"]["C_trend_filtered_brando"]
        brando = {s: {"A": BreakoutRetestBuy(sc["strategies"]["A_breakout_retest_buy"]), "B": BreakdownReclaimBuy(sc["strategies"]["B_breakdown_reclaim_buy"]), "C": ResistanceRejectionSell(sc["strategies"]["C_resistance_rejection_sell"])} for s in self.symbols}
        lc = sc["local_structure"]
        all_ts = sorted(set().union(*[set(mc.ts.tolist()) for mc in ctxs.values()]))
        if self.start is not None:
            all_ts = [t for t in all_ts if t >= self.start]
        if self.end is not None:
            all_ts = [t for t in all_ts if t <= self.end]
        pos_of = {s: {pd.Timestamp(t): i for i, t in enumerate(mc.ts.tolist())} for s, mc in ctxs.items()}
        pending: dict[str, list] = {}; eq_rows, dec_rows, trade_rows = [], [], []
        peak = self.initial; halted = False; last_px: dict[str, float] = {}
        counters = {s: {"core_entries": 0, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0} for s in self.symbols}
        core_ever = {s: False for s in self.symbols}
        reg_rows = []
        for t in all_ts:
            t = pd.Timestamp(t)
            for s in self.symbols:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = ctxs[s]; o, h, l, cl = float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), float(mc.cl[i])
                # (a) 成交
                for pend in pending.pop(s, []):
                    self._fill(L, pend, s, o, t, i, mc, max_pos, trade_rows, dec_rows, counters, {**last_px, s: o})
                # (b) 持仓管理：异常跌幅 → 全部退出；否则按退出模式
                r = reg[s].at(i)
                crash = r.get("j", -1) >= 1 and (reg[s]._close[r["j"]] / reg[s]._close[r["j"] - 1] - 1) <= -float(hl["crash_exit_daily_drop"])
                for lot in list(L.lots.get(s, [])):
                    lot.mae = max(lot.mae, lot.price - l); lot.mfe = max(lot.mfe, h - lot.price)
                    sleeve = lot.strategy.split("|")[0]
                    if crash and L.qty(s) > 0:
                        rec = L.sell(s, lot.qty, o, t, "crash_exit", [lot.lot_id]); trade_rows += self._rows(rec, s, t, i, mc, r); counters[s]["exit_crash"] += 1; continue
                    tactical_in_B = (self.model == "B_core_satellite" and sleeve == "tactical")      # 模型 B 的战术袖子：永远由 Brando 结构止损 + 策略 C 管理
                    if self.exit_mode in ("A_structure_stop", "C_atr_trailing") or tactical_in_B:
                        if lot.stop is not None and l <= lot.stop:
                            px = o if o <= lot.stop else lot.stop
                            rec = L.sell(s, lot.qty, px, t, "stop_gap" if o <= lot.stop else ("trail_stop" if lot.trailed else "structure_stop"), [lot.lot_id]); trade_rows += self._rows(rec, s, t, i, mc, r)
                            counters[s]["exit_trail" if lot.trailed else "exit_structure"] += 1; continue
                    if self.exit_mode == "C_atr_trailing" and r.get("atr_1d") and not tactical_in_B:
                        hc = getattr(lot, "_hc", lot.price); hc = max(hc, cl); lot._hc = hc
                        new_stop = hc - float(xcfg["atr_mult"]) * r["atr_1d"]
                        if lot.stop is None or new_stop > lot.stop:
                            lot.stop = new_stop; lot.trailed = True
                    if self.exit_mode == "B_trend_failure" and not tactical_in_B:
                        if reg[s].days_not_in(i, "BULL") >= int(xcfg["grace_days"]) and r["state"] != "BULL":
                            rec = L.sell(s, lot.qty, cl, t, "trend_failure", [lot.lot_id]); trade_rows += self._rows(rec, s, t, i, mc, r); counters[s]["exit_trend"] += 1; continue
                last_px[s] = cl
            prices = {s: last_px.get(s, np.nan) for s in self.symbols}
            equity = L.equity(prices); peak = max(peak, equity); dd = equity / peak - 1
            if dd <= -float(hl["max_drawdown_halt"]):
                halted = True
            elif halted and dd > -float(hl["max_drawdown_halt"]) / 2:
                halted = False
            states = {s: reg[s].at(pos_of[s][t])["state"] if t in pos_of[s] else None for s in self.symbols}
            eq_rows.append({"ts": t, "equity": equity, "cash": L.cash, "exposure": L.exposure(prices), **{f"qty_{s}": L.qty(s) for s in self.symbols}, **{f"state_{s}": states[s] for s in self.symbols}, "drawdown": dd, "halted": halted})
            # (c) 决策
            for s in self.symbols:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = ctxs[s]; a = float(mc.atr_ex[i])
                if i < 1 or not np.isfinite(a) or a <= 0 or any(mc.htf[tf]["idx"][i] < 0 for tf in mc.level_tfs):
                    continue
                r = reg[s].at(i); px = float(mc.cl[i]); holding = L.qty(s)
                core_qty = sum(x.qty for x in L.lots.get(s, []) if x.strategy.startswith("core")); tac_qty = holding - core_qty
                decision, reasons, chosen = "HOLD", [], None
                n_ok = int(np.searchsorted(mc._exec_swing_conf, i, side="right")); swings = mc.exec_swings[:n_ok]
                orders = []
                # ---- 趋势袖子（模型 A / B 的 core）----
                if self.model in ("A_trend_following", "B_core_satellite"):
                    frac = float(mA["allocation"]) if self.model == "A_trend_following" else float(mB["core_fraction"])
                    frac = min(frac, max_pos)
                    if r["state"] == "BULL" and core_qty <= 0:
                        target = frac * equity; need = target - core_qty * px
                        qty = need / px
                        ok, why = self._check(L, s, qty, px, equity, holding, max_pos, halted, counters)
                        if ok:
                            stop = self._trend_structure_stop(swings, i, r, a, px, tc) if self.exit_mode == "A_structure_stop" else (px - float(xcfg["atr_mult"]) * r["atr_1d"] if self.exit_mode == "C_atr_trailing" and r.get("atr_1d") else None)
                            orders.append({"action": "BUY", "sleeve": "core", "strategy": "core|trend_BULL", "qty": qty, "stop": stop, "reasons": [f"日线趋势 BULL（SMA50 {r['sma50']:.0f} > SMA200 {r['sma200']:.0f}，斜率 {reg[s].daily['slope50'].iloc[r['j']]:+.2%}）→ 核心仓位 {frac:.0%}", f"退出模式 {self.exit_mode}" + (f"，初始止损 {stop:.2f}" if stop else "")]})
                            counters[s]["core_entries"] += 1
                            if core_ever[s]:
                                counters[s]["core_reentries"] += 1
                            core_ever[s] = True
                        else:
                            reasons.append("趋势 BULL 但买入被拒：" + "；".join(why))
                # ---- 战术 / 过滤 Brando ----
                use_brando = self.model in ("B_core_satellite", "C_trend_filtered_brando", "BRANDO_original")
                if use_brando:
                    res, sup = local_levels(swings, i, mc.cl, a, lc); alv = all_levels(swings, i, mc.cl, a, lc)
                    zones, _ = mc.zones_at(i); htf_res = [z for z in zones if z.role(px) == "resistance" and any(tf in sc["htf_context"]["zone_tfs"] for tf in z.tfs)]
                    bar = SpotBar(s, i, t, float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), px, float(mc.cl[i - 1]), a, res, sup, alv, htf_res, {"trend": r["state"]}, tac_qty if self.model == "B_core_satellite" else holding)
                    sigs = [g for g in (brando[s][k].on_bar(bar) for k in ("A", "B", "C")) if g is not None]
                    allowed = True; why_f = []
                    if self.model == "C_trend_filtered_brando":
                        if r["state"] != "BULL":
                            allowed = False; why_f.append(f"日线状态 {r['state']} ≠ BULL")
                        if mC.get("require_4h_not_down", True) and r.get("trend_4h") == "down":
                            allowed = False; why_f.append("4h 结构 down")
                    buys = [g for g in sigs if g.action == "BUY"]; sells = [g for g in sigs if g.action == "SELL"]
                    sleeve_cap = float(mB["tactical_fraction"]) if self.model == "B_core_satellite" else float(mC.get("max_position_pct", 1.0)) if self.model == "C_trend_filtered_brando" else max_pos
                    risk_pct = float(mB["tactical_risk_per_trade"]) if self.model == "B_core_satellite" else float(mC.get("risk_per_trade", 0.01)) if self.model == "C_trend_filtered_brando" else float(sc["risk"]["risk_per_trade"])
                    if buys:
                        g = buys[0]
                        if not allowed:
                            counters[s]["buy_rejected_regime"] += 1; reasons.append(f"Brando {g.strategy} 买点被趋势过滤拒绝：" + "；".join(why_f))
                        elif self.model == "BRANDO_original" and r.get("state") == "BEAR" and False:
                            pass
                        else:
                            budget_eq = equity * sleeve_cap
                            held_val = tac_qty * px if self.model == "B_core_satellite" else holding * px
                            risk_usd = budget_eq * risk_pct if self.model == "B_core_satellite" else equity * risk_pct
                            dist = g.ref_price - g.stop
                            qty = min(risk_usd / dist, max(0.0, budget_eq - held_val) / px)
                            ok, why = self._check(L, s, qty, px, equity, holding, max_pos, halted, counters)
                            if ok:
                                stop = g.stop if (self.exit_mode == "A_structure_stop" or self.model == "B_core_satellite") else (px - float(xcfg["atr_mult"]) * r["atr_1d"] if self.exit_mode == "C_atr_trailing" and r.get("atr_1d") else None)
                                orders.append({"action": "BUY", "sleeve": "tactical", "strategy": f"tactical|{g.strategy}", "qty": qty, "stop": stop, "reasons": [f"Brando {g.strategy}：" + "；".join(g.reasons), f"日线 {r['state']}，4h {r.get('trend_4h')}；退出模式 {self.exit_mode}"]})
                                counters[s]["tactical_entries"] += 1
                            else:
                                reasons.append(f"Brando {g.strategy} 买点被拒：" + "；".join(why))
                    if sells:
                        g = sells[0]
                        if self.model == "C_trend_filtered_brando":
                            frac_s = 0.25 if mC.get("strategy_c_action") == "reduce_25" else 0.5
                            if holding > 0:
                                orders.append({"action": "SELL", "sleeve": "tactical", "strategy": "reduce|C_rejection", "qty": holding * frac_s, "reasons": [f"策略 C 阻力拒绝 → 风险提示，减仓 {frac_s:.0%}：" + "；".join(g.reasons)]}); counters[s]["exit_rejection_reduce"] += 1
                        elif self.model == "BRANDO_original" and holding > 0:
                            orders.append({"action": "SELL", "sleeve": "tactical", "strategy": "sell|C_rejection", "qty": holding * g.sell_fraction, "reasons": ["策略 C 阻力拒绝：" + "；".join(g.reasons)]})
                        elif self.model == "B_core_satellite" and tac_qty > 0:
                            orders.append({"action": "SELL", "sleeve": "tactical", "strategy": "sell|C_rejection", "qty": tac_qty * g.sell_fraction, "reasons": ["战术袖子：策略 C 阻力拒绝：" + "；".join(g.reasons)]})
                if orders:
                    pending[s] = orders
                    decision = "BUY" if any(o_["action"] == "BUY" for o_ in orders) else "SELL"
                    chosen = orders[0]; reasons = [x for o_ in orders for x in o_["reasons"]]
                elif not reasons:
                    reasons = [f"日线 {r['state']}，4h {r.get('trend_4h')}；" + ("持有现货，按退出模式管理" if holding > 0 else ("持有 USDT：等待日线 BULL" if self.model != "BRANDO_original" else "持有 USDT：等待 Brando 买点"))]
                dec_rows.append({"ts": t, "symbol": s, "decision": decision, "state": r["state"], "trend_4h": r.get("trend_4h"), "price": px, "holding_qty": holding, "core_qty": core_qty, "tactical_qty": tac_qty, "cash": L.cash, "equity": equity, "strategy": chosen["strategy"] if chosen else "", "reasons": " | ".join(reasons)})
        prices = {s: last_px.get(s, np.nan) for s in self.symbols}
        final = L.snapshot(prices) | {"initial_cash": self.initial, "last_prices": prices, "peak_equity": peak}
        trades = pd.DataFrame(trade_rows); fills = pd.DataFrame([{k: v for k, v in r_.items() if k != "closed_lots"} for r_ in L.trade_log]); eq = pd.DataFrame(eq_rows)
        period = (str(all_ts[0])[:19] if all_ts else "", str(all_ts[-1])[:19] if all_ts else "")
        return TrendResult(self.model, self.exit_mode, self.symbols, self.exec_tf, period, trades, fills, eq, pd.DataFrame(dec_rows), final, counters,
                           notes=[f"费用 {self.fee} bps / 滑点 {self.slip} bps", "只做多现货；下一根开盘成交；无杠杆；止损只上移；硬性限制：回撤 20% 暂停买入、单日跌 15% 异常退出"])

    # ---------------- 辅助 ----------------
    def _trend_structure_stop(self, swings, i, r, a, px, tc) -> float:
        swl = last_confirmed_swing_low(swings, i)
        cands = [x for x in (swl, (r["sma50"] - r["atr_1d"]) if r.get("sma50") and r.get("atr_1d") else None) if x is not None and x < px]
        base = min(cands) if cands else px - 3 * a
        return base - float(tc["exits"]["A_structure_stop"]["stop_buffer_atr"]) * a

    def _check(self, L, s, qty, px, equity, holding, max_pos, halted, counters):
        why = []
        if halted:
            why.append("账户回撤 ≥ 20%，暂停新买入"); counters[s]["buy_rejected_halt"] += 1
        cap = max(0.0, max_pos * equity - holding * px) / px
        cash_cap = L.cash / (px * (1 + self.slip / 1e4) * (1 + self.fee / 1e4)) * 0.999
        q = min(qty, cap, cash_cap)
        if q * px < 10:
            why.append(f"可买名义额不足 10 USDT（现金 {L.cash:.2f}，持仓上限 {max_pos:.0%}）"); counters[s]["buy_rejected_cash"] += 1
        return (not why), why

    def _fill(self, L, pend, s, o, t, i, mc, max_pos, trade_rows, dec_rows, counters, prices=None):
        r = {"state": None}; prices = prices or {s: o}
        if pend["action"] == "SELL":
            q = min(pend["qty"], L.qty(s))
            if pend["sleeve"] == "tactical" and self.model == "B_core_satellite":
                ids = [x.lot_id for x in L.lots.get(s, []) if x.strategy.startswith("tactical")]
                q = min(q, sum(x.qty for x in L.lots.get(s, []) if x.lot_id in ids))
                if q > 0:
                    trade_rows += self._rows(L.sell(s, q, o, t, pend["strategy"], ids), s, t, i, mc, r)
            elif q > 0:
                trade_rows += self._rows(L.sell(s, q, o, t, pend["strategy"], None), s, t, i, mc, r)
            return
        if pend.get("stop") is not None and o <= pend["stop"]:
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "price": o, "reasons": "开盘已低于止损，取消买入", "strategy": pend["strategy"]}); return
        equity = L.equity(prices)
        holding = L.qty(s)
        cap = max(0.0, max_pos * equity - holding * o) / o
        cash_cap = L.cash / (o * (1 + self.slip / 1e4) * (1 + self.fee / 1e4)) * 0.999
        step = float(self.c["spot"]["risk"]["qty_step"].get(s, 1e-6))
        q = float(np.floor(min(pend["qty"], cap, cash_cap) / step) * step)
        if q * o < 10:
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "price": o, "reasons": "成交时现金/上限不足，取消买入", "strategy": pend["strategy"]}); return
        try:
            L.buy(s, q, o, t, pend["strategy"], pend.get("stop"), 0.0, q * (o - pend["stop"]) if pend.get("stop") else 0.0)
        except SpotConstraintError as e:
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "price": o, "reasons": f"账本拒绝：{e}", "strategy": pend["strategy"]})

    def _rows(self, rec, s, t, i, mc, r) -> list[dict]:
        rows = []
        for cl in rec["closed_lots"]:
            pnl = (rec["price"] - cl["entry"]) * cl["qty"]; fee = rec["fee"] * (cl["qty"] / rec["qty"]) if rec["qty"] else 0.0
            risk = cl["risk_usd"] if cl["risk_usd"] else None
            rows.append({"symbol": s, "sleeve": cl["strategy"].split("|")[0], "strategy": cl["strategy"], "entry_ts": cl["entry_ts"], "exit_ts": str(t), "entry": cl["entry"], "exit": rec["price"], "qty": cl["qty"], "gross_pnl": pnl, "fee": fee, "net_pnl": pnl - fee,
                         "ret_pct": rec["price"] / cl["entry"] - 1, "r_multiple": (pnl - fee) / risk if risk else np.nan, "exit_reason": rec["reason"], "hold_days": (pd.Timestamp(t) - pd.Timestamp(cl["entry_ts"])).total_seconds() / 86400, "mae": cl["mae"], "mfe": cl["mfe"], "state_at_exit": r.get("state")})
        return rows


def sma200_benchmark(mc, start, end, initial: float, sma: int = 200, fee_bps: float = 0.0, slip_bps: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """简单趋势基准：日线收盘 > SMA(200) 持有现货，否则空仓；在日线收盘后的下一根 1h 开盘执行。返回 (equity, fills)。"""
    d1 = mc.bars["1d"]; c = d1["close"].astype(float); s = c.rolling(sma).mean()
    sig = (c > s).to_numpy(); idx1d = mc.htf["1d"]["idx"]
    L = SpotLedger(initial, fee_bps, slip_bps); rows = []
    ex = mc.ex; ts = ex["ts"]
    mask = np.ones(len(ex), bool)
    if start is not None:
        mask &= (ts >= pd.Timestamp(start, tz="UTC")).to_numpy()
    if end is not None:
        mask &= (ts <= pd.Timestamp(end, tz="UTC")).to_numpy()
    want_prev = None
    for i in np.flatnonzero(mask):
        j = int(idx1d[i]); o = float(mc.o[i]); cl = float(mc.cl[i])
        want = bool(sig[j]) if j >= 0 and np.isfinite(s.iloc[j]) else False
        if want_prev is not None and want_prev != (L.qty("X") > 0):
            if want_prev and L.qty("X") <= 0:
                q = L.cash / (o * (1 + slip_bps / 1e4) * (1 + fee_bps / 1e4)) * 0.999
                if q * o >= 10:
                    L.buy("X", q, o, ts.iloc[i], "sma200", 0.0, 0.0, 0.0)
            elif (not want_prev) and L.qty("X") > 0:
                L.sell("X", L.qty("X"), o, ts.iloc[i], "sma200_exit")
        want_prev = want
        rows.append({"ts": ts.iloc[i], "equity": L.equity({"X": cl}), "cash": L.cash, "exposure": L.exposure({"X": cl}), "qty": L.qty("X")})
    return pd.DataFrame(rows), pd.DataFrame(L.trade_log)
