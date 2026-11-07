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
            st["cash"] += dcash
            if closed:
                tr = trades[-1]
                st["consecutive_losses"] = int(st.get("consecutive_losses", 0)) + 1 if tr["net_pnl"] < 0 else 0
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
                cb = self.circuit_breakers(rs, equity)
                if cb:
                    ok = False; whyr = whyr + [f"熔断：{x}" for x in cb]
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
        prev_day = st.get("stats_day")
        if prev_day is not None and prev_day != rs.day:
            self.daily_stats(prev_day)
        st["stats_day"] = rs.day
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

    # ---------- 稳定性：实时能力 / 中断检测 / 补齐 / 熔断 / 日统计 ----------
    def realtime_status(self, now: pd.Timestamp) -> dict:
        """实时运行条件 = 行情适配器为交易所实时源 且 最新执行 K 线未过期。公开缓存（每日更新）永远不算实时。"""
        adapter = self.c["data"].get("exchange_adapter", "static_klines")
        fresh = {}
        for s in self.symbols:
            mc = self.contexts[s] if self.contexts else None
            stale, why = bars_are_stale(mc.ex, self.exec_tf, now=now) if mc is not None else (True, "无数据")
            fresh[s] = {"stale": bool(stale), "detail": why}
        exchange_live = adapter != "static_klines"
        capable = exchange_live and not any(v["stale"] for v in fresh.values())
        return {"adapter": adapter, "exchange_realtime_source": exchange_live, "bars_fresh": fresh, "realtime_capable": capable,
                "label": "实时模拟盘" if capable else ("延迟数据模拟盘（不具备实时运行条件：行情源为每日更新的公开缓存或 K 线过期）")}

    def detect_data_issues(self, now: pd.Timestamp) -> list[str]:
        """数据中断/异常检测：最近 N 根有缺口、最新 K 线过期、单根异常跳变、参考价与最后收盘偏离过大。"""
        issues = []
        for s in self.symbols:
            mc = self.contexts[s]; ex = mc.ex.tail(200)
            sec = mc.bar_seconds
            d = ex["ts"].diff().dt.total_seconds().dropna()
            n_gap = int((d > sec * 1.5).sum())
            if n_gap:
                issues.append(f"{s} 最近 200 根 {self.exec_tf} 有 {n_gap} 处缺口")
            stale, why = bars_are_stale(mc.ex, self.exec_tf, now=now)
            if stale:
                issues.append(f"{s} {why}")
            r = ex["close"].pct_change().abs()
            if (r > 0.2).any():
                issues.append(f"{s} 最近出现单根 >20% 跳变（{r.max():.1%}），疑似坏数据")
        return issues

    def backfill_missing(self) -> dict:
        """重连与补齐：检测到缺口/过期时重新拉取（适配器窗口刷新 + upsert 去重），返回补齐结果。"""
        try:
            st = self._with_retries(lambda: run_ingest(self.symbols, ["1w", "1d", "4h", self.exec_tf]), "backfill")
            return {k: {tf: e.get("result") for tf, e in v.items()} for k, v in st["symbols"].items()}
        except Exception as e:  # noqa: BLE001
            self._alert(f"补齐失败：{e}"); return {"error": str(e)}

    def circuit_breakers(self, rs: RiskState, equity: float) -> list[str]:
        """异常熔断：连续亏损、峰值回撤、净值异常、数据异常 → 返回触发原因（非空即暂停新开仓）。"""
        cb = self.c["paper"].get("circuit_breakers", {}) or {}
        out = []
        st = self.state.data
        peak = max(float(st.get("peak_equity", equity)), equity); st["peak_equity"] = peak
        dd = equity / peak - 1 if peak > 0 else 0.0
        if dd <= -float(cb.get("max_drawdown", 0.10)):
            out.append(f"峰值回撤 {dd:.2%} ≥ {float(cb.get('max_drawdown', 0.10)):.0%}")
        if int(st.get("consecutive_losses", 0)) >= int(cb.get("max_consecutive_losses", 8)):
            out.append(f"连续亏损 {st.get('consecutive_losses')} 笔 ≥ {cb.get('max_consecutive_losses', 8)}")
        if st.get("data_issues"):
            out.append("数据异常：" + "；".join(st["data_issues"][:3]))
        return out

    def daily_stats(self, day: str) -> None:
        """每日绩效统计（UTC 日）：净值、已实现、交易数、胜率、最大持仓数，追加 daily_stats.jsonl。"""
        st = self.state.data
        tl = self.trade_log
        rows = []
        if tl.exists():
            rows = [json.loads(x) for x in tl.read_text().splitlines() if x.strip()]
        today = [t for t in rows if str(t.get("exit_ts", ""))[:10] == day]
        rec = {"day": day, "equity": st.get("equity"), "cash": st.get("cash"), "realized_pnl_day": float(sum(t["net_pnl"] for t in today)), "n_trades_day": len(today),
               "win_rate_day": (sum(1 for t in today if t["net_pnl"] > 0) / len(today)) if today else None, "open_positions": len(st.get("positions", [])), "halted_until": st["risk"].get("halted_until_day"),
               "realized_total": st.get("realized_pnl"), "n_trades_total": st.get("n_trades"), "mode": st.get("mode"), "ts": datetime.now(timezone.utc).isoformat()}
        p = self.trade_log.parent / "daily_stats.jsonl"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # ---------- 入口 ----------
    def run_once(self, now: pd.Timestamp | None = None, contexts=None) -> dict:
        if not self._lock():
            return {"skipped": "已有实例在运行（锁文件存在）"}
        try:
            now = now or pd.Timestamp.now(tz="UTC")
            report = {"run_at": str(now), "symbols": {}, "data_refresh": None}
            st = self.state.data
            if self.refresh_data and contexts is None:
                try:
                    report["data_refresh"] = self._with_retries(lambda: {k: v["result"] for k, v in run_ingest(self.symbols, ["1w", "1d", "4h", self.exec_tf])["symbols"][self.symbols[0]].items()}, "ingest")
                    st["refresh_failures"] = 0
                except Exception as e:  # noqa: BLE001
                    report["data_refresh"] = f"失败：{e}"; st["refresh_failures"] = int(st.get("refresh_failures", 0)) + 1
                    self._alert(f"行情刷新失败（连续 {st['refresh_failures']} 次）：{e}")
            self.contexts = contexts or load_contexts(self.symbols, self.exec_tf, self.c)
            issues = self.detect_data_issues(now)
            if issues and self.refresh_data and contexts is None:
                report["backfill"] = self.backfill_missing()
                self.contexts = load_contexts(self.symbols, self.exec_tf, self.c)
                issues = self.detect_data_issues(now)
            st["data_issues"] = [x for x in issues if "过期" not in x]      # 过期单独由 data_ok 处理；缺口/跳变触发熔断
            report["data_issues"] = issues
            report["realtime"] = self.realtime_status(now)
            try:
                from ..data.derivatives_live import collect as _collect_deriv
                report["derivatives_forward_snapshot"] = _collect_deriv(symbols=self.symbols) if self.refresh_data and contexts is None else {"ok": False, "error": "离线/回放模式不采集"}
            except Exception as e:  # noqa: BLE001
                report["derivatives_forward_snapshot"] = {"ok": False, "error": type(e).__name__}
            st["mode"] = "paper_realtime" if report["realtime"]["realtime_capable"] else "paper_delayed_data"
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
