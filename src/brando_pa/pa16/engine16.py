"""第十六阶段长期战役回测引擎（预登记 §4–§7）。只做 BTC 现货、无杠杆。
时间规则：第 t 日收盘后用 ≤ t 的价格与 ≤ t−1 的链上 / 成本数据决策；周线信号只在周日收盘更新；全部成交在第 t+1 日开盘。
“战役”：从空仓开始，战役资金 = 当时全部现金；分批买入；进入卖出阶段后不再买入（实现细节，未改变预登记规则含义）；持仓清零后战役结束。
每笔成交记录触发规则与当时的数值依据；每个战役记录建仓 / 退出依据、持仓时间、手续费、滑点与净利润。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..holding.engine import HoldResult
from ..spot.ledger import SpotLedger
from ..usdt.engine import liquidation_value

SYM = "BTCUSDT"
ENTRIES = ("A", "A0", "B", "C", "D")
EXITS = ("X1", "X2", "X3", "X4", "X5")
X1_LEVELS = (2.0, 2.5, 3.0, 3.5)
X2_LEVELS = (2.5, 3.0, 3.5, 4.0)
X5_MULTS = (2.0, 4.0, 8.0, 16.0)
D_LEVELS = (1.1, 1.0, 0.9, 0.8)
ARM_DAYS = 540


class CampaignBacktester:
    def __init__(self, d: pd.DataFrame, f: pd.DataFrame, entry: str, exit_: str, fee_bps: float, slip_bps: float, start, end=None, initial: float = 10000.0, c_confirm: np.ndarray | None = None, name: str | None = None):
        assert entry in ENTRIES and exit_ in EXITS
        self.d = d.reset_index(drop=True); self.f = f.reset_index(drop=True) if f.index.name != "ts" else f.reset_index(drop=True)
        self.entry, self.exit = entry, exit_; self.fee, self.slip = float(fee_bps), float(slip_bps); self.initial = float(initial)
        self.name = name or f"{entry}_{exit_}"
        ts = pd.DatetimeIndex(pd.to_datetime(self.d["ts"], utc=True)); self.ts = ts
        self.i0 = int(np.searchsorted(ts, pd.Timestamp(start, tz="UTC"))); self.i1 = int(np.searchsorted(ts, pd.Timestamp(end, tz="UTC"), side="right")) if end is not None else len(ts)
        self.open = self.d["open"].to_numpy(float); self.close = self.d["close"].to_numpy(float)
        g = lambda c: self.f[c].to_numpy() if c in self.f else np.full(len(ts), np.nan)
        self.r = g("r").astype(float); self.mvrv = g("mvrv").astype(float); self.wk = self.f["is_week_close"].fillna(False).to_numpy(bool)
        self.w_close, self.sma20w, self.sma40w, self.sma20w_4 = (g(c).astype(float) for c in ("w_close", "sma20w", "sma40w", "sma20w_4ago"))
        self.cconf = c_confirm if c_confirm is not None else np.zeros(len(ts), bool)
        self.L = SpotLedger(self.initial, self.fee, self.slip); self.pending: list[dict] = []
        self.fills: list[dict] = []; self.campaigns: list[dict] = []; self.nav: list[dict] = []; self.camp: dict | None = None

    # ---------- 工具 ----------
    def _ctx(self, i: int) -> dict:
        return {"date": str(self.ts[i].date()), "close": float(self.close[i]), "r_cost_mid": None if np.isnan(self.r[i]) else round(float(self.r[i]), 4), "mvrv": None if np.isnan(self.mvrv[i]) else round(float(self.mvrv[i]), 4),
                "weekly": {"w_close": None if np.isnan(self.w_close[i]) else float(self.w_close[i]), "sma20w": None if np.isnan(self.sma20w[i]) else float(self.sma20w[i]), "sma40w": None if np.isnan(self.sma40w[i]) else float(self.sma40w[i])} if self.wk[i] else None}

    def _new_camp(self, i: int, kind: str) -> dict:
        c = {"id": len(self.campaigns) + 1, "start_i": i, "start_ts": str(self.ts[i].date()), "trigger": kind, "capital": float(self.L.cash), "n_buys": 0, "last_buy_i": None, "paused": False, "armed_until": i + ARM_DAYS,
             "levels": set(), "selling": False, "peak_qty": 0.0, "x_done": set(), "x_armed": False, "x4_half": False, "buy_cost": 0.0, "buy_qty": 0.0, "proceeds": 0.0, "fees": 0.0, "slippage": 0.0,
             "first_buy": None, "last_sell": None, "entry_basis": [], "exit_basis": [], "status": "open"}
        self.campaigns.append(c); return c

    def _buy(self, i: int, frac: float, rule: str):
        c = self.camp; amt = frac * c["capital"]; self.pending.append({"side": "BUY", "amount": amt, "rule": rule, "ctx": self._ctx(i), "camp": c["id"]}); c["n_buys"] += 1; c["last_buy_i"] = i

    def _sell(self, i: int, qty: float, rule: str):
        self.pending.append({"side": "SELL", "qty": qty, "rule": rule, "ctx": self._ctx(i), "camp": self.camp["id"]}); self.camp["selling"] = True

    # ---------- 执行（第 i 日开盘） ----------
    def _execute(self, i: int):
        ts = self.ts[i]; ref = float(self.open[i])
        for o in sorted(self.pending, key=lambda o: 0 if o["side"] == "SELL" else 1):
            c = next(x for x in self.campaigns if x["id"] == o["camp"])
            if o["side"] == "SELL":
                q = min(float(o["qty"]), self.L.qty(SYM))
                if q <= 1e-12 or q * ref < 1.0:
                    continue
                rec = self.L.sell(SYM, q, ref, ts, o["rule"]); slip = (ref - rec["price"]) * q
                c["proceeds"] += rec["price"] * q - rec["fee"]; c["fees"] += rec["fee"]; c["slippage"] += slip; c["last_sell"] = str(ts.date()); c["exit_basis"].append({"rule": o["rule"], **o["ctx"]})
                self.fills.append({"ts": str(ts.date()), "campaign": c["id"], "side": "SELL", "qty": q, "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": slip, "rule": o["rule"], "basis": o["ctx"], "data_label": "历史结果（回测）"})
            else:
                amt = min(float(o["amount"]), self.L.cash * (1 - 0.001)); px = ref * (1 + self.slip / 1e4) * (1 + self.fee / 1e4)
                if amt < 10:
                    continue
                q = amt / px; lot = self.L.buy(SYM, q, ref, ts, o["rule"], 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]; slip = (lot.price - ref) * q
                c["buy_cost"] += lot.price * q + fee; c["buy_qty"] += q; c["fees"] += fee; c["slippage"] += slip; c["first_buy"] = c["first_buy"] or str(ts.date()); c["entry_basis"].append({"rule": o["rule"], **o["ctx"]})
                self.fills.append({"ts": str(ts.date()), "campaign": c["id"], "side": "BUY", "qty": q, "ref_price": ref, "fill_price": lot.price, "fee": fee, "slippage": slip, "rule": o["rule"], "basis": o["ctx"], "data_label": "历史结果（回测）"})
        self.pending = []
        if self.camp is not None:
            self.camp["peak_qty"] = max(self.camp["peak_qty"], self.L.qty(SYM))
            if self.camp["selling"] and self.L.qty(SYM) <= 1e-10:
                self.camp["status"] = "closed"; self.camp["end_ts"] = str(self.ts[i].date()); self.camp = None

    # ---------- 决策（第 i 日收盘） ----------
    def _entries(self, i: int):
        r = self.r[i]; e = self.entry; c = self.camp
        if c is not None and (c["selling"] or self.L.qty(SYM) > 0 and e in ("A0",)):
            return
        if np.isnan(r):
            return
        if c is None:
            if e == "D":
                if r <= D_LEVELS[0]:
                    c = self.camp = self._new_camp(i, "r<=1.1")
                else:
                    return
            elif r <= 1.0:
                c = self.camp = self._new_camp(i, "r<=1.0")
                if e == "A":
                    self._buy(i, 0.25, "A: 首次 r≤1.0 第 1 批"); return
                if e == "A0":
                    self._buy(i, 1.0, "A0: 首次 r≤1.0 一次性"); return
            else:
                return
        if e == "A" and c["n_buys"] < 4:
            if r > 1.2:
                c["paused"] = True
            elif c["paused"] and r <= 1.0:
                c["paused"] = False; self._buy(i, 0.25, f"A: 暂停后再次 r≤1.0 第 {c['n_buys'] + 1} 批")
            elif not c["paused"] and c["last_buy_i"] is not None and i - c["last_buy_i"] >= 14 and r <= 1.2:
                self._buy(i, 0.25, f"A: 间隔 14 天且 r≤1.2 第 {c['n_buys'] + 1} 批")
        elif e == "B":
            if c["n_buys"] == 0 and i > c["armed_until"]:
                c["status"] = "cancelled_unarmed"; self.camp = None; return
            if self.wk[i] and c["n_buys"] < 2 and not np.isnan(self.sma20w[i]) and not np.isnan(self.sma20w_4[i]):
                ok = self.w_close[i] > self.sma20w[i] and self.sma20w[i] >= self.sma20w_4[i]
                if ok and c["n_buys"] == 0:
                    c["first_buy_week_i"] = i; self._buy(i, 0.5, "B: 成本低估后周线 > 20 周 SMA 且 SMA 上行 第 1 批")
                elif ok and c["n_buys"] == 1 and i - c.get("first_buy_week_i", i) >= 28:
                    self._buy(i, 1.0, "B: 4 个周线后条件仍成立 第 2 批")            # 剩余全部（战役资金的 50%）
        elif e == "C":
            if i > c["armed_until"]:
                if c["n_buys"] == 0:
                    c["status"] = "cancelled_unarmed"; self.camp = None
                return
            if self.cconf[i] and c["n_buys"] < 3 and (c["last_buy_i"] is None or i - c["last_buy_i"] >= 7):
                self._buy(i, 1 / 3, f"C: 成本低估后支撑 / 回踩 / 收复确认（K 线量价代理）第 {c['n_buys'] + 1} 批")
        elif e == "D":
            for lv in D_LEVELS:
                if lv not in c["levels"] and r <= lv:
                    c["levels"].add(lv); self._buy(i, 0.25, f"D: r≤{lv} 层")

    def _exits(self, i: int):
        c = self.camp; held = self.L.qty(SYM) + sum(-o.get("qty", 0) for o in self.pending if o["side"] == "SELL")
        if c is None or held <= 1e-10:
            return
        x = self.exit; r = self.r[i]; peak = max(c["peak_qty"], self.L.qty(SYM))
        if x == "X1" and not np.isnan(r):
            q = 0.0
            for lv in X1_LEVELS:
                if lv not in c["x_done"] and r >= lv:
                    c["x_done"].add(lv); q += 0.25 * peak
            if q > 0:
                self._sell(i, min(q, held), f"X1: 成本比 ≥ {max(l for l in c['x_done'])}")
        elif x == "X2" and not np.isnan(self.mvrv[i]):
            q = 0.0
            for lv in X2_LEVELS:
                if lv not in c["x_done"] and self.mvrv[i] >= lv:
                    c["x_done"].add(lv); q += 0.25 * peak
            if q > 0:
                self._sell(i, min(q, held), f"X2: MVRV ≥ {max(l for l in c['x_done'])}")
        elif x in ("X3", "X4") and self.wk[i] and not np.isnan(self.sma40w[i]):
            if self.w_close[i] > self.sma40w[i]:
                c["x_armed"] = True
            if c["x_armed"]:
                if self.w_close[i] < self.sma40w[i]:
                    self._sell(i, held, f"{x}: 周线收盘 < 40 周 SMA")
                elif x == "X4" and not c["x4_half"] and self.w_close[i] < self.sma20w[i]:
                    c["x4_half"] = True; self._sell(i, 0.5 * held, "X4: 周线收盘 < 20 周 SMA 卖 50%")
        elif x == "X5" and c["buy_qty"] > 0:
            avg = c["buy_cost"] / c["buy_qty"]
            for m in X5_MULTS:
                if m not in c["x_done"] and self.close[i] >= m * avg:
                    c["x_done"].add(m); self._sell(i, 0.2 * held, f"X5: 价格 ≥ 均价 × {m:g}"); held *= 0.8

    def run(self) -> HoldResult:
        assert self.i0 < self.i1, "窗口内无数据"
        for i in range(self.i0, self.i1):
            if self.pending:
                self._execute(i)
            cl = {SYM: float(self.close[i])}
            if self.camp is not None and self.L.qty(SYM) > 0:
                self._exits(i)
            if self.camp is None or not self.camp["selling"]:
                self._entries(i)
            eq = self.L.equity(cl); liq = liquidation_value(self.L, cl); self._peak = max(getattr(self, "_peak", self.initial), eq); peak = self._peak
            self.nav.append({"ts": self.ts[i], "equity": eq, "liquidation": liq, "cash": self.L.cash, "exposure": self.L.exposure(cl), "drawdown": eq / peak - 1, "close": float(self.close[i])})
        nav = pd.DataFrame(self.nav); last = float(self.close[self.i1 - 1]); cl = {SYM: last}
        rows = []
        for c in self.campaigns:
            if c["n_buys"] == 0 or c["buy_qty"] == 0:
                continue
            open_q = c["buy_qty"] - sum(f["qty"] for f in self.fills if f["campaign"] == c["id"] and f["side"] == "SELL")
            mark = open_q * last * (1 - self.slip / 1e4) * (1 - self.fee / 1e4) if open_q > 1e-10 else 0.0
            end = c.get("end_ts") or str(self.ts[self.i1 - 1].date()); hold = (pd.Timestamp(end) - pd.Timestamp(c["first_buy"])).days if c["first_buy"] else 0
            sells = [f for f in self.fills if f["campaign"] == c["id"] and f["side"] == "SELL"]
            rows.append({"campaign": c["id"], "trigger_date": c["start_ts"], "first_buy": c["first_buy"], "last_sell": c["last_sell"], "end": end, "status": "closed" if c["status"] == "closed" else "open_at_end",
                         "n_buys": sum(1 for f in self.fills if f["campaign"] == c["id"] and f["side"] == "BUY"), "n_sells": len(sells), "avg_entry": c["buy_cost"] / c["buy_qty"],
                         "avg_exit": (sum(f["fill_price"] * f["qty"] for f in sells) / sum(f["qty"] for f in sells)) if sells else None, "qty": c["buy_qty"], "invested_usdt": c["buy_cost"],
                         "proceeds_usdt": c["proceeds"], "open_qty_at_end": open_q, "marked_liquidation_usdt": mark, "fees_usdt": c["fees"], "slippage_usdt": c["slippage"],
                         "net_pnl": c["proceeds"] + mark - c["buy_cost"], "hold_days": hold, "reason": c["exit_basis"][-1]["rule"] if c["exit_basis"] else "未退出（期末按清仓估值）",
                         "entry_basis": c["entry_basis"], "exit_basis": c["exit_basis"]})
        camps = pd.DataFrame(rows); closed = camps[camps["status"] == "closed"] if len(camps) else camps
        fills = pd.DataFrame(self.fills); buy_fees = float(fills.loc[fills.side == "BUY", "fee"].sum()) if len(fills) else 0.0
        eqf = float(nav["equity"].iloc[-1]); liq = float(nav["liquidation"].iloc[-1]); unreal = float(self.L.unrealized(cl)); realized = float(self.L.realized) - buy_fees
        final = {"end_ts": str(nav["ts"].iloc[-1]), "equity": eqf, "cash": float(self.L.cash), "holdings": {SYM: self.L.qty(SYM)}, "realized_net_usdt": realized, "unrealized_usdt": unreal, "buy_fees_usdt": buy_fees,
                 "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - self.initial, "equity_profit_usdt": eqf - self.initial, "fees_usdt": float(self.L.fees_paid), "slippage_usdt": float(fills["slippage"].sum()) if len(fills) else 0.0,
                 "hypothetical_exit_cost_usdt": eqf - liq, "n_fills": int(len(fills)), "n_round_trips": int(len(closed)), "open_positions": int(self.L.qty(SYM) > 1e-10),
                 "identity_ok": abs((eqf - self.initial) - (realized + unreal)) < 1e-6 * max(1.0, eqf), "campaigns": camps}
        trades = closed.rename(columns={"campaign": "id"})[["hold_days", "net_pnl", "reason"]] if len(closed) else pd.DataFrame(columns=["hold_days", "net_pnl", "reason"])
        return HoldResult(self.name, {"entry": self.entry, "exit": self.exit}, self.fee, self.slip, self.initial, nav, trades, fills, final)
