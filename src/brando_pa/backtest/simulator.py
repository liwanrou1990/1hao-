"""逐根 K 线事件回测（执行周期），多标的共享一个组合账户。

时间顺序（每根执行 K 线 t）：
  (a) 上一根收盘产生的待执行信号 → 以本根开盘价 ± 滑点成交（next_open），成交前重算净盈亏比/仓位，不达标则取消并记录；
  (b) 用本根 OHLC 管理全部持仓：跳空越过止损/止盈按开盘价成交；同一根内止损与止盈都可能触发 → 按 same_bar_rule（默认止损优先，保守）；
      时间止损；资金费（跨越 00/08/16 UTC 结算点，按假设费率或数据）；之后才更新移动止损（避免同根先移后触）；
  (c) 本根收盘 → 信号引擎评估（只用 ≤ 本根收盘的信息）→ 组合风控检查 → 生成待执行信号给 t+1。
不存在：未收盘 K 线提前确认、跨周期穿越（由 MarketContext 保证）、重复信号（同策略同设置 ID 去重）、补仓/马丁。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import cfg, deep_update
from ..engine.context import MarketContext
from ..engine.signal_engine import SignalEngine, instrument_for
from ..risk.limits import RiskState, check_new_position
from ..strategies import make_strategies
from .execution import Position, PositionManager, hypothetical_outcome
from ..engine.signal_engine import regime_class4


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.DataFrame                   # ts, equity（每根执行 K 线收盘）
    signals_log: pd.DataFrame              # 每个候选信号：状态/拒绝原因
    status_counts: dict
    cancelled_entries: list[dict]
    config_used: dict
    exec_tf: str
    symbols: list[str]
    period: tuple[str, str]
    notes: list[str] = field(default_factory=list)
    limit_log: list[dict] = field(default_factory=list)
    strategy_counters: dict = field(default_factory=dict)
    order_events: list[dict] = field(default_factory=list)


class Backtester:
    def __init__(self, c: dict | None = None, exec_tf: str | None = None, strategies: list[str] | None = None, filters_enabled: bool = True,
                 params_override: dict | None = None, start: str | None = None, end: str | None = None, initial_equity: float | None = None,
                 contexts: dict[str, MarketContext] | None = None):
        self.c = deep_update(c or cfg(), params_override or {})
        self.exec_tf = exec_tf or self.c["timeframes"]["execution"]
        self.strategy_keys = strategies
        self.filters_enabled = filters_enabled
        self.start = pd.Timestamp(start, tz="UTC") if start else None
        self.end = pd.Timestamp(end, tz="UTC") if end else None
        self.initial_equity = float(initial_equity or self.c["backtest"]["initial_equity"])
        self.contexts = contexts or {}

    def _engine(self) -> SignalEngine:
        strats = make_strategies(self.c["strategies"], enabled_only=True)
        if self.strategy_keys is not None:
            strats = {k: v for k, v in strats.items() if k in self.strategy_keys}
        return SignalEngine(self.c, strategies_override=strats, filters_enabled=self.filters_enabled)

    def run(self, contexts: dict[str, MarketContext]) -> BacktestResult:
        self.contexts = contexts
        syms = list(contexts)
        c, costs, rc = self.c, self.c["costs"], self.c["risk"]
        bt = c["backtest"]
        pm = PositionManager(self.c, self.filters_enabled)
        engines = {s: self._engine() for s in syms}      # 每个标的独立的策略状态机
        # 统一时间轴
        all_ts = sorted(set().union(*[set(mc.ts.tolist()) for mc in contexts.values()]))
        if self.start is not None:
            all_ts = [t for t in all_ts if t >= self.start]
        if self.end is not None:
            all_ts = [t for t in all_ts if t <= self.end]
        pos_of = {s: {pd.Timestamp(t): i for i, t in enumerate(mc.ts.tolist())} for s, mc in contexts.items()}
        equity_cash = self.initial_equity
        positions: list[Position] = []
        pending: dict[str, dict] = {}
        trades: list[dict] = []; eq_rows: list[dict] = []; sig_rows: list[dict] = []; cancelled: list[dict] = []; limit_log: list[dict] = []; order_events: list[dict] = []
        def _ev(kind, sym, t_, **kw):
            order_events.append({"ts": str(t_), "symbol": sym, "event": kind} | kw)
        entry_mode = (c.get("execution_modes", {}) or {}).get("entry_mode", "market")
        lim_cfg = (c.get("execution_modes", {}) or {}).get("limit", {}) or {}
        mtf_mode = (c.get("mtf", {}) or {}).get("mode", "single")
        status_counts: dict[str, int] = {}
        rs = RiskState(equity=self.initial_equity)
        seen_setups: set[str] = set()
        bar_sec = contexts[syms[0]].bar_seconds
        for t in all_ts:
            t = pd.Timestamp(t)
            day = t.strftime("%Y-%m-%d")
            rs.roll_day(day)
            for s in syms:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = contexts[s]
                o, h, l, cl = float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), float(mc.cl[i])
                # (a) 待执行信号在本根开盘成交
                pend = pending.get(s)
                if pend is not None:
                    equity_now = equity_cash + sum(p.unrealized(float(contexts[p.symbol].cl[pos_of[p.symbol].get(t, p.entry_idx)])) for p in positions)
                    if pend.get("entry_mode") == "limit":
                        state, px = pm.limit_fill_check(pend, o, h, l, cl, i)
                        lp_ = float(pend["signal"]["limit_price"]); touched = (l <= lp_) if pend["signal"]["direction"] == "long" else (h >= lp_)
                        if touched and state != "fill":
                            _ev("order_touched_not_filled", s, t, setup_id=pend["signal"]["setup_id"], limit_price=lp_, low=l, high=h)
                        if state == "fill":
                            _ev("order_filled", s, t, setup_id=pend["signal"]["setup_id"], fill_price=px, order_type="limit")
                            pending.pop(s)
                            newp, dcash, canc = pm.fill_entry(pend, o, t, i, equity_now, bar_sec, fill_price=px, maker=bool(lim_cfg.get("maker_fill_uses_maker_fee", True)))
                            equity_cash += dcash
                            if newp is not None:
                                positions.append(newp)
                            if canc is not None:
                                cancelled.append(canc)
                            limit_log.append({"ts": str(t), "symbol": s, "strategy": pend["signal"]["strategy"], "direction": pend["signal"]["direction"], "limit_price": pend["signal"]["limit_price"], "status": "filled" if newp is not None else "filled_then_rejected", "wait_bars": i - pend["placed_idx"]})
                        elif state.startswith("cancel"):
                            pending.pop(s); _ev("order_cancelled", s, t, setup_id=pend["signal"]["setup_id"], reason=state)
                            hyp = hypothetical_outcome(pm, mc, pend, pend["placed_idx"], bar_sec, equity_now)
                            limit_log.append({"ts": str(t), "symbol": s, "strategy": pend["signal"]["strategy"], "direction": pend["signal"]["direction"], "limit_price": pend["signal"]["limit_price"], "status": state, "wait_bars": i - pend["placed_idx"],
                                              "hypothetical_market_net_pnl": hyp.get("net_pnl"), "hypothetical_market_r": hyp.get("r_multiple"), "hypothetical_exit": hyp.get("exit_reason", hyp.get("status"))})
                            cancelled.append({"ts": str(t), "symbol": s, "strategy": pend["signal"]["strategy"], "reason": f"限价未成交：{state}"})
                    else:
                        pending.pop(s)
                        newp, dcash, canc = pm.fill_entry(pend, o, t, i, equity_now, bar_sec)
                        equity_cash += dcash
                        if newp is not None:
                            positions.append(newp); _ev("order_filled", s, t, setup_id=pend["signal"]["setup_id"], fill_price=newp.entry_price, order_type="market")
                        if canc is not None:
                            cancelled.append(canc); _ev("order_cancelled", s, t, setup_id=pend["signal"]["setup_id"], reason=canc["reason"])
                # (b) 管理持仓
                still: list[Position] = []
                for p in positions:
                    if p.symbol != s:
                        still.append(p); continue
                    closed, pnl_cash = pm.manage(p, o, h, l, cl, t, i, trades)
                    equity_cash += pnl_cash
                    if closed:
                        _ev("position_exit", s, t, position_id=p.position_id, reason=trades[-1]["exit_reason"], exit_price=trades[-1]["exit_price"])
                        rs.day_realized_pnl += trades[-1]["net_pnl"]
                        rs.consecutive_losses = rs.consecutive_losses + 1 if trades[-1]["net_pnl"] < 0 else 0
                    else:
                        still.append(p)
                positions = still
            # 每根收盘：净值、信号
            unreal = 0.0
            for p in positions:
                j = pos_of[p.symbol].get(t)
                unreal += p.unrealized(float(contexts[p.symbol].cl[j])) if j is not None else 0.0
            equity = equity_cash + unreal
            rs.equity = equity
            eq_rows.append({"ts": t, "equity": equity, "cash": equity_cash, "n_pos": len(positions)})
            for s in syms:
                i = pos_of[s].get(t)
                if i is None:
                    continue
                mc = contexts[s]
                rep = engines[s].evaluate(mc, i, equity, detail=False)
                status_counts[rep.status] = status_counts.get(rep.status, 0) + 1
                for e in rep.candidates:
                    fails = [f"{ch['name']}" for ch in e.get("filter", {}).get("checks", []) if ch["status"] == "FAIL"]
                    sig_rows.append({"ts": t, "symbol": s, "strategy": e["strategy"], "direction": e["signal"]["direction"], "accepted": bool(e.get("sizing", {}).get("ok", False)) and not fails,
                                     "fails": ",".join(fails), "first_fail": fails[0] if fails else "", "net_rr": e.get("net_rr", e.get("net_rr_est")), "rr_gross": e["signal"]["rr_gross"], "status": rep.status,
                                     "regime4": e.get("regime4"), "stop_distance_pct": e.get("stop_distance_pct"), "stop_distance_atr": e.get("stop_distance_atr"), "cost_bps": e.get("cost_bps", e.get("cost_bps_est")),
                                     "cost_to_risk": e.get("cost_to_risk"), "cost_share_of_reward": e.get("cost_share_of_reward", e.get("cost_share_of_reward_est")), "zone_tfs": e.get("zone_tfs"),
                                     "sizing_ok": bool(e.get("sizing", {}).get("ok", False)) if e.get("sizing") else None, "portfolio_block": "",
                                     "ref_price": e["signal"]["ref_price"], "stop_price": e["signal"]["stop_price"], "take_profit": e["signal"]["take_profit"], "atr": e["signal"]["atr"],
                                     "structure_price": e["signal"].get("structure_price"), "micro_stop_price": e["signal"].get("micro_stop_price"),
                                     **{f"diag_{k}": v for k, v in (e["signal"].get("diag") or {}).items()}})
                if rep.status in ("LONG", "SHORT") and rep.chosen is not None:
                    sig = rep.chosen["signal"]
                    if sig["setup_id"] in seen_setups:
                        continue                                            # 订单去重：同一设置只执行一次
                    open_dicts = [{"symbol": p.symbol, "direction": p.direction, "risk_usd": p.risk_usd, "strategy": p.strategy} for p in positions]
                    ok, why = check_new_position(sig["direction"], s, rep.chosen["sizing"]["risk_usd"], open_dicts, rs, rc, unrealized_pnl=unreal)
                    if not ok:
                        if sig_rows:
                            sig_rows[-1]["fails"] = (sig_rows[-1]["fails"] + ",portfolio").strip(","); sig_rows[-1]["portfolio_block"] = "|".join(why); sig_rows[-1]["first_fail"] = sig_rows[-1]["first_fail"] or "portfolio"
                        status_counts["NO_TRADE_PORTFOLIO"] = status_counts.get("NO_TRADE_PORTFOLIO", 0) + 1
                        continue
                    seen_setups.add(sig["setup_id"])
                    if sig_rows:
                        sig_rows[-1]["order_created"] = True
                    pend_new = {"signal": sig, "instrument": rep.chosen["instrument"], "regime": rep.trend_1d.get("trend"), "regime4": rep.trend_1d.get("class4"), "trend_4h": rep.structure_4h.get("trend"), "signal_ts": t,
                                "entry_mode": entry_mode if sig.get("limit_price") is not None else "market", "placed_idx": i + 1, "expires_idx": i + 1 + int(lim_cfg.get("max_wait_bars", 8))}
                    if s in pending and pending[s].get("entry_mode") == "limit":
                        limit_log.append({"ts": str(t), "symbol": s, "strategy": pending[s]["signal"]["strategy"], "direction": pending[s]["signal"]["direction"], "limit_price": pending[s]["signal"]["limit_price"], "status": "cancel:replaced_by_new_signal", "wait_bars": i - pending[s]["placed_idx"]})
                    pending[s] = pend_new
                    _ev("signal_generated", s, t, setup_id=sig["setup_id"], strategy=sig["strategy"], direction=sig["direction"], ref_price=sig["ref_price"], stop=sig["stop_price"], tp=sig["take_profit"])
                    _ev("order_submitted", s, t, setup_id=sig["setup_id"], order_type=pend_new["entry_mode"], limit_price=sig.get("limit_price") if pend_new["entry_mode"] == "limit" else None, expires_idx=pend_new["expires_idx"])
            if rs.day_start_equity and rs.halted_until_day is None:
                day_loss = -(rs.day_realized_pnl + unreal) / rs.day_start_equity
                if day_loss >= float(rc["daily_loss_limit"]):
                    rs.halted_until_day = (t + pd.Timedelta(days=1)).strftime("%Y-%m-%d"); rs.halt_reasons = [f"{day} 亏损 {day_loss:.2%}"]
        # 期末强平未平仓（按最后收盘价，标注 end_of_test）
        for p in positions:
            mc = contexts[p.symbol]; j = len(mc.cl) - 1
            equity_cash += pm.close(p, float(mc.cl[j]), mc.ts.iloc[j], j, "end_of_test", trades)
        trades_df = pd.DataFrame(trades)
        if not trades_df.empty:
            reg_map = {"strong_up": "上涨趋势", "up": "上涨趋势", "strong_down": "下跌趋势", "down": "下跌趋势", "range": "横盘震荡", "undefined": "横盘震荡"}
            trades_df["regime4"] = trades_df["regime"].map(reg_map).fillna("横盘震荡")
            trades_df["year"] = pd.to_datetime(trades_df["entry_ts"]).dt.year
            trades_df["mtf_mode"] = mtf_mode; trades_df["exec_tf"] = self.exec_tf
        eq = pd.DataFrame(eq_rows)
        period = (str(all_ts[0])[:19] if all_ts else "", str(all_ts[-1])[:19] if all_ts else "")
        notes = [f"执行周期 {self.exec_tf}；成交规则 {bt['entry_fill']}；同根止盈止损规则 {bt['same_bar_rule']}；资金费假设 {costs['funding']}（无历史资金费率数据）",
                 f"过滤模块 {'开启' if self.filters_enabled else '关闭'}；策略 {self.strategy_keys or '全部'}"]
        notes.append(f"入场模式 {entry_mode}；多周期模式 {mtf_mode}；退出配置 {c.get('exits')}")
        counters = {s: {k: dict(v.counters) for k, v in engines[s].strategies.items()} for s in syms}
        return BacktestResult(trades_df, eq, pd.DataFrame(sig_rows), status_counts, cancelled, {"strategies": self.c["strategies"], "filters": self.c["filters"], "risk": self.c["risk"], "costs": costs, "backtest": bt, "exits": c.get("exits"), "execution_modes": c.get("execution_modes"), "mtf": c.get("mtf")},
                              self.exec_tf, syms, period, notes, limit_log, counters, order_events)
