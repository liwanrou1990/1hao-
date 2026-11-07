"""第七阶段前瞻模拟：PortfolioAllocator 驱动的 BTC/ETH/USDT 模拟盘（只读行情、追加 JSONL + 哈希链、挂单恢复）。复用第六阶段 TrendSysPaper 的数据检查、补齐、成交、状态与记录机制。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import cfg
from ..data.sources import utcnow
from ..trendsys.paper import TrendSysPaper
from ..trendsys.trend_layer import trend_signal
from .allocator import PortfolioAllocator
from .engine import liquidation_value


class UsdtPaper(TrendSysPaper):
    def __init__(self, c: dict | None = None, log_dir: Path | None = None, model: str | None = None, risk_mode: str | None = None, store=None, live: bool = False):
        c = c or cfg(); uc = c["usdt"]
        super().__init__(c, log_dir or Path(uc["paper"]["log_dir"]), "A_sma200", risk_mode or uc["paper"]["risk_mode"], list(uc["symbols"]), store, live)
        self.uc = uc; self.model = model or uc["paper"]["model"]; self.confirm = int(uc["trend"]["confirm_days"]); self.sma = int(uc["trend"]["sma"])
        self.alloc = PortfolioAllocator(uc["models"][self.model], self.symbols, uc["allocator"], float(uc["costs"]["round2"]["fee_bps"]), float(uc["costs"]["round2"]["slippage_bps"]))
        self.alloc.last_target = self.state.get("alloc_last_target"); self.state["model"] = self.model

    def run_once(self, now: pd.Timestamp | None = None, allow_backfill: bool = True, bars: dict | None = None) -> dict:
        now = now or utcnow(); bars = bars or self.load_bars(); chk = self.data_check(bars, now)
        if not chk["ok"] and allow_backfill:
            bf = self.backfill(); bars = self.load_bars(); chk = self.data_check(bars, now); chk["backfill"] = bf
        data_fault = not chk["ok"]; self.risk.set_data_fault(data_fault, "; ".join(chk["issues"])[:300])
        sec = {"1d": 86400, "1h": 3600, "4h": 14400}
        bars = {s: {tf: b[tf][(b[tf]["ts"] + pd.Timedelta(seconds=sec[tf])) <= now].reset_index(drop=True) for tf in b} for s, b in bars.items()}
        ex = bars[self.symbols[0]][self.exec_tf]
        if ex.empty:
            self._log("failures", {"kind": "no_exec_bars", "now": str(now)}); return {"status": "no_data"}
        sig = {s: trend_signal(bars[s]["1d"], self.sma, self.confirm) for s in self.symbols}; d1_ts = sig[self.symbols[0]]["ts"]
        last_done = pd.Timestamp(self.state["last_daily_ts"]) if self.state.get("last_daily_ts") else None
        new_days = d1_ts[d1_ts > last_done] if last_done is not None else d1_ts.tail(1)
        out = {"now": str(now), "data_check": chk, "new_daily_bars": int(len(new_days)), "fills": [], "signals": []}
        L = self._ledger(); self._fill_pending(bars, L, out)
        for dts in new_days:
            close_t = dts + pd.Timedelta(days=1); prices = {}; drops = []; on = {}
            for s in self.symbols:
                e = bars[s][self.exec_tf]; m = e[(e["ts"] + pd.Timedelta(seconds=sec[self.exec_tf])) <= close_t]; d1 = bars[s]["1d"]; k = d1.index[d1["ts"] == dts]
                prices[s] = float(m["close"].iloc[-1]) if len(m) else float(d1.loc[k[0], "close"])
                drops.append(float(d1["close"].iloc[k[0]] / d1["close"].iloc[k[0] - 1] - 1) if len(k) and k[0] >= 1 else 0.0)
                row = sig[s][sig[s]["ts"] == dts]; on[s] = bool(row["on"].iloc[0]) if len(row) else False
            equity = L.equity(prices); expo = L.exposure(prices)
            dec = self.risk.evaluate(close_t, equity, min(drops), expo)
            if dec.data_fault:
                dec.max_exposure = min(dec.max_exposure, expo)
            self._log("risk_state", {"signal_ts": str(close_t), "state": dec.state, "max_exposure": dec.max_exposure, "force_exit": dec.force_exit, "dd_ref": dec.dd_ref, "dd_all": dec.dd_all, "equity": equity, "data_fault": dec.data_fault, "note": dec.note})
            a = self.alloc.allocate(on, {s: L.qty(s) for s in self.symbols}, L.cash, prices, dec, equity)
            srec = {"signal_ts": str(close_t), "data_ts": str(ex["ts"].iloc[-1] + pd.Timedelta(seconds=sec[self.exec_tf])), "model": self.model, "trend_on": on, "risk_state": dec.state, "max_exposure": dec.max_exposure, "equity": equity, "usdt": L.cash, "holdings": {s: L.qty(s) for s in self.symbols}, "prices": prices,
                    "target_weights": a.target_weights, "cur_weights": a.inputs["cur_weights"], "rebalance": a.rebalance, "reason": a.reason, "est_cost": a.est_cost_usdt, "data_fault": dec.data_fault}
            self._log("signals", srec); out["signals"].append(srec)
            for k_, o in enumerate(sorted(a.orders, key=lambda o: 0 if o["side"] == "SELL" else 1)):
                od = {"order_id": f"{o['symbol']}-{close_t.strftime('%Y%m%d')}-{o['side']}-{k_}", "symbol": o["symbol"], "signal_ts": str(close_t), "exec_after": str(close_t), "reason": o["reason"], "side": o["side"], "qty": o.get("qty"), "amount": o.get("amount"), "est_cost": o.get("est_cost")}
                self._log("orders", od); self.state["pending"].append(od)
            self.state["last_daily_ts"] = str(dts); self._fill_pending(bars, L, out)
            self._log("nav", {"signal_ts": str(close_t), "equity": L.equity(prices), "liquidation_usdt": liquidation_value(L, prices), "cash": L.cash, "holdings": {s: L.qty(s) for s in self.symbols}, "weights": {s: L.qty(s) * prices[s] / max(L.equity(prices), 1e-9) for s in self.symbols}, "exposure": L.exposure(prices), "risk_state": dec.state, "prices": prices})
        self._store_ledger(L); self.state["alloc_last_target"] = self.alloc.last_target
        self.state["n_runs"] = int(self.state.get("n_runs", 0)) + 1; self.state["last_run_at"] = str(now); self.state["last_data_check"] = chk; self._save_state()
        px_last = {s: float(bars[s][self.exec_tf]["close"].iloc[-1]) for s in self.symbols}
        out |= {"equity_mark": L.equity(px_last), "liquidation_usdt": liquidation_value(L, px_last), "cash": L.cash, "holdings": {s: L.qty(s) for s in self.symbols}, "risk_state": getattr(self.risk, "state", "NORMAL"), "pending": list(self.state["pending"]), "data_fault": data_fault}
        return out
