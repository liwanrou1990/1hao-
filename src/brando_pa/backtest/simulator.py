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
from .execution import Position, PositionManager


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
        trades: list[dict] = []; eq_rows: list[dict] = []; sig_rows: list[dict] = []; cancelled: list[dict] = []
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
                pend = pending.pop(s, None)
                if pend is not None:
                    equity_now = equity_cash + sum(p.unrealized(float(contexts[p.symbol].cl[pos_of[p.symbol].get(t, p.entry_idx)])) for p in positions)
                    newp, dcash, canc = pm.fill_entry(pend, o, t, i, equity_now, bar_sec)
                    equity_cash += dcash
                    if newp is not None:
                        positions.append(newp)
                    if canc is not None:
                        cancelled.append(canc)
                # (b) 管理持仓
                still: list[Position] = []
                for p in positions:
                    if p.symbol != s:
                        still.append(p); continue
                    closed, pnl_cash = pm.manage(p, o, h, l, cl, t, i, trades)
                    if closed:
                        equity_cash += pnl_cash
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
                rep = engines[s].evaluate(mc, i, equity)
                status_counts[rep.status] = status_counts.get(rep.status, 0) + 1
                for e in rep.candidates:
                    fails = [f"{ch['name']}" for ch in e.get("filter", {}).get("checks", []) if ch["status"] == "FAIL"]
                    sig_rows.append({"ts": t, "symbol": s, "strategy": e["strategy"], "direction": e["signal"]["direction"], "accepted": bool(e.get("sizing", {}).get("ok", False)) and not fails,
                                     "fails": ",".join(fails), "net_rr": e.get("net_rr"), "rr_gross": e["signal"]["rr_gross"], "status": rep.status})
                if rep.status in ("LONG", "SHORT") and rep.chosen is not None:
                    sig = rep.chosen["signal"]
                    if sig["setup_id"] in seen_setups:
                        continue                                            # 订单去重：同一设置只执行一次
                    open_dicts = [{"symbol": p.symbol, "direction": p.direction, "risk_usd": p.risk_usd, "strategy": p.strategy} for p in positions]
                    ok, why = check_new_position(sig["direction"], s, rep.chosen["sizing"]["risk_usd"], open_dicts, rs, rc, unrealized_pnl=unreal)
                    if not ok:
                        sig_rows[-1]["fails"] = (sig_rows[-1]["fails"] + ",portfolio:" + "|".join(why)).strip(",") if sig_rows else ""
                        status_counts["NO_TRADE_PORTFOLIO"] = status_counts.get("NO_TRADE_PORTFOLIO", 0) + 1
                        continue
                    seen_setups.add(sig["setup_id"])
                    pending[s] = {"signal": sig, "instrument": rep.chosen["instrument"], "regime": rep.trend_1d.get("trend"), "trend_4h": rep.structure_4h.get("trend"), "signal_ts": t}
            if rs.day_start_equity and rs.halted_until_day is None:
                day_loss = -(rs.day_realized_pnl + unreal) / rs.day_start_equity
                if day_loss >= float(rc["daily_loss_limit"]):
                    rs.halted_until_day = (t + pd.Timedelta(days=1)).strftime("%Y-%m-%d"); rs.halt_reasons = [f"{day} 亏损 {day_loss:.2%}"]
        # 期末强平未平仓（按最后收盘价，标注 end_of_test）
        for p in positions:
            mc = contexts[p.symbol]; j = len(mc.cl) - 1
            equity_cash += pm.close(p, float(mc.cl[j]), mc.ts.iloc[j], j, "end_of_test", trades)
        trades_df = pd.DataFrame(trades)
        eq = pd.DataFrame(eq_rows)
        period = (str(all_ts[0])[:19] if all_ts else "", str(all_ts[-1])[:19] if all_ts else "")
        notes = [f"执行周期 {self.exec_tf}；成交规则 {bt['entry_fill']}；同根止盈止损规则 {bt['same_bar_rule']}；资金费假设 {costs['funding']}（无历史资金费率数据）",
                 f"过滤模块 {'开启' if self.filters_enabled else '关闭'}；策略 {self.strategy_keys or '全部'}"]
        return BacktestResult(trades_df, eq, pd.DataFrame(sig_rows), status_counts, cancelled, {"strategies": self.c["strategies"], "filters": self.c["filters"], "risk": self.c["risk"], "costs": costs, "backtest": bt},
                              self.exec_tf, syms, period, notes)
