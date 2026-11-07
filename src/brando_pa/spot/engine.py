"""纯现货回测引擎：多资产共享一个现货账本；决策只有 BUY / HOLD / SELL。

每根执行 K 线 t（按时间轴对全部资产）：
  (a) 上一根收盘决定的 BUY/SELL 在本根开盘成交（现金/持仓约束在成交时再次检查）；
  (b) 持仓管理（本根 OHLC）：硬止损（跳空按开盘；同根止损优先）→ 保本/摆动低点跟踪止损（只上移，下一根生效）；
  (c) 本根收盘：高周期背景（日线环境、4h 结构、高周期区域）+ 本地结构 → 策略 → 风控（现金、持仓上限、回撤暂停）→ 决策与理由。
全部持仓按最后收盘价计价；逐根记录现金、持仓数量、权益、已实现/未实现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis.levels import Zone
from ..backtest.runner import load_contexts
from ..config import cfg, deep_update
from ..engine.context import MarketContext
from ..engine.signal_engine import regime_class4
from .ledger import SpotConstraintError, SpotLedger
from .strategies import BreakdownReclaimBuy, BreakoutRetestBuy, ResistanceRejectionSell, SpotBar
from .structure import all_levels, last_confirmed_swing_low, local_levels

REG4 = {"strong_up": "强势上涨", "up": "上涨趋势", "strong_down": "强势下跌", "down": "下跌趋势", "range": "横盘震荡", "undefined": "横盘震荡"}


@dataclass
class SpotResult:
    trades: pd.DataFrame            # 每次卖出的平仓记录（按批次拆分）
    fills: pd.DataFrame             # 全部买卖成交
    equity: pd.DataFrame            # 逐根：ts, equity, cash, exposure, holdings
    decisions: pd.DataFrame         # 逐根决策：BUY/HOLD/SELL + 理由（含被风控拒绝的 BUY）
    final: dict
    config_used: dict
    symbols: list
    exec_tf: str
    period: tuple
    counters: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


class SpotBacktester:
    def __init__(self, c: dict | None = None, symbols: list[str] | None = None, exec_tf: str | None = None, start: str | None = None, end: str | None = None,
                 research_costs: bool | None = None, override: dict | None = None, initial_cash: float | None = None):
        self.c = deep_update(c or cfg(), override or {})
        sc = self.c["spot"]
        self.symbols = symbols or sc["symbols"]; self.exec_tf = exec_tf or sc["exec_tf"]
        self.start = pd.Timestamp(start, tz="UTC") if start else None; self.end = pd.Timestamp(end, tz="UTC") if end else None
        rm = sc["costs"]["research_mode"] if research_costs is None else research_costs
        sens = sc["costs"]["sensitivity"]
        self.fee_bps = 0.0 if rm else float(sens["fee_bps"]); self.slip_bps = 0.0 if rm else float(sens["slippage_bps"])
        self.initial_cash = float(initial_cash or sc["initial_cash_usdt"])
        self.dual = len(self.symbols) > 1

    def run(self, contexts: dict[str, MarketContext] | None = None) -> SpotResult:
        c, sc = self.c, self.c["spot"]
        ctxs = contexts or load_contexts(self.symbols, self.exec_tf, c)
        ctxs = {s: ctxs[s] for s in self.symbols}
        L = SpotLedger(self.initial_cash, self.fee_bps, self.slip_bps)
        strat_cfg = sc["strategies"]
        strats = {s: {"A": BreakoutRetestBuy(strat_cfg["A_breakout_retest_buy"]), "B": BreakdownReclaimBuy(strat_cfg["B_breakdown_reclaim_buy"]), "C": ResistanceRejectionSell(strat_cfg["C_resistance_rejection_sell"])} for s in self.symbols}
        enabled = {k: bool(strat_cfg[n]["enabled"]) for k, n in (("A", "A_breakout_retest_buy"), ("B", "B_breakdown_reclaim_buy"), ("C", "C_resistance_rejection_sell"))}
        lc, ex, rk, hc = sc["local_structure"], sc["exits"], sc["risk"], sc["htf_context"]
        max_pos = float(rk["max_position_pct_dual"] if self.dual else rk["max_position_pct"])
        all_ts = sorted(set().union(*[set(mc.ts.tolist()) for mc in ctxs.values()]))
        if self.start is not None:
            all_ts = [t for t in all_ts if t >= self.start]
        if self.end is not None:
            all_ts = [t for t in all_ts if t <= self.end]
        pos_of = {s: {pd.Timestamp(t): i for i, t in enumerate(mc.ts.tolist())} for s, mc in ctxs.items()}
        pending: dict[str, dict] = {}
        eq_rows, dec_rows, trade_rows = [], [], []
        peak = self.initial_cash; halted = False
        last_px: dict[str, float] = {}
        for t in all_ts:
            t = pd.Timestamp(t)
            prices_open = {}
            for s in self.symbols:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = ctxs[s]; o, h, l, cl = float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), float(mc.cl[i])
                prices_open[s] = o
                # (a) 成交上一根的决策
                pend = pending.pop(s, None)
                if pend is not None:
                    self._fill(L, pend, s, o, t, rk, max_pos, prices_open | last_px, dec_rows, i, mc, trade_rows)
                # (b) 持仓管理
                for lot in list(L.lots.get(s, [])):
                    lot.mae = max(lot.mae, lot.price - l); lot.mfe = max(lot.mfe, h - lot.price)
                    if ex.get("hard_stop", True) and l <= lot.stop:
                        px = o if o <= lot.stop else lot.stop
                        rec = L.sell(s, lot.qty, px, t, "stop_gap" if o <= lot.stop else ("trail_stop" if lot.trailed else "stop"), [lot.lot_id])
                        trade_rows += self._trade_rows(rec, s, t, i, mc)
                        continue
                    risk = lot.price - lot.init_stop
                    favor = h - lot.price
                    be = ex.get("breakeven_after_r")
                    if be is not None and risk > 0 and favor >= float(be) * risk and lot.stop < lot.price:
                        lot.stop = lot.price; lot.trailed = True
                    tr = ex.get("trail_to_swing_low_after_r")
                    if tr is not None and risk > 0 and favor >= float(tr) * risk:
                        swl = last_confirmed_swing_low(mc.exec_swings, i)
                        if swl is not None and swl > lot.stop:
                            lot.stop = swl; lot.trailed = True
                last_px[s] = cl
            # 收盘计价
            prices = {s: last_px.get(s, np.nan) for s in self.symbols}
            equity = L.equity(prices); peak = max(peak, equity)
            dd = equity / peak - 1
            if dd <= -float(rk["max_drawdown_halt"]):
                halted = True
            elif halted and dd > -float(rk["max_drawdown_halt"]) / 2:
                halted = False
            eq_rows.append({"ts": t, "equity": equity, "cash": L.cash, "exposure": L.exposure(prices), **{f"qty_{s}": L.qty(s) for s in self.symbols}, "drawdown": dd, "halted": halted})
            # (c) 决策
            for s in self.symbols:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = ctxs[s]
                a = float(mc.atr_ex[i])
                if i < 1 or not np.isfinite(a) or a <= 0 or any(mc.htf[tf]["idx"][i] < 0 for tf in mc.level_tfs):
                    continue
                n_ok = int(np.searchsorted(mc._exec_swing_conf, i, side="right"))
                swings = mc.exec_swings[:n_ok]
                res, sup = local_levels(swings, i, mc.cl, a, lc); alv = all_levels(swings, i, mc.cl, a, lc)
                zones, _ = mc.zones_at(i); px = float(mc.cl[i])
                htf_res = [z for z in zones if z.role(px) == "resistance" and any(tf in hc["zone_tfs"] for tf in z.tfs)]
                htf_sup = [z for z in zones if z.role(px) == "support" and any(tf in hc["zone_tfs"] for tf in z.tfs)]
                regime = mc.regime_at(i); reg4 = REG4.get(regime.get("trend", "range"), "横盘震荡")
                if regime.get("highvol"):
                    reg4 = "高波动异常"
                st = mc.structure_at(i)
                holding = L.qty(s)
                bar = SpotBar(s, i, t, float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), px, float(mc.cl[i - 1]), a, res, sup, alv, htf_res, regime, holding)
                sigs = []
                for key in ("A", "B", "C"):
                    if not enabled[key]:
                        continue
                    sg = strats[s][key].on_bar(bar)
                    if sg is not None:
                        sigs.append(sg)
                buys = [g for g in sigs if g.action == "BUY"]; sells = [g for g in sigs if g.action == "SELL"]
                decision = "HOLD"; reasons = []; chosen = None
                d1_atr = float(mc.htf["1d"]["atr"][int(mc.htf["1d"]["idx"][i])]) if "1d" in mc.htf else np.nan
                near_sup = any(abs(px - z.hi) <= float(hc["near_support_atr_1d"]) * d1_atr for z in htf_sup) if np.isfinite(d1_atr) else False
                ctx_info = {"regime4": reg4, "trend_4h": st.trend if st else "undefined", "near_htf_support": near_sup, "n_htf_res": len(htf_res), "local_res": [round(l.price, 2) for l in res], "local_sup": [round(l.price, 2) for l in sup]}
                if sells and holding > 0:
                    g = sells[0]; decision = "SELL"; chosen = g
                    reasons = [f"策略 {g.strategy}：" + "；".join(g.reasons)]
                    pending[s] = {"action": "SELL", "signal": g, "qty": holding * g.sell_fraction, "signal_ts": t}
                elif buys:
                    g = max(buys, key=lambda x: (x.ref_price - x.stop) if x.stop else 0)
                    g = buys[0] if len(buys) == 1 else g
                    ok, why, qty, risk_usd = self._size(L, s, g, equity, prices, rk, max_pos, halted, reg4, hc, holding)
                    if ok:
                        decision = "BUY"; chosen = g
                        reasons = [f"策略 {g.strategy}：" + "；".join(g.reasons), f"数量 {qty:.6g}（风险 {risk_usd:.2f} USDT = 净值 {float(rk['risk_per_trade']):.1%}，止损距离 {(g.ref_price - g.stop) / a:.2f} ATR）", f"背景：日线 {reg4}，4h {ctx_info['trend_4h']}，{'靠近高周期支撑' if near_sup else '不在高周期支撑附近'}"]
                        pending[s] = {"action": "BUY", "signal": g, "qty": qty, "risk_usd": risk_usd, "signal_ts": t}
                    else:
                        decision = "HOLD"; reasons = [f"策略 {g.strategy} 给出买入条件，但被拒绝：" + "；".join(why)]
                        for key in ("A", "B"):
                            strats[s][key].counters["buy_rejected"] = strats[s][key].counters.get("buy_rejected", 0) + (1 if g.strategy.startswith(key) else 0)
                else:
                    reasons = [("持有现货，等待结构失效/阻力拒绝/跟踪止损" if holding > 0 else "持有 USDT，等待突破回踩或跌破收复的买入条件")]
                dec_rows.append({"ts": t, "symbol": s, "decision": decision, "strategy": chosen.strategy if chosen else "", "price": px, "stop": chosen.stop if chosen else None, "holding_qty": holding, "cash": L.cash, "equity": equity,
                                 "reasons": " | ".join(reasons), **ctx_info, **({f"diag_{k}": v for k, v in chosen.diag.items()} if chosen else {})})
        # 期末按最后收盘计价（不强平），记录未实现
        prices = {s: last_px.get(s, np.nan) for s in self.symbols}
        final = L.snapshot(prices) | {"initial_cash": self.initial_cash, "last_prices": prices, "peak_equity": peak}
        trades = pd.DataFrame(trade_rows)
        fills = pd.DataFrame([{k: v for k, v in r.items() if k != "closed_lots"} for r in L.trade_log])
        eq = pd.DataFrame(eq_rows)
        period = (str(all_ts[0])[:19] if all_ts else "", str(all_ts[-1])[:19] if all_ts else "")
        counters = {s: {k: dict(v.counters) for k, v in strats[s].items()} for s in self.symbols}
        notes = [f"费用 {self.fee_bps} bps / 滑点 {self.slip_bps} bps（{'理想研究模式' if self.fee_bps == 0 and self.slip_bps == 0 else '真实成本敏感性'}）", "只做多、无杠杆、无做空；决策 BUY/HOLD/SELL；下一根开盘成交；同根止损优先；止损只上移"]
        return SpotResult(trades, fills, eq, pd.DataFrame(dec_rows), final, {"spot": sc}, self.symbols, self.exec_tf, period, counters, notes)

    # ---- 仓位 / 风控 ----
    def _size(self, L: SpotLedger, s: str, g, equity: float, prices: dict, rk: dict, max_pos: float, halted: bool, reg4: str, hc: dict, holding: float):
        why = []
        if g.stop is None or g.stop >= g.ref_price:
            why.append("止损无效")
        if reg4 in (hc.get("block_buy_regimes") or []):
            why.append(f"日线环境 {reg4} 不买入（背景过滤）")
        if halted:
            why.append(f"账户回撤超过 {float(rk['max_drawdown_halt']):.0%}，暂停新买入")
        if why:
            return False, why, 0.0, 0.0
        risk_usd = equity * float(rk["risk_per_trade"])
        dist = g.ref_price - g.stop
        qty = risk_usd / dist
        cap_pos = max(0.0, max_pos * equity - holding * g.ref_price) / g.ref_price
        cap_cash = L.cash / (g.ref_price * (1 + self.slip_bps / 1e4) * (1 + self.fee_bps / 1e4)) * 0.999
        qty = min(qty, cap_pos, cap_cash)
        step = float(rk["qty_step"].get(s, 1e-6)); qty = np.floor(qty / step) * step
        if qty * g.ref_price < float(rk["min_order_usdt"]):
            return False, [f"可买数量 {qty:.6g} 名义额不足 {rk['min_order_usdt']} USDT（现金 {L.cash:.2f}，持仓上限 {max_pos:.0%}）"], 0.0, 0.0
        return True, [], float(qty), float(qty * dist)

    def _fill(self, L: SpotLedger, pend: dict, s: str, o: float, t, rk: dict, max_pos: float, prices: dict, dec_rows: list, i: int = 0, mc=None, trade_rows: list | None = None):
        g = pend["signal"]
        if pend["action"] == "SELL":
            q = min(pend["qty"], L.qty(s))
            if q > 0:
                rec = L.sell(s, q, o, t, f"{g.strategy}", None)
                if trade_rows is not None and mc is not None:
                    trade_rows += self._trade_rows(rec, s, t, i, mc)          # 策略 C 卖出同样计入平仓记录（按 FIFO 批次拆分）
            return
        if o <= g.stop:
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "strategy": g.strategy, "price": o, "reasons": "开盘价已低于止损，取消买入"}); return
        qty = pend["qty"]
        cap_cash = L.cash / (o * (1 + self.slip_bps / 1e4) * (1 + self.fee_bps / 1e4)) * 0.999
        step = float(rk["qty_step"].get(s, 1e-6)); qty = float(np.floor(min(qty, cap_cash) / step) * step)
        if qty * o < float(rk["min_order_usdt"]):
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "strategy": g.strategy, "price": o, "reasons": "成交时现金不足，取消买入"}); return
        try:
            L.buy(s, qty, o, t, g.strategy, g.stop, 0.0, qty * (o - g.stop))
        except SpotConstraintError as e:
            dec_rows.append({"ts": t, "symbol": s, "decision": "HOLD", "strategy": g.strategy, "price": o, "reasons": f"买入被账本拒绝：{e}"})

    def _trade_rows(self, rec: dict, s: str, t, i: int, mc) -> list[dict]:
        rows = []
        for cl in rec["closed_lots"]:
            pnl = (rec["price"] - cl["entry"]) * cl["qty"]
            fee_share = rec["fee"] * (cl["qty"] / rec["qty"]) if rec["qty"] else 0.0
            risk = cl["risk_usd"] if cl["risk_usd"] else (cl["entry"] - cl["init_stop"]) * cl["qty"]
            rows.append({"symbol": s, "strategy": cl["strategy"], "entry_ts": cl["entry_ts"], "exit_ts": str(t), "entry": cl["entry"], "exit": rec["price"], "qty": cl["qty"], "gross_pnl": pnl, "fee": fee_share, "net_pnl": pnl - fee_share,
                         "r_multiple": (pnl - fee_share) / risk if risk else np.nan, "ret_pct": rec["price"] / cl["entry"] - 1, "exit_reason": rec["reason"], "init_stop": cl["init_stop"], "mae": cl["mae"], "mfe": cl["mfe"],
                         "regime4": REG4.get(mc.regime_at(i).get("trend", "range"), "横盘震荡") if not mc.regime_at(i).get("highvol") else "高波动异常"})
        return rows


# ---------------- 基准 ----------------
def buy_and_hold(ctxs: dict, symbols: list[str], start, end, initial_cash: float, weights: dict | None = None, fee_bps: float = 0.0) -> pd.DataFrame:
    """期初第一根开盘按权重买入、持有到期末按收盘计价；逐根权益。"""
    weights = weights or {s: 1.0 / len(symbols) for s in symbols}
    frames = []
    for s in symbols:
        df = ctxs[s].ex[["ts", "open", "close"]]
        if start is not None:
            df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
        if end is not None:
            df = df[df["ts"] <= pd.Timestamp(end, tz="UTC")]
        p0 = float(df["open"].iloc[0]); cash = initial_cash * weights[s]; qty = cash * (1 - fee_bps / 1e4) / p0
        frames.append(pd.DataFrame({"ts": pd.to_datetime(df["ts"], utc=True).to_numpy(), f"v_{s}": qty * df["close"].to_numpy()}))
        frames[-1]["ts"] = pd.to_datetime(frames[-1]["ts"], utc=True)
    out = frames[0]
    for f_ in frames[1:]:
        out = out.merge(f_, on="ts", how="outer")
    out = out.sort_values("ts").ffill()
    out["equity"] = out[[c for c in out.columns if c.startswith("v_")]].sum(axis=1)
    return out[["ts", "equity"]]
