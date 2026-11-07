"""模拟盘运行器：行情获取 → 策略决策 → 风险管理 → 订单执行（PaperBroker）→ 持仓同步 → 日志，各环节解耦。

* run_once：锁 → （可选）刷新 K 线（重试/退避/限速）→ 构建上下文 → 逐根处理自上次以来新收盘的执行 K 线（与回测同一套 PositionManager 规则）
            → 最新 K 线上评估信号 → 组合风控 → 生成待执行（下一根开盘成交）→ 保存状态 → 告警。
* loop：固定间隔循环；异常只记录并重试，不退出。
* replay：用历史 K 线按时间推进调用同一 process 路径（模拟时钟），用于在没有实时数据的环境里验证全流程、状态恢复与订单去重。
* 数据过期（最新 K 线落后 > N 个周期）→ data_ok=False → 禁止新开仓，持仓照常按新 K 线管理。
* 绝不下真实单：PaperBroker 记账；LiveBroker 永远拒绝。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from ..backtest.execution import PositionManager
from ..backtest.runner import load_contexts
from ..config import cfg, report_dir, resolve
from ..data.ingest import run_ingest
from ..data.sources import bars_are_stale
from ..engine.signal_engine import SignalEngine
from ..risk.limits import RiskState, check_new_position
from .broker import Order, PaperBroker, make_client_order_id
from .state import PaperState


class RateLimiter:
    def __init__(self, rps: float):
        self.min_gap = 1.0 / max(rps, 0.01); self.last = 0.0

    def wait(self) -> None:
        gap = self.min_gap - (time.monotonic() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()


class PaperRunner:
    def __init__(self, symbols: list[str] | None = None, exec_tf: str | None = None, equity: float | None = None, c: dict | None = None, state_path: Path | None = None, refresh_data: bool = True):
        self.c = c or cfg()
        pc = self.c["paper"]
        self.symbols = symbols or self.c["symbols"]
        self.exec_tf = exec_tf or self.c["timeframes"]["execution"]
        self.state = PaperState(state_path or resolve(pc["state_file"]), float(equity or self.c["risk"]["account_equity_usdt"]))
        self.trade_log = resolve(pc["log_file"]); self.event_log = self.trade_log.parent / "events.jsonl"
        self.lock_file = resolve(pc["lock_file"]); self.refresh_data = refresh_data
        self.pm = PositionManager(self.c, True)
        self.broker = PaperBroker(self.c["costs"], set(self.state.data["order_ids"]))
        self.rl = RateLimiter(float(pc.get("rate_limit_rps", 5)))
        self.engines = {s: SignalEngine(self.c) for s in self.symbols}
        self.contexts = None

    # ---------- 基础设施 ----------
    def _lock(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd); return True
        except FileExistsError:
            try:
                age = time.time() - self.lock_file.stat().st_mtime
                if age > 2 * float(self.c["paper"]["poll_interval_s"]) + 600:
                    self.lock_file.unlink(); return self._lock()
            except FileNotFoundError:
                return self._lock()
            return False

    def _unlock(self) -> None:
        try:
            self.lock_file.unlink()
        except FileNotFoundError:
            pass

    def _event(self, kind: str, **kw) -> None:
        ev = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind} | kw
        self.state.append_event(self.event_log, ev)

    def _alert(self, text: str) -> None:
        self._event("alert", text=text)
        url = os.environ.get(self.c["paper"].get("alert_webhook_env", "BRANDO_ALERT_WEBHOOK"), "")
        if url:
            try:
                httpx.post(url, json={"text": f"[brando_pa paper] {text}"}, timeout=8)
            except Exception as e:  # noqa: BLE001
                self._event("alert_failed", error=type(e).__name__)

    def _with_retries(self, fn, what: str):
        pc = self.c["paper"]; last = None
        for k in range(int(pc["max_retries"])):
            try:
                self.rl.wait(); return fn()
            except Exception as e:  # noqa: BLE001
                last = e; self._event("retry", what=what, attempt=k + 1, error=f"{type(e).__name__}: {str(e)[:120]}")
                time.sleep(float(pc["retry_backoff_s"]) * (2 ** k))
        raise RuntimeError(f"{what} 重试 {pc['max_retries']} 次仍失败：{type(last).__name__}")

    # ---------- 核心处理 ----------
    def _risk_state(self, equity: float) -> RiskState:
        r = self.state.data["risk"]
        return RiskState(equity=equity, day=r["day"], day_start_equity=r["day_start_equity"], day_realized_pnl=r["day_realized_pnl"], halted_until_day=r["halted_until_day"], halt_reasons=list(r["halt_reasons"]))

    def _save_risk(self, rs: RiskState) -> None:
        self.state.data["risk"] = {"day": rs.day, "day_start_equity": rs.day_start_equity, "day_realized_pnl": rs.day_realized_pnl, "halted_until_day": rs.halted_until_day, "halt_reasons": rs.halt_reasons}

    def process_bar(self, symbol: str, i: int, now: pd.Timestamp | None = None) -> dict:
        """处理 symbol 的第 i 根（已收盘）执行 K 线：成交待执行 → 管理持仓 → 评估信号 → 生成待执行。"""
        mc = self.contexts[symbol]; st = self.state.data
        t = mc.ts.iloc[i]; o, h, l, cl = float(mc.o[i]), float(mc.h[i]), float(mc.l[i]), float(mc.cl[i])
        positions = self.state.positions
        equity = st["cash"] + sum(p.unrealized(self._last_price(p.symbol, t)) for p in positions)
        rs = self._risk_state(equity); rs.roll_day(t.strftime("%Y-%m-%d"))
        out = {"symbol": symbol, "ts": str(t), "fills": [], "closes": [], "status": None}
        # (a) 待执行 → 本根开盘成交（通过 PaperBroker 下单，订单号确定性去重）
        pend = st["pending"].pop(symbol, None)
        if pend is not None:
            sig = pend["signal"]
            coid = make_client_order_id("entry", sig["setup_id"], str(t))
            if self.broker.seen(coid):
                self._event("duplicate_entry_blocked", setup_id=sig["setup_id"], ts=str(t))
            else:
                newp, dcash, canc = self.pm.fill_entry(pend, o, t, i, equity, mc.bar_seconds)
                if newp is not None:
                    order = self.broker.place_order(Order(coid, symbol, "buy" if newp.direction == "long" else "sell", newp.qty, newp.instrument, o))
                    st["cash"] += dcash; positions.append(newp); st["order_ids"].append(coid)
                    out["fills"].append(order.to_dict()); self._event("entry_filled", order=order.to_dict(), position=newp.to_dict())
                elif canc:
                    self._event("entry_cancelled", **canc)
        # (b) 管理持仓
        still = []; trades: list[dict] = []
        for p in positions:
            if p.symbol != symbol:
                still.append(p); continue
            closed, dcash = self.pm.manage(p, o, h, l, cl, t, i, trades)
            if closed:
                st["cash"] += dcash; tr = trades[-1]
                coid = make_client_order_id("exit", p.position_id, tr["exit_reason"], str(t))
                self.broker.place_order(Order(coid, symbol, "sell" if p.direction == "long" else "buy", p.qty, p.instrument, tr["exit_price"], reduce_only=True)); st["order_ids"].append(coid)
                self.state.append_trade(self.trade_log, tr); rs.day_realized_pnl += tr["net_pnl"]
                out["closes"].append({k: tr[k] for k in ("strategy", "direction", "exit_reason", "net_pnl", "r_multiple")})
                self._event("position_closed", trade=tr)
            else:
                still.append(p)
        positions = still
        unreal = sum(p.unrealized(self._last_price(p.symbol, t)) for p in positions)
        equity = st["cash"] + unreal; rs.equity = equity
        # (c) 信号
        stale, why = bars_are_stale(mc.ex.iloc[: i + 1], self.exec_tf, now=now) if now is not None else (False, "回放模式")
        rep = self.engines[symbol].evaluate(mc, i, equity, data_ok=not stale)
        out["status"], out["reasons"] = rep.status, rep.reasons[:4]
        if out["closes"] and rep.status not in ("LONG", "SHORT"):
            out["status"] = "EXIT"                                   # 本根发生了持仓退出（止损/止盈/时间/保本），且无新开仓
            out["reasons"] = [f"{c_['strategy']} {c_['direction']} 退出：{c_['exit_reason']}，净 {c_['net_pnl']:.2f}（{c_['r_multiple']:.2f}R）" for c_ in out["closes"]] + rep.reasons[:2]
        if rep.status in ("LONG", "SHORT") and rep.chosen is not None:
            sig = rep.chosen["signal"]
            if sig["setup_id"] in set(st["seen_setups"]):
                self._event("duplicate_setup_blocked", setup_id=sig["setup_id"])
            else:
                open_dicts = [{"symbol": p.symbol, "direction": p.direction, "risk_usd": p.risk_usd, "strategy": p.strategy} for p in positions]
                ok, whyr = check_new_position(sig["direction"], symbol, rep.chosen["sizing"]["risk_usd"], open_dicts, rs, self.c["risk"], unrealized_pnl=unreal, data_ok=not stale)
                if ok:
                    st["pending"][symbol] = {"signal": sig, "instrument": rep.chosen["instrument"], "regime": rep.trend_1d.get("trend"), "trend_4h": rep.structure_4h.get("trend"), "signal_ts": str(t)}
                    st["seen_setups"].append(sig["setup_id"]); out["pending"] = {"strategy": sig["strategy"], "direction": sig["direction"], "stop": sig["stop_price"], "tp": sig["take_profit"]}
                    self._event("signal_accepted", symbol=symbol, strategy=sig["strategy"], direction=sig["direction"], reasons=rep.reasons)
                else:
                    out["status"] = "NO_TRADE"; out["reasons"] = whyr + rep.reasons[:2]
                    self._event("signal_blocked_by_portfolio", symbol=symbol, reasons=whyr)
        if rs.day_start_equity and rs.halted_until_day is None:
            day_loss = -(rs.day_realized_pnl + unreal) / rs.day_start_equity
            if day_loss >= float(self.c["risk"]["daily_loss_limit"]):
                rs.halted_until_day = (t + pd.Timedelta(days=1)).strftime("%Y-%m-%d"); rs.halt_reasons = [f"{rs.day} 亏损 {day_loss:.2%}"]
                self._alert(f"单日亏损 {day_loss:.2%} 触发暂停新开仓至 {rs.halted_until_day}")
        self.state.positions = positions; self._save_risk(rs)
        st["last_bar_ts"][symbol] = str(t); st["equity"] = equity; st["unrealized"] = unreal
        st["last_signal"] = st.get("last_signal", {}); st["last_signal"][symbol] = rep.to_dict() | {"freshness": why, "data_ok": not stale}
        st["seen_setups"] = st["seen_setups"][-2000:]; st["order_ids"] = st["order_ids"][-5000:]
        return out

    def _last_price(self, symbol: str, t: pd.Timestamp) -> float:
        mc = self.contexts[symbol]
        j = mc.ts.searchsorted(t, side="right") - 1
        return float(mc.cl[max(j, 0)])

    def _warm(self, symbol: str, upto_i: int, warm_bars: int = 600) -> None:
        """重启后重建策略状态机：在不产生订单的情况下空跑最近 warm_bars 根。"""
        mc = self.contexts[symbol]; eng = self.engines[symbol]; eng.reset()
        for i in range(max(1, upto_i - warm_bars), upto_i):
            eng.evaluate(mc, i, float(self.state.data.get("equity", self.state.data["cash"])))

    # ---------- 入口 ----------
    def run_once(self, now: pd.Timestamp | None = None, contexts=None) -> dict:
        if not self._lock():
            return {"skipped": "已有实例在运行（锁文件存在）"}
        try:
            now = now or pd.Timestamp.now(tz="UTC")
            report = {"run_at": str(now), "symbols": {}, "data_refresh": None}
            if self.refresh_data and contexts is None:
                try:
                    report["data_refresh"] = self._with_retries(lambda: {k: v["result"] for k, v in run_ingest(self.symbols, ["1w", "1d", "4h", self.exec_tf])["symbols"][self.symbols[0]].items()}, "ingest")
                except Exception as e:  # noqa: BLE001
                    report["data_refresh"] = f"失败：{e}"; self._alert(f"行情刷新失败：{e}")
            self.contexts = contexts or load_contexts(self.symbols, self.exec_tf, self.c)
            for s in self.symbols:
                mc = self.contexts[s]
                last_done = self.state.data["last_bar_ts"].get(s)
                start_i = int(mc.ts.searchsorted(pd.Timestamp(last_done), side="right")) if last_done else mc.n - 1
                self._warm(s, start_i)
                outs = [self.process_bar(s, i, now) for i in range(start_i, mc.n)]
                report["symbols"][s] = {"processed_bars": len(outs), "last": outs[-1] if outs else None, "stale": bars_are_stale(mc.ex, self.exec_tf, now=now)[1]}
            report["equity"] = self.state.data.get("equity"); report["positions"] = [p.to_dict() for p in self.state.positions]; report["pending"] = self.state.data["pending"]
            self.state.save()
            return report
        finally:
            self._unlock()

    def loop(self) -> None:
        pc = self.c["paper"]
        while True:
            try:
                r = self.run_once()
                self._event("cycle", equity=r.get("equity"), n_pos=len(r.get("positions", [])))
            except Exception as e:  # noqa: BLE001
                self._alert(f"循环异常：{type(e).__name__}: {str(e)[:160]}")
            time.sleep(float(pc["poll_interval_s"]))

    def replay(self, n_bars: int = 3000, contexts=None, save_every: int = 200) -> dict:
        """历史回放：从倒数第 n_bars 根开始逐根推进（模拟时钟 = 该根收盘时刻），每 save_every 根保存状态。"""
        self.contexts = contexts or load_contexts(self.symbols, self.exec_tf, self.c)
        n = min(mc.n for mc in self.contexts.values())
        start_i = max(1, n - n_bars)
        for s in self.symbols:
            if not self.state.data["last_bar_ts"].get(s):
                self.state.data["last_bar_ts"][s] = str(self.contexts[s].ts.iloc[start_i - 1])
            self._warm(s, int(self.contexts[s].ts.searchsorted(pd.Timestamp(self.state.data["last_bar_ts"][s]), side="right")))
        statuses: dict[str, int] = {}
        step = 0
        for i in range(start_i, n):
            for s in self.symbols:
                mc = self.contexts[s]
                if pd.Timestamp(self.state.data["last_bar_ts"][s]) >= mc.ts.iloc[i]:
                    continue
                sim_now = mc.ts.iloc[i] + pd.Timedelta(seconds=mc.bar_seconds)
                o = self.process_bar(s, i, sim_now); statuses[o["status"]] = statuses.get(o["status"], 0) + 1
            step += 1
            if step % save_every == 0:
                self.state.save()
        self.state.save()
        return {"mode": "replay", "bars": n - start_i, "status_counts": statuses, "equity": self.state.data.get("equity"), "n_trades": self.state.data["n_trades"], "realized_pnl": self.state.data["realized_pnl"],
                "open_positions": len(self.state.data["positions"]), "state_file": str(self.state.path), "trade_log": str(self.trade_log)}
