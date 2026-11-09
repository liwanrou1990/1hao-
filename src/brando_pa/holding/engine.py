"""第十阶段日线持仓回测引擎（独立；复用 SpotLedger / trend_signal / liquidation_value / CircuitBreaker）。

时间规则（无未来函数）：第 t 日收盘后形成决策（只用 ≤ t 的收盘），第 t+1 日开盘价成交；净值按每日收盘标记。
策略规格 spec：
  assets: ["BTCUSDT"] | ["ETHUSDT"] | ["BTCUSDT", "ETHUSDT"]
  mode: "trend" | "fixed" | "minhold" | "buyhold"
  min_hold: int      持有不足 min_hold 个交易日时忽略 trend_off（fixed / minhold）
  max_hold: int|None 持有达到 max_hold 日 → 次日开盘退出（fixed）
  risk_exit_dd: float|None  收盘 ≤ 入场成交价 × (1 − dd) → 次日开盘退出
  risk_mode: "none" | "state_machine"   （基准 B 日线复现：CircuitBreaker 按日收盘权益评估）
分配：等权分给当前应持有的资产；只在持有集合（或风险限仓）变化时调仓（先卖后买）；现金缓冲 0.1%；最小下单 10 USDT。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..spot.ledger import SpotLedger
from ..trendsys.risk_layer import make_risk
from ..trendsys.trend_layer import trend_signal
from ..usdt.engine import liquidation_value

SYMS = ["BTCUSDT", "ETHUSDT"]


@dataclass
class Position:
    entry_ts: pd.Timestamp
    entry_price: float            # 首笔成交价（含滑点）
    entry_fee: float = 0.0
    days_held: int = 0


@dataclass
class HoldResult:
    name: str
    spec: dict
    fee_bps: float
    slip_bps: float
    initial: float
    nav: pd.DataFrame             # ts, equity, liquidation, cash, exposure, drawdown, risk_state, holdings, closes
    trades: pd.DataFrame          # 闭合/部分卖出：symbol, entry_ts, exit_ts, entry_price, exit_price, qty, hold_days, gross_pnl, fees, net_pnl, reason, partial
    fills: pd.DataFrame
    final: dict = field(default_factory=dict)


class HoldingBacktester:
    def __init__(self, bars: dict, spec: dict, fee_bps: float, slip_bps: float, start, end, initial: float = 10000.0, sma: int = 200, risk_params: dict | None = None):
        self.spec = spec; self.fee, self.slip = float(fee_bps), float(slip_bps); self.initial = float(initial)
        self.start = pd.Timestamp(start, tz="UTC"); self.end = pd.Timestamp(end, tz="UTC") if end is not None else None
        self.assets = list(spec["assets"]); self.sma = int(sma)
        idx = None
        for s in self.assets:
            t = pd.DatetimeIndex(pd.to_datetime(bars[s]["ts"], utc=True)); idx = t if idx is None else idx.intersection(t)
        self.idx = idx.sort_values()
        self.px = {}; self.sig = {}
        for s in self.assets:
            b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.set_index("ts").loc[self.idx]
            self.px[s] = b
            sg = trend_signal(pd.DataFrame({"ts": self.idx, "close": b["close"].to_numpy(float)}), self.sma, 1)
            self.sig[s] = sg["on"].to_numpy(bool)
        self.open = {s: self.px[s]["open"].to_numpy(float) for s in self.assets}; self.close = {s: self.px[s]["close"].to_numpy(float) for s in self.assets}
        self.risk = make_risk(spec.get("risk_mode", "none"), risk_params or {})
        self.L = SpotLedger(self.initial, self.fee, self.slip); self.pos: dict[str, Position] = {}
        self.trades = []; self.fills = []; self.nav_rows = []; self.pending: list[dict] = []

    # ---------- 决策 ----------
    def _want(self, i: int) -> tuple[set, dict]:
        """第 i 日收盘后：应持有的资产集合与退出原因。"""
        mode = self.spec["mode"]; want = set(); reasons = {}
        for s in self.assets:
            on = bool(self.sig[s][i]); close = float(self.close[s][i]); p = self.pos.get(s)
            if mode == "buyhold":
                want.add(s); continue
            if p is None:
                if on:
                    want.add(s)
                continue
            dd = self.spec.get("risk_exit_dd")
            if dd is not None and close <= p.entry_price * (1 - float(dd)):
                reasons[s] = "risk_exit"; continue
            mx = self.spec.get("max_hold")
            if mx is not None and p.days_held >= int(mx):
                reasons[s] = "max_hold"; continue
            mn = int(self.spec.get("min_hold", 0) or 0)
            if not on and p.days_held >= mn:
                reasons[s] = "trend_off"; continue
            want.add(s)
        return want, reasons

    def _orders(self, i: int, eq: float, cl: dict, want: set, reasons: dict, dec) -> list[dict]:
        orders = []
        if dec.force_exit:
            for s in list(self.pos):
                reasons[s] = "risk_state_machine_halt"
            want = set()
        for s in list(self.pos):
            if s not in want:
                orders.append({"symbol": s, "side": "SELL", "reason": reasons.get(s, "exit")})
        held = sorted(want)
        if not held:
            return orders
        cap = max(0.0, min(1.0, float(dec.max_exposure)))
        if dec.reduce_to is not None:
            cap = min(cap, float(dec.reduce_to))
        w = cap / len(held); thr = max(10.0, 0.005 * eq)
        set_changed = set(held) != set(self.pos) or (dec.changed and dec.reduce_to is not None)
        for s in held:
            cur_v = self.L.qty(s) * cl[s]; tgt_v = w * eq
            if s not in self.pos:
                if cap > 0 and tgt_v >= 10:
                    orders.append({"symbol": s, "side": "BUY", "amount": tgt_v, "reason": "entry"})
            elif set_changed and tgt_v > cur_v + thr:
                orders.append({"symbol": s, "side": "BUY", "amount": tgt_v - cur_v, "reason": "rebalance_up"})
            elif set_changed and cur_v > tgt_v + thr:
                orders.append({"symbol": s, "side": "SELL", "partial_qty": (cur_v - tgt_v) / cl[s], "reason": "risk_reduce" if (dec.reduce_to is not None and dec.changed) else "rebalance_down"})
        return orders

    # ---------- 执行 ----------
    def _execute(self, i: int, orders: list[dict]) -> None:
        """第 i 日开盘执行上一日决策（先卖后买；买入金额以卖出后的现金为上限）。"""
        ts = self.idx[i]
        for o in sorted(orders, key=lambda o: 0 if o["side"] == "SELL" else 1):
            s = o["symbol"]; ref = float(self.open[s][i])
            if o["side"] == "SELL":
                held = self.L.qty(s); q = min(float(o["partial_qty"]), held) if o.get("partial_qty") is not None else held
                if q <= 1e-12 or q * ref < 1.0:
                    continue
                rec = self.L.sell(s, q, ref, ts, o["reason"]); p = self.pos[s]; partial = o.get("partial_qty") is not None and self.L.qty(s) > 1e-12
                efee = 0.0 if partial else p.entry_fee
                self.fills.append({"ts": ts, "symbol": s, "side": "SELL", "qty": q, "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (ref - rec["price"]) * q, "reason": o["reason"]})
                self.trades.append({"symbol": s, "entry_ts": p.entry_ts, "exit_ts": ts, "entry_price": p.entry_price, "exit_price": rec["price"], "qty": q, "hold_days": p.days_held, "gross_pnl": rec["gross_pnl"], "fees": rec["fee"] + efee, "net_pnl": rec["net_pnl"] - efee, "reason": o["reason"], "partial": partial})
                if not partial:
                    self.pos.pop(s)
            else:
                amt = min(float(o["amount"]), self.L.cash * (1 - 0.001)); px = ref * (1 + self.slip / 1e4) * (1 + self.fee / 1e4); q = amt / px
                if q <= 0 or q * ref < 10:
                    continue
                lot = self.L.buy(s, q, ref, ts, o["reason"], 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]
                self.fills.append({"ts": ts, "symbol": s, "side": "BUY", "qty": q, "ref_price": ref, "fill_price": lot.price, "fee": fee, "slippage": (lot.price - ref) * q, "reason": o["reason"]})
                if s in self.pos:
                    self.pos[s].entry_fee += fee
                else:
                    self.pos[s] = Position(ts, lot.price, fee, 0)

    def run(self) -> HoldResult:
        n = len(self.idx); i0 = int(np.searchsorted(self.idx, self.start)); i1 = int(np.searchsorted(self.idx, self.end, side="right")) if self.end is not None else n
        assert i0 < i1 <= n, "窗口内无数据"
        prev_close = None
        for i in range(i0, i1):
            ts = self.idx[i]
            if self.pending:
                self._execute(i, self.pending); self.pending = []
            cl = {s: float(self.close[s][i]) for s in self.assets}; eq = self.L.equity(cl)
            for p in self.pos.values():
                p.days_held += 1
            drop = min((cl[s] / prev_close[s] - 1) for s in self.assets) if prev_close else 0.0
            dec = self.risk.evaluate(ts, eq, drop, self.L.exposure(cl))
            want, reasons = self._want(i)
            self.pending = self._orders(i, eq, cl, want, reasons, dec)
            liq = liquidation_value(self.L, cl); peak = max([r["equity"] for r in self.nav_rows] + [eq, self.initial])
            self.nav_rows.append({"ts": ts, "equity": eq, "liquidation": liq, "cash": self.L.cash, "exposure": self.L.exposure(cl), "drawdown": eq / peak - 1, "risk_state": dec.state, "holdings": {s: self.L.qty(s) for s in self.assets}, "closes": cl})
            prev_close = cl
        nav = pd.DataFrame(self.nav_rows); last = nav.iloc[-1]; cl = last["closes"]
        buy_fees = float(sum(f["fee"] for f in self.fills if f["side"] == "BUY")); fees = float(self.L.fees_paid); slippage = float(sum(f["slippage"] for f in self.fills)) if self.fills else 0.0
        realized = float(self.L.realized) - buy_fees                      # 已实现净额 = 卖出净盈亏（含卖出费）− 全部买入手续费（买入费在成交时已付）
        unreal = float(self.L.unrealized(cl)); liq = float(last["liquidation"]); eqf = float(last["equity"])
        final = {"end_ts": str(last["ts"]), "equity": eqf, "cash": float(self.L.cash), "holdings": {s: self.L.qty(s) for s in self.assets}, "realized_net_usdt": realized, "unrealized_usdt": unreal, "buy_fees_usdt": buy_fees,
                 "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - self.initial, "equity_profit_usdt": eqf - self.initial, "fees_usdt": fees, "slippage_usdt": slippage, "hypothetical_exit_cost_usdt": eqf - liq,
                 "n_fills": len(self.fills), "n_round_trips": int(sum(1 for t in self.trades if not t.get("partial"))), "open_positions": len(self.pos),
                 "identity_ok": abs((eqf - self.initial) - (realized + unreal)) < 1e-6 * max(1.0, eqf)}
        return HoldResult(self.spec.get("name", "?"), self.spec, self.fee, self.slip, self.initial, nav, pd.DataFrame(self.trades), pd.DataFrame(self.fills), final)
