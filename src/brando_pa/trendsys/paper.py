"""前瞻模拟（第六阶段）：只读行情驱动的三层趋势系统模拟盘。

- 数据：BarStore 只读；新鲜度检查（最新日线/执行 K 线收盘距今 > N 个周期 → 安全态：禁止新买入、不强制卖出、只记录）；
  缺口检测与补齐（仅通过现有只读 run_ingest；失败写 failures.jsonl）。
- 信号：每根新的已收盘日线产生一次决策，记录信号时间戳（日线收盘时刻）、数据时间戳（最新 K 线收盘）、墙钟时间。
- 成交：在日线收盘后的第一根执行 K 线开盘模拟成交；该 K 线尚未出现时订单挂起（pending），下次运行补成交（状态恢复）。
- 记录：signals / orders / fills / nav / risk_state / failures 全部 JSONL 只追加，每行带 prev_hash → sha256 哈希链（verify_chain 可检测事后修改/删除）。
- 真实交易：无实现；任何 live 参数都被拒绝。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg
from ..data.sources import bars_are_stale, utcnow
from ..data.store import BarStore
from ..spot.ledger import SpotLedger
from .risk_layer import make_risk
from .trend_layer import trend_signal

GENESIS = "0" * 64


def _append(path: Path, rec: dict) -> str:
    """只追加 + 哈希链。返回本行哈希。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = GENESIS
    if path.exists() and path.stat().st_size:
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - 65536)); last = f.read().splitlines()[-1]
        prev = json.loads(last).get("hash", GENESIS)
    body = dict(rec); body["prev_hash"] = prev; body["recorded_at"] = str(utcnow())
    h = hashlib.sha256(json.dumps(body, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest(); body["hash"] = h
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(body, ensure_ascii=False, default=str) + "\n"); f.flush(); os.fsync(f.fileno())
    return h


def verify_chain(path: Path) -> dict:
    if not path.exists():
        return {"ok": True, "n": 0}
    prev = GENESIS; n = 0
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line); h = rec.pop("hash", None)
        if rec.get("prev_hash") != prev or hashlib.sha256(json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest() != h:
            return {"ok": False, "n": n, "broken_at_line": n + 1}
        prev = h; n += 1
    return {"ok": True, "n": n}


class TrendSysPaper:
    def __init__(self, c: dict | None = None, log_dir: Path | None = None, strategy: str | None = None, risk_mode: str | None = None, symbols: list[str] | None = None, store: BarStore | None = None, live: bool = False):
        if live:
            raise RuntimeError("真实交易接口在本项目中无实现且被禁用（live_trading_allowed 三重闸门之外本模块也不接受 live 参数）")
        self.c = c or cfg(); self.tc = self.c["trendsys"]
        self.dir = Path(log_dir or self.tc["paper"]["log_dir"]); self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.symbols = symbols or list(self.tc["symbols"]); self.exec_tf = self.tc["exec_tf"]
        self.strategy = strategy or "A_sma200"; self.risk_mode = risk_mode or "state_machine"
        self.confirm = int(self.tc["trend"]["strategies"][self.strategy]["confirm_days"]); self.sma = int(self.tc["trend"]["sma"])
        self.store = store or BarStore()
        self.weights = {s: 1.0 / len(self.symbols) for s in self.symbols}
        self.risk = make_risk(self.risk_mode, self.tc["risk"])
        self.state = self._load_state()

    # ---- 状态 ----
    def _load_state(self) -> dict:
        if self.state_path.exists():
            st = json.loads(self.state_path.read_text())
            if st.get("risk"):
                self.risk.restore(st["risk"])
            return st
        return {"cash": float(self.tc["initial_cash_usdt"]), "holdings": {s: 0.0 for s in self.symbols}, "lots": {}, "last_daily_ts": None, "pending": [], "fees_paid": 0.0, "realized": 0.0, "strategy": self.strategy, "risk_mode": self.risk_mode, "created_at": str(utcnow()), "n_runs": 0}

    def _save_state(self) -> None:
        self.state["risk"] = self.risk.snapshot(); self.state["saved_at"] = str(utcnow())
        atomic_write_json(self.state_path, self.state, indent=1, default=str)

    def _log(self, name: str, rec: dict) -> None:
        _append(self.dir / f"{name}.jsonl", rec)

    # ---- 数据 ----
    def load_bars(self) -> dict:
        return {s: {"1d": self.store.load(s, "1d"), self.exec_tf: self.store.load(s, self.exec_tf)} for s in self.symbols}

    def data_check(self, bars: dict, now: pd.Timestamp) -> dict:
        mult = float(self.tc["risk"].get("data_fault_max_bar_age_multiplier", 3)); issues = []
        for s, b in bars.items():
            for tf in ("1d", self.exec_tf):
                stale, why = bars_are_stale(b[tf], tf, now=now, multiplier=mult)
                if stale:
                    issues.append(f"{s} {tf} {why}")
                d = b[tf]["ts"].diff().dt.total_seconds().dropna(); sec = {"1d": 86400, "1h": 3600, "4h": 14400}[tf]
                if len(d) and (d.tail(500) > sec * 1.5).any():
                    issues.append(f"{s} {tf} 最近 500 根存在缺口 {int((d.tail(500) > sec * 1.5).sum())} 处")
        return {"ok": not issues, "issues": issues, "latest": {s: {tf: str(b[tf]['ts'].iloc[-1]) for tf in b} for s, b in bars.items()}}

    def backfill(self) -> dict:
        try:
            from ..data.ingest import run_ingest
            st = run_ingest(self.symbols, ["1d", self.exec_tf], store=self.store)
            return {"ok": True, "result": {k: {tf: e.get("result") for tf, e in v.items()} for k, v in st.get("symbols", {}).items()}}
        except Exception as e:  # noqa: BLE001
            self._log("failures", {"kind": "backfill_failed", "error": str(e)}); return {"ok": False, "error": str(e)}

    # ---- 核心 ----
    def run_once(self, now: pd.Timestamp | None = None, allow_backfill: bool = True, bars: dict | None = None) -> dict:
        now = now or utcnow(); bars = bars or self.load_bars()
        chk = self.data_check(bars, now)
        if not chk["ok"] and allow_backfill:
            bf = self.backfill(); bars = self.load_bars(); chk = self.data_check(bars, now); chk["backfill"] = bf
        data_fault = not chk["ok"]
        self.risk.set_data_fault(data_fault, "; ".join(chk["issues"])[:300])
        ref = bars[self.symbols[0]]; ex = ref[self.exec_tf]
        if now is not None:   # 只用 now 之前已收盘的 K 线（重放时严格截断）
            bars = {s: {tf: b[tf][(b[tf]["ts"] + pd.Timedelta(seconds={"1d": 86400, "1h": 3600, "4h": 14400}[tf])) <= now].reset_index(drop=True) for tf in b} for s, b in bars.items()}
            ex = bars[self.symbols[0]][self.exec_tf]
        if ex.empty:
            self._log("failures", {"kind": "no_exec_bars", "now": str(now)}); return {"status": "no_data"}
        sig = {s: trend_signal(bars[s]["1d"], self.sma, self.confirm) for s in self.symbols}
        d1_ts = sig[self.symbols[0]]["ts"]
        last_done = pd.Timestamp(self.state["last_daily_ts"]) if self.state.get("last_daily_ts") else None
        new_days = d1_ts[(d1_ts > last_done)] if last_done is not None else d1_ts.tail(1)
        out = {"now": str(now), "data_check": chk, "new_daily_bars": int(len(new_days)), "fills": [], "signals": []}
        L = self._ledger()
        px_last = {s: float(bars[s][self.exec_tf]["close"].iloc[-1]) for s in self.symbols}
        # 1) 先补成交挂起订单（其执行 K 线现已存在）
        self._fill_pending(bars, L, out)
        # 2) 逐个新日线收盘产生决策
        for dts in new_days:
            close_t = dts + pd.Timedelta(days=1)
            prices = {}
            for s in self.symbols:
                e = bars[s][self.exec_tf]; m = e[(e["ts"] + pd.Timedelta(hours=1)) <= close_t]
                prices[s] = float(m["close"].iloc[-1]) if len(m) else float(bars[s]["1d"].loc[bars[s]["1d"]["ts"] == dts, "close"].iloc[0])
            equity = L.equity(prices); expo = L.exposure(prices)
            drops = []
            for s in self.symbols:
                d1 = bars[s]["1d"]; k = d1.index[d1["ts"] == dts]
                drops.append(float(d1["close"].iloc[k[0]] / d1["close"].iloc[k[0] - 1] - 1) if len(k) and k[0] >= 1 else 0.0)
            dec = self.risk.evaluate(close_t, equity, min(drops), expo)
            self._log("risk_state", {"signal_ts": str(close_t), "state": dec.state, "max_exposure": dec.max_exposure, "reduce_to": dec.reduce_to, "force_exit": dec.force_exit, "dd_ref": dec.dd_ref, "dd_all": dec.dd_all, "equity": equity, "data_fault": dec.data_fault, "note": dec.note})
            for s in self.symbols:
                row = sig[s][sig[s]["ts"] == dts]
                if row.empty:
                    continue
                on = bool(row["on"].iloc[0]); q = L.qty(s); val = q * prices[s]
                target = self.weights[s] * (1.0 if on else 0.0) * dec.max_exposure * equity
                action, reason, order = "HOLD", "", None
                if dec.force_exit and q > 1e-12:
                    action, reason = "EXIT", "emergency_halt"; order = {"side": "SELL", "qty": None}
                elif (not on) and q > 1e-12:
                    action, reason = "EXIT", "trend_exit"; order = {"side": "SELL", "qty": None}
                elif on and dec.reduce_to is not None and q > 1e-12 and val - self.weights[s] * dec.reduce_to * equity > max(10, 0.01 * equity):
                    action, reason = "REDUCE", f"risk_reduce_to_{dec.reduce_to:.2f}"; order = {"side": "SELL", "qty": (val - self.weights[s] * dec.reduce_to * equity) / prices[s]}
                elif on and dec.max_exposure > 0 and not dec.data_fault and (target - val) > max(10, 0.05 * equity) and L.cash > 10:
                    action, reason = "BUY", "trend_entry" if q <= 1e-12 else "top_up"; order = {"side": "BUY", "amount": target - val}
                srec = {"signal_ts": str(close_t), "data_ts": str(bars[s][self.exec_tf]["ts"].iloc[-1] + pd.Timedelta(hours=1)), "symbol": s, "strategy": self.strategy, "close_1d": float(row["close"].iloc[0]), "sma": float(row["sma"].iloc[0]) if np.isfinite(row["sma"].iloc[0]) else None, "trend_on": on, "risk_state": dec.state, "max_exposure": dec.max_exposure, "equity": equity, "qty": q, "action": action, "reason": reason, "data_fault": dec.data_fault}
                self._log("signals", srec); out["signals"].append(srec)
                if order:
                    od = {"order_id": f"{s}-{close_t.strftime('%Y%m%d')}-{order['side']}", "symbol": s, "signal_ts": str(close_t), "exec_after": str(close_t), "reason": reason} | order
                    self._log("orders", od); self.state["pending"].append(od)
            self.state["last_daily_ts"] = str(dts)
            self._fill_pending(bars, L, out)
            self._log("nav", {"signal_ts": str(close_t), "equity": L.equity(prices), "cash": L.cash, "holdings": {s: L.qty(s) for s in self.symbols}, "exposure": L.exposure(prices), "risk_state": dec.state, "prices": prices})
        self._store_ledger(L)
        self.state["n_runs"] = int(self.state.get("n_runs", 0)) + 1; self.state["last_run_at"] = str(now); self.state["last_data_check"] = chk
        self._save_state()
        out |= {"equity_mark": L.equity(px_last), "cash": L.cash, "holdings": {s: L.qty(s) for s in self.symbols}, "risk_state": getattr(self.risk, "state", "NORMAL" if self.risk_mode == "none" else ("HALTED" if getattr(self.risk, "halted", False) else "NORMAL")), "pending": list(self.state["pending"]), "data_fault": data_fault}
        return out

    def _fill_pending(self, bars: dict, L: SpotLedger, out: dict) -> None:
        keep = []
        for od in sorted(self.state["pending"], key=lambda o: 0 if o["side"] == "SELL" else 1):
            s = od["symbol"]; e = bars[s][self.exec_tf]; m = e[e["ts"] >= pd.Timestamp(od["exec_after"])]
            if m.empty:
                keep.append(od); continue
            bar = m.iloc[0]; o = float(bar["open"]); t = bar["ts"]
            if od["side"] == "SELL":
                q = L.qty(s) if od.get("qty") is None else min(float(od["qty"]), L.qty(s))
                if q > 1e-12:
                    rec = L.sell(s, q, o, t, od["reason"]); frec = {"order_id": od["order_id"], "symbol": s, "side": "SELL", "qty": q, "price": rec["price"], "fee": rec["fee"], "fill_ts": str(t), "net_pnl": rec["net_pnl"], "reason": od["reason"]}
                    self._log("fills", frec); out["fills"].append(frec)
            else:
                amt = min(float(od["amount"]), L.cash * (1 - float(self.tc["position"].get("cash_buffer", 0.001)))); px = o * (1 + L.slippage_bps / 1e4) * (1 + L.fee_bps / 1e4); q = amt / px
                if q * o >= float(self.tc["position"].get("min_order_usdt", 10)):
                    lot = L.buy(s, q, o, t, od["reason"], 0.0, 0.0, 0.0); frec = {"order_id": od["order_id"], "symbol": s, "side": "BUY", "qty": q, "price": lot.price, "fee": L.trade_log[-1]["fee"], "fill_ts": str(t), "reason": od["reason"]}
                    self._log("fills", frec); out["fills"].append(frec)
        self.state["pending"] = keep

    def _ledger(self) -> SpotLedger:
        cost = self.tc["costs"]["round2"]
        L = SpotLedger(float(self.state["cash"]), float(cost["fee_bps"]), float(cost["slippage_bps"]))
        from ..spot.ledger import Lot
        for s, lots in self.state.get("lots", {}).items():
            L.lots[s] = [Lot(**l) for l in lots]
        L.fees_paid = float(self.state.get("fees_paid", 0.0)); L.realized = float(self.state.get("realized", 0.0)); L._seq = int(self.state.get("seq", 0))
        return L

    def _store_ledger(self, L: SpotLedger) -> None:
        from dataclasses import asdict
        self.state["cash"] = L.cash; self.state["holdings"] = {s: L.qty(s) for s in self.symbols}; self.state["lots"] = {s: [asdict(l) for l in ls if l.qty > 1e-15] for s, ls in L.lots.items()}
        self.state["fees_paid"] = L.fees_paid; self.state["realized"] = L.realized; self.state["seq"] = L._seq

    def replay(self, start: str, end: str | None = None, step: str = "1D") -> dict:
        """重放模式（标注为重放，不是前瞻）：从 start 起每日推进 now，严格只用 now 之前已收盘的 K 线。"""
        bars = self.load_bars()
        last = min(b[self.exec_tf]["ts"].iloc[-1] for b in bars.values()) + pd.Timedelta(hours=1)
        end_ts = min(pd.Timestamp(end, tz="UTC"), last) if end else last
        now = pd.Timestamp(start, tz="UTC"); n = 0
        while now <= end_ts:
            self.run_once(now=now, allow_backfill=False, bars=bars); n += 1; now += pd.Timedelta(step)
        return {"steps": n, "state": self.state, "chains": self.verify_all()}

    def verify_all(self) -> dict:
        return {p.stem: verify_chain(p) for p in self.dir.glob("*.jsonl")}
