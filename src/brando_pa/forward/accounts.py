"""前瞻模拟账户（第八阶段）：同一实时行情驱动多个独立 Paper 账户。

账户（规则原样取自已预登记模型，不新增策略）：
  A_BTC_SMA200            BTC 日线收盘 > SMA200 → 100% BTC，否则 USDT（第六阶段策略 A，无状态机）
  B_modelB_state_machine  第七阶段模型 B 独立趋势满额配置 + 有限熔断状态机
  REF_ETH_SMA200          ETH SMA200 参考账户
每个账户两本账：ideal（零成本）与 cost（手续费 10 bps + 滑点 5 bps，压力假设）；风险状态机按 cost 账本权益评估。
只允许 BUY / SELL / HOLD；现金与持仓不为负；真实下单无实现。

前瞻纪律：forward_start_utc = 系统首次运行时刻；只处理收盘时刻 > forward_start 的日线信号；历史 K 线只用于指标预热（事件 kind=warmup）。
事件日志 events.jsonl 追加写入 + sha256 哈希链（trendsys.paper._append / verify_chain）；订单按 order_id 去重；状态持久化 state.json，重启后恢复。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg, own_data_dir, report_dir
from ..data.sources import utcnow
from ..data.store import BarStore
from ..schema import tf_seconds
from ..spot.ledger import Lot, SpotLedger
from ..trendsys.paper import _append, verify_chain
from ..trendsys.risk_layer import make_risk
from ..trendsys.trend_layer import trend_signal
from ..usdt.allocator import PortfolioAllocator
from ..usdt.engine import liquidation_value

SYMS = ["BTCUSDT", "ETHUSDT"]


def account_specs(c: dict) -> dict:
    return {
        "A_BTC_SMA200": {"weights": {"both": {"BTCUSDT": 1.0}, "btc_only": {"BTCUSDT": 1.0}, "eth_only": {}, "none": {}}, "risk_mode": "none", "label": "账户 A：BTC SMA200"},
        "B_modelB_state_machine": {"weights": c["usdt"]["models"]["B_full_single"], "risk_mode": "state_machine", "label": "账户 B：模型 B 满额配置 + 状态机"},
        "REF_ETH_SMA200": {"weights": {"both": {"ETHUSDT": 1.0}, "eth_only": {"ETHUSDT": 1.0}, "btc_only": {}, "none": {}}, "risk_mode": "none", "label": "参考：ETH SMA200"},
    }


class ForwardAccount:
    LEDGERS = ("ideal", "cost")

    def __init__(self, name: str, spec: dict, c: dict, log_dir: Path, initial: float, fee_bps: float, slip_bps: float):
        self.name, self.spec, self.c = name, spec, c; self.dir = log_dir; self.dir.mkdir(parents=True, exist_ok=True)
        self.initial = float(initial); self.fee, self.slip = float(fee_bps), float(slip_bps)
        self.events = self.dir / "events.jsonl"; self.state_path = self.dir / "state.json"
        self.risk = make_risk(spec["risk_mode"], c["trendsys"]["risk"])
        self.allocs = {k: PortfolioAllocator(spec["weights"], SYMS, c["usdt"]["allocator"], 0.0 if k == "ideal" else fee_bps, 0.0 if k == "ideal" else slip_bps) for k in self.LEDGERS}
        self.state = self._load()

    # ---- 状态 ----
    def _fresh(self) -> dict:
        return {"account": self.name, "initial_usdt": self.initial, "fee_bps": self.fee, "slippage_bps": self.slip, "forward_start_utc": None, "last_daily_ts": None, "pending": [], "processed_orders": [], "n_runs": 0,
                "ledgers": {k: {"cash": self.initial, "lots": {}, "fees_paid": 0.0, "realized": 0.0, "seq": 0, "peak_equity": self.initial, "max_drawdown": 0.0, "max_drawdown_usdt": 0.0} for k in self.LEDGERS}, "risk": None, "alloc_last_target": None}

    def _load(self) -> dict:
        if self.state_path.exists():
            st = json.loads(self.state_path.read_text())
            if st.get("risk"):
                self.risk.restore(st["risk"])
            for k in self.LEDGERS:
                self.allocs[k].last_target = st.get("alloc_last_target")
            return st
        return self._fresh()

    def save(self) -> None:
        self.state["risk"] = self.risk.snapshot(); self.state["alloc_last_target"] = self.allocs["cost"].last_target; self.state["saved_at"] = str(utcnow())
        atomic_write_json(self.state_path, self.state, indent=1, default=str)

    def ledger(self, k: str) -> SpotLedger:
        s = self.state["ledgers"][k]; L = SpotLedger(float(s["cash"]), 0.0 if k == "ideal" else self.fee, 0.0 if k == "ideal" else self.slip)
        for sym, lots in s.get("lots", {}).items():
            L.lots[sym] = [Lot(**l) for l in lots]
        L.fees_paid = float(s.get("fees_paid", 0.0)); L.realized = float(s.get("realized", 0.0)); L._seq = int(s.get("seq", 0)); return L

    def store_ledger(self, k: str, L: SpotLedger) -> None:
        s = self.state["ledgers"][k]; s.update({"cash": L.cash, "lots": {sym: [asdict(l) for l in ls if l.qty > 1e-15] for sym, ls in L.lots.items()}, "fees_paid": L.fees_paid, "realized": L.realized, "seq": L._seq})

    def log(self, kind: str, **kw) -> None:
        _append(self.events, {"kind": kind, "account": self.name, **kw})

    # ---- 一次日线收盘决策 ----
    def decide(self, close_t: pd.Timestamp, on: dict, prices: dict, daily_drop: float, data_fault: bool, data_ts, indicators: dict, execute: bool = True, missed_reason: str = "", quality_fault: bool = False, source: dict | None = None, mode: str = "forward", exec_after: pd.Timestamp | None = None) -> list[dict]:
        exec_after = exec_after if exec_after is not None else close_t
        """execute=False：服务离线错过的历史信号——照常计算并记录决定，但标记 missed，不生成订单、不改变分配器状态。quality_fault：跨来源偏差可能改变决策 → 记录信号但暂停下单。"""
        Lc = self.ledger("cost"); eq_c = Lc.equity(prices); expo_c = Lc.exposure(prices)
        self.risk.set_data_fault(data_fault, "stale_or_gap" if data_fault else "")
        if not execute:
            saved = self.risk.snapshot()
        dec = self.risk.evaluate(close_t, eq_c, daily_drop, expo_c)
        if not execute:
            self.risk.restore(saved); self.risk.log = self.risk.log[:-1] if self.risk.log else self.risk.log     # 错过的信号不推进风险状态机
        if dec.data_fault:
            dec.max_exposure = min(dec.max_exposure, expo_c)
        self.log("risk", mode=mode, signal_ts=str(close_t), state=dec.state, max_exposure=dec.max_exposure, force_exit=dec.force_exit, dd_ref=dec.dd_ref, dd_all=dec.dd_all, equity_cost=eq_c, data_fault=dec.data_fault, note=dec.note, executed=execute)
        orders = []
        for k in self.LEDGERS:
            L = self.ledger(k); eq = L.equity(prices)
            lt = self.allocs[k].last_target
            a = self.allocs[k].allocate(on, {s: L.qty(s) for s in SYMS}, L.cash, prices, dec, eq)
            if not execute or quality_fault:
                self.allocs[k].last_target = lt                      # 不执行：分配器目标不前移，下一次实时收盘自然补齐
            if k == "cost":
                sig = {"signal_ts": str(close_t), "data_ts": str(data_ts), "prices": prices, "indicators": indicators, "trend_on": on, "risk_state": dec.state, "max_exposure": dec.max_exposure, "target_weights": a.target_weights, "current_weights": a.inputs["cur_weights"],
                       "signal": "HOLD" if not a.orders else ("BUY" if any(o["side"] == "BUY" for o in a.orders) and not any(o["side"] == "SELL" for o in a.orders) else "SELL" if not any(o["side"] == "BUY" for o in a.orders) else "REBALANCE"), "reason": a.reason, "equity_usdt": eq, "usdt": L.cash, "holdings": {s: L.qty(s) for s in SYMS}, "data_fault": dec.data_fault,
                       "signal_generated_at": str(utcnow()), "mode": mode, "market_source": source, "executed": bool(execute and not quality_fault), "missed_reason": missed_reason if not execute else ("data_quality_fault" if quality_fault else "")}
                self.log("signal" if (execute and not quality_fault) else "missed_signal", **sig)
            if not execute or quality_fault:
                continue
            for i, o in enumerate(sorted(a.orders, key=lambda o: 0 if o["side"] == "SELL" else 1)):
                oid = f"{self.name}:{k}:{o['symbol']}:{close_t.strftime('%Y%m%d')}:{o['side']}:{i}"
                if oid in self.state["processed_orders"] or any(p["order_id"] == oid for p in self.state["pending"]):
                    continue                                   # 重复订单去重
                od = {"order_id": oid, "ledger": k, "symbol": o["symbol"], "side": o["side"], "qty": o.get("qty"), "amount": o.get("amount"), "signal_ts": str(close_t), "exec_after": str(exec_after), "late": bool(exec_after > close_t), "reason": o["reason"], "est_cost": o.get("est_cost", 0.0), "order_simulated_at": str(utcnow()), "mode": mode}
                self.state["pending"].append(od); self.log("order", **od); orders.append(od)
        return orders

    def fill_pending(self, bars: dict, now: pd.Timestamp) -> list[dict]:
        keep = []; fills = []; tf = "1h"; sec = tf_seconds(tf)
        for od in sorted(self.state["pending"], key=lambda o: 0 if o["side"] == "SELL" else 1):
            e = bars[od["symbol"]][tf]; m = e[(e["ts"] >= pd.Timestamp(od["exec_after"])) & ((e["ts"] + pd.Timedelta(seconds=sec)) <= now)]
            if m.empty:
                keep.append(od); continue
            bar = m.iloc[0]; o = float(bar["open"]); t = bar["ts"]; k = od["ledger"]; L = self.ledger(k); s = od["symbol"]
            if od["side"] == "SELL":
                q = L.qty(s) if od.get("qty") is None else min(float(od["qty"]), L.qty(s))
                if q > 1e-12:
                    rec = L.sell(s, q, o, t, od["reason"]); f = {"order_id": od["order_id"], "ledger": k, "symbol": s, "side": "SELL", "qty": q, "fill_price": rec["price"], "bar_open": o, "fee": rec["fee"], "fill_ts": str(t), "net_pnl": rec["net_pnl"], "reason": od["reason"], "note": "simulated fill at next closed 1h bar open; not an exchange guarantee"}
                    self.log("fill", **f); fills.append(f)
            else:
                buf = float(self.c["usdt"]["allocator"].get("cash_buffer", 0.001)); amt = min(float(od["amount"]), L.cash * (1 - buf)); px = o * (1 + L.slippage_bps / 1e4) * (1 + L.fee_bps / 1e4); q = amt / px
                if q > 0 and q * o >= 10:
                    lot = L.buy(s, q, o, t, od["reason"], 0.0, 0.0, 0.0); f = {"order_id": od["order_id"], "ledger": k, "symbol": s, "side": "BUY", "qty": q, "fill_price": lot.price, "bar_open": o, "fee": L.trade_log[-1]["fee"], "fill_ts": str(t), "reason": od["reason"], "note": "simulated fill at next closed 1h bar open; not an exchange guarantee"}
                    self.log("fill", **f); fills.append(f)
            self.store_ledger(k, L); self.state["processed_orders"].append(od["order_id"])
        self.state["pending"] = keep; return fills

    def mark(self, prices: dict, ts, kind: str = "nav", **extra) -> dict:
        out = {}
        for k in self.LEDGERS:
            L = self.ledger(k); eq = L.equity(prices); s = self.state["ledgers"][k]
            s["peak_equity"] = max(float(s.get("peak_equity", self.initial)), eq); dd = eq / s["peak_equity"] - 1; s["max_drawdown"] = min(float(s.get("max_drawdown", 0.0)), dd); s["max_drawdown_usdt"] = min(float(s.get("max_drawdown_usdt", 0.0)), eq - s["peak_equity"])
            out[k] = {"equity_usdt": eq, "liquidation_usdt": liquidation_value(L, prices), "profit_usdt": eq - self.initial, "return": eq / self.initial - 1, "realized_usdt": L.realized, "unrealized_usdt": L.unrealized(prices), "cash": L.cash, "holdings": {sym: L.qty(sym) for sym in SYMS}, "weights": {sym: L.qty(sym) * prices[sym] / eq if eq > 0 else 0.0 for sym in SYMS}, "exposure": L.exposure(prices), "drawdown": dd, "max_drawdown": s["max_drawdown"], "max_drawdown_usdt": s["max_drawdown_usdt"], "fees_paid": L.fees_paid}
        self.log(kind, ts=str(ts), prices=prices, ledgers=out, risk_state=getattr(self.risk, "state", "NORMAL"), **extra); return out


class ForwardRunner:
    def __init__(self, c: dict | None = None, log_dir: Path | None = None, store: BarStore | None = None, accounts: list[str] | None = None, mode: str = "forward"):
        self.c = c or cfg(); self.mode = mode
        self.dir = Path(log_dir or (report_dir(self.c) / "phase8_forward")); self.dir.mkdir(parents=True, exist_ok=True)
        self.store = store or BarStore(root=own_data_dir(self.c) / "forward_bars", fallback_root=None)
        cost = self.c["usdt"]["costs"]["round2"]; initial = float(self.c["usdt"]["initial_cash_usdt"])
        specs = account_specs(self.c); names = accounts or list(specs)
        self.accounts = {n: ForwardAccount(n, specs[n], self.c, self.dir / n, initial, float(cost["fee_bps"]), float(cost["slippage_bps"])) for n in names}
        self.runner_events = self.dir / "runner_events.jsonl"; self.status_path = self.dir / "status.json"
        self.mult = float(self.c["data"].get("max_bar_age_multiplier", 3)); self.sma = int(self.c["usdt"]["trend"]["sma"])

    def _log(self, kind: str, **kw) -> None:
        _append(self.runner_events, {"kind": kind, "mode": self.mode, **kw})

    def load_bars(self) -> dict:
        return {s: {tf: self.store.load(s, tf, allow_fallback=False) for tf in ("1h", "1d")} for s in SYMS}

    def data_check(self, bars: dict, now: pd.Timestamp) -> dict:
        issues = []; anomalies = []
        for s in SYMS:
            for tf in ("1h", "1d"):
                b = bars[s][tf]
                if b.empty:
                    issues.append(f"{s} {tf} 无数据"); continue
                last_close = b["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf)); lag = (now - last_close).total_seconds()
                if lag > self.mult * tf_seconds(tf):
                    issues.append(f"{s} {tf} 过期：最新收盘 {last_close}，落后 {lag/3600:.1f} 小时")
                if lag < 0:
                    issues.append(f"{s} {tf} 含未收盘 K 线")
                d = b["ts"].diff().dt.total_seconds().tail(500)
                if (d > tf_seconds(tf) * 1.5).any():
                    issues.append(f"{s} {tf} 最近 500 根有 {int((d > tf_seconds(tf) * 1.5).sum())} 处缺口")
            r = bars[s]["1h"]["close"].pct_change().tail(48).abs()
            if len(r) and r.max() > 0.2:
                anomalies.append(f"{s} 最近 48 根 1h 出现 {r.max():.1%} 单根跳变")
        return {"ok": not issues and not anomalies, "issues": issues, "anomalies": anomalies, "latest": {s: {tf: str(bars[s][tf]["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf))) for tf in ("1h", "1d") if not bars[s][tf].empty} for s in SYMS}}

    def run_once(self, now: pd.Timestamp | None = None, bars: dict | None = None, quality_fault: bool = False, quality_note: str = "") -> dict:
        now = now or utcnow(); bars = bars or self.load_bars()
        grace = pd.Timedelta(hours=float(self.c.get("forward", {}).get("missed_signal_grace_hours", 3)))
        sec = {"1h": 3600, "1d": 86400}
        bars = {s: {tf: b[(b["ts"] + pd.Timedelta(seconds=sec[tf])) <= now].reset_index(drop=True) for tf, b in bb.items()} for s, bb in bars.items()}
        chk = self.data_check(bars, now); data_fault = not chk["ok"]
        if quality_fault:
            chk["ok"] = False; chk["issues"] = chk["issues"] + [f"数据质量异常（跨来源偏差）：{quality_note}"]; data_fault = True
        self._log("data_check", now=str(now), **chk)
        if any(bars[s]["1h"].empty or bars[s]["1d"].empty for s in SYMS):
            self._log("no_data", now=str(now)); return {"status": "no_data", "data_check": chk}
        sig = {s: trend_signal(bars[s]["1d"], self.sma, 1) for s in SYMS}; d1_ts = sig[SYMS[0]]["ts"]
        px_last = {s: float(bars[s]["1h"]["close"].iloc[-1]) for s in SYMS}; data_ts = {s: str(bars[s]["1h"]["ts"].iloc[-1] + pd.Timedelta(hours=1)) for s in SYMS}
        out = {"now": str(now), "mode": self.mode, "data_check": chk, "accounts": {}}
        for name, acc in self.accounts.items():
            st = acc.state; first = st["forward_start_utc"] is None
            if first:
                st["forward_start_utc"] = str(now)
                last = sig[SYMS[0]].iloc[-1]
                acc.log("warmup", forward_start_utc=str(now), note="历史 K 线仅用于指标预热；此前不产生任何前瞻信号", indicators={s: {"sma200": float(sig[s]["sma"].iloc[-1]), "daily_close": float(sig[s]["close"].iloc[-1]), "trend_on": bool(sig[s]["on"].iloc[-1]), "signal_day": str(sig[s]["ts"].iloc[-1])[:10]} for s in SYMS}, source={s: str(bars[s]["1h"]["source"].iloc[-1]) if "source" in bars[s]["1h"] else None for s in SYMS})
            fstart = pd.Timestamp(st["forward_start_utc"]); last_done = pd.Timestamp(st["last_daily_ts"]) if st.get("last_daily_ts") else None
            closes = d1_ts + pd.Timedelta(days=1)
            new = d1_ts[(closes > fstart) & ((d1_ts > last_done) if last_done is not None else True)]
            if quality_fault:
                for od in st["pending"]:                              # 数据质量异常：暂停模拟成交；挂单顺延到当前小时之后（不回填旧价格）
                    if pd.Timestamp(od["exec_after"]) < now.floor("h"):
                        od["exec_after"] = str(now.floor("h")); od["deferred_reason"] = "data_quality_fault"
                fills = []
            else:
                fills = acc.fill_pending(bars, now)
            for dts in new:
                close_t = dts + pd.Timedelta(days=1); prices = {}; drops = []; on = {}; ind = {}
                for s in SYMS:
                    e = bars[s]["1h"]; m = e[(e["ts"] + pd.Timedelta(hours=1)) <= close_t]; d1 = bars[s]["1d"]; k = d1.index[d1["ts"] == dts]
                    prices[s] = float(m["close"].iloc[-1]) if len(m) else float(d1.loc[k[0], "close"])
                    drops.append(float(d1["close"].iloc[k[0]] / d1["close"].iloc[k[0] - 1] - 1) if len(k) and k[0] >= 1 else 0.0)
                    row = sig[s][sig[s]["ts"] == dts]; on[s] = bool(row["on"].iloc[0]) if len(row) else False
                    ind[s] = {"daily_close": float(row["close"].iloc[0]) if len(row) else None, "sma200": float(row["sma"].iloc[0]) if len(row) and np.isfinite(row["sma"].iloc[0]) else None, "above_sma": bool(row["above"].iloc[0]) if len(row) else None, "trend": "UP" if on[s] else "DOWN_OR_NONE", "source": str(d1.loc[k[0], "source"]) if (len(k) and "source" in d1) else None}
                src = {s: ind[s].get("source") for s in SYMS}
                live = (now - close_t) <= grace + pd.Timedelta(hours=1)            # 收盘后 1h 执行 K 线 + 宽限期内 → 实时；否则为服务离线错过
                late = (now - close_t) > pd.Timedelta(hours=2)                      # 标准执行 K 线（收盘后第一根 1h）已经过去 → 不回填，改在当前正在形成的 1h K 线收盘后按其开盘成交
                exec_after = now.floor("h") if (live and late and self.mode != "replay") else close_t
                acc.log("market", mode=self.mode, signal_ts=str(close_t), prices=prices, data_ts={s: str(bars[s]["1h"].loc[(bars[s]["1h"]["ts"] + pd.Timedelta(hours=1)) <= close_t, "ts"].iloc[-1] + pd.Timedelta(hours=1)) for s in SYMS}, indicators=ind, source=src, live=live or self.mode == "replay")
                acc.decide(close_t, on, prices, min(drops), data_fault, data_ts, ind, execute=(live or self.mode == "replay"), missed_reason="" if (live or self.mode == "replay") else f"late: close {close_t} first processed at {now} (> grace); not executed, no backdated fill", quality_fault=quality_fault, source=src, mode=self.mode, exec_after=exec_after)
                st["last_daily_ts"] = str(dts)
                if not quality_fault:
                    fills += acc.fill_pending(bars, now)
            nav = acc.mark(px_last, now, "nav", data_ts=data_ts, data_fault=data_fault, pending=len(st["pending"]), mode=self.mode, source={s: str(bars[s]["1h"]["source"].iloc[-1]) if "source" in bars[s]["1h"] else None for s in SYMS})
            st["n_runs"] = int(st.get("n_runs", 0)) + 1; st["last_run_at"] = str(now); acc.save()
            out["accounts"][name] = {"label": acc.spec["label"], "nav": nav, "fills": fills, "pending": st["pending"], "risk_state": getattr(acc.risk, "state", "NORMAL"), "forward_start_utc": st["forward_start_utc"], "last_daily_ts": st["last_daily_ts"]}
        self._write_status(out, bars); return out

    def _write_status(self, out: dict, bars: dict) -> None:
        bh = {}
        for name, acc in self.accounts.items():
            fs = acc.state.get("forward_start_utc")
            if fs:
                for s in SYMS:
                    e = bars[s]["1h"]; after = e[e["ts"] >= pd.Timestamp(fs)]
                    if len(after):
                        bh[s] = {"start_price": float(after["open"].iloc[0]), "last_price": float(e["close"].iloc[-1]), "buy_hold_return": float(e["close"].iloc[-1] / after["open"].iloc[0] - 1)}
                break
        atomic_write_json(self.status_path, out | {"buy_hold_since_forward_start": bh, "chains": self.verify_all(), "real_trading": "disabled (no implementation)"}, indent=1, default=str)

    def verify_all(self) -> dict:
        res = {"runner": verify_chain(self.runner_events)}
        for n, a in self.accounts.items():
            res[n] = verify_chain(a.events)
        return res

    def replay(self, start: str, end: str | None = None) -> dict:
        """独立历史回放（mode=replay，单独目录）：逐日推进，严格只用当日之前已收盘 K 线。不是前瞻。"""
        bars = self.load_bars(); last = min(b["1h"]["ts"].iloc[-1] for b in bars.values()) + pd.Timedelta(hours=1)
        now = pd.Timestamp(start, tz="UTC"); end_ts = min(pd.Timestamp(end, tz="UTC"), last) if end else last; n = 0
        while now <= end_ts:
            self.run_once(now=now, bars=bars); n += 1; now += pd.Timedelta(days=1)
        return {"steps": n, "chains": self.verify_all()}
