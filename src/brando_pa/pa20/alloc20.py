"""第二十阶段固定 BTC/USDT 配置引擎与统一指标（预登记 §2、§5）。
配置：权重 w ∈ {0, 0.25, 0.5, 0.75, 1}；维护方式 N（不再平衡）/ Y（每年 1 月第一个交易日开盘再平衡）/ D（日收盘偏离 > 10 个百分点，次日开盘再平衡）。
窗口首日收盘决策、次日开盘建仓（与其他引擎一致）；USDT 不计利息；成交走 ImpactLedger（数量精度、最小订单、平方根冲击）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..holding.engine import HoldResult
from .execution20 import MIN_NOTIONAL, final_liquidation_with_impact, make_ledger

SYM = "BTCUSDT"
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
MODES = {"N": "不再平衡", "Y": "年度再平衡", "D": "偏离 10 个百分点再平衡"}
WARN = (0.35, 0.40, 0.45, 0.50)


class AllocBacktester:
    def __init__(self, bars: pd.DataFrame, weight: float, mode: str, fee_bps: float, slip_bps: float, start, end, initial: float, liq: dict):
        b = bars.copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.sort_values("ts").reset_index(drop=True)
        ts = pd.DatetimeIndex(b["ts"]); self.i0 = int(np.searchsorted(ts, pd.Timestamp(start, tz="UTC"))); self.i1 = int(np.searchsorted(ts, pd.Timestamp(end, tz="UTC"), side="right")) if end is not None else len(ts)
        self.ts, self.o, self.c = ts, b["open"].to_numpy(float), b["close"].to_numpy(float); self.w = float(weight); self.mode = mode; self.initial = float(initial)
        self.L = make_ledger(initial, fee_bps, slip_bps, liq); self.fills: list[dict] = []; self.nav: list[dict] = []; self.name = f"ALLOC_{int(weight * 100)}_{mode}"

    def _rebalance(self, i, reason):
        ref = float(self.o[i]); eq = self.L.equity({SYM: ref}); cur = self.L.qty(SYM) * ref; diff = self.w * eq - cur
        if abs(diff) < MIN_NOTIONAL:
            return
        if diff > 0:
            amt = min(diff, self.L.cash * 0.999); q = amt / (ref * (1 + self.L.slippage_bps / 1e4) * (1 + self.L.fee_bps / 1e4))
            if q * ref < MIN_NOTIONAL:
                return
            lot = self.L.buy(SYM, q, ref, self.ts[i], reason, 0.0, 0.0, 0.0); fee = self.L.trade_log[-1]["fee"]; q = lot.qty
            self.fills.append({"ts": self.ts[i], "symbol": SYM, "side": "BUY", "qty": q, "ref_price": ref, "fill_price": lot.price, "fee": fee, "slippage": (lot.price - ref) * q, "reason": reason})
        else:
            q = min(-diff / ref, self.L.qty(SYM))
            if q * ref < MIN_NOTIONAL:
                return
            rec = self.L.sell(SYM, q, ref, self.ts[i], reason)
            self.fills.append({"ts": self.ts[i], "symbol": SYM, "side": "SELL", "qty": rec["qty"], "ref_price": ref, "fill_price": rec["price"], "fee": rec["fee"], "slippage": (ref - rec["price"]) * rec["qty"], "reason": reason})

    def run(self) -> HoldResult:
        pending = None
        for i in range(self.i0, self.i1):
            if i == self.i0 + 1 and self.w > 0:                                              # 与其他引擎一致：窗口首日收盘决策，次日开盘建仓
                self._rebalance(i, "建仓（窗口首日收盘决策，次日开盘）")
            elif pending:
                self._rebalance(i, pending); pending = None
            cl = float(self.c[i]); eq = self.L.equity({SYM: cl}); wa = self.L.qty(SYM) * cl / eq if eq > 0 else 0.0
            if self.w > 0 and i > self.i0 and i + 1 < self.i1:
                if self.mode == "Y" and self.ts[i + 1].year != self.ts[i].year:
                    pending = "年度再平衡"
                elif self.mode == "D" and abs(wa - self.w) > 0.10:
                    pending = f"偏离再平衡（实际 {wa:.0%}）"
            self.nav.append({"ts": self.ts[i], "equity": eq, "liquidation": self.L.cash + self.L.qty(SYM) * cl * (1 - self.L.base_slip / 1e4) * (1 - self.L.fee_bps / 1e4), "exposure": wa, "holdings": {SYM: self.L.qty(SYM)}})
        nav = pd.DataFrame(self.nav); fills = pd.DataFrame(self.fills); last = float(self.c[self.i1 - 1])
        liq_imp = final_liquidation_with_impact(self.L, {SYM: last}, self.ts[self.i1 - 1])
        final = {"liquidation_usdt": liq_imp, "liquidation_net_profit_usdt": liq_imp - self.initial, "equity": float(nav["equity"].iloc[-1]), "fees_usdt": float(self.L.fees_paid), "n_fills": int(len(fills))}
        return HoldResult(self.name, {"assets": [SYM], "weight": self.w, "mode": self.mode}, self.L.fee_bps, self.L.base_slip, self.initial, nav, pd.DataFrame(columns=["hold_days", "net_pnl", "reason"]), fills, final)


def metrics(res, L=None, final_liq: float | None = None) -> dict:
    """统一指标（清仓口径；期末清仓含冲击）。L：所用 ImpactLedger（用于冲击分项）。"""
    nav = res.nav.copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True); liq = nav.set_index("ts")["liquidation"].astype(float)
    if final_liq is not None:
        liq.iloc[-1] = final_liq
    initial = float(res.initial); fin = float(liq.iloc[-1]); days = max((liq.index[-1] - liq.index[0]).days, 1); yrs = days / 365.25
    dd = liq / liq.cummax() - 1; mdd = float(dd.min())
    longest, cur = 0, None
    for t, v in dd.items():
        if v < -1e-12:
            cur = cur or t
        elif cur is not None:
            longest = max(longest, (t - cur).days); cur = None
    if cur is not None:
        longest = max(longest, (dd.index[-1] - cur).days + 1)
    cagr = (fin / initial) ** (1 / yrs) - 1 if fin > 0 else -1.0; vol = float(np.log(liq).diff().std() * np.sqrt(365))
    warn = {}
    for th in WARN:
        hit = dd[dd <= -th]; warn[f"{int(th * 100)}%"] = {"hit": bool(len(hit)), "first_date": str(hit.index[0].date()) if len(hit) else None, "days": int(len(hit))}
    fills = res.fills; imp = getattr(L, "impacts", []) if L is not None else []
    imp_usdt = float(sum(x["impact_usdt"] for x in imp)); slip_total = float(fills["slippage"].sum()) if len(fills) else 0.0
    return {"initial_usdt": initial, "final_usdt": fin, "net_profit_usdt": fin - initial, "max_drawdown": mdd, "longest_drawdown_days": int(longest), "cagr": float(cagr), "ann_vol": vol, "return_over_mdd": float(cagr / abs(mdd)) if mdd < 0 else None,
            "within_50pct_budget": mdd >= -0.50, "warnings": warn, "avg_btc_exposure": float(nav["exposure"].mean()), "n_fills": int(len(fills)), "fees_usdt": float(L.fees_paid) if L is not None else None,
            "base_slippage_usdt": slip_total - imp_usdt, "impact_usdt": imp_usdt, "total_cost_usdt": (float(L.fees_paid) if L is not None else 0.0) + slip_total,
            "max_participation": max([x["participation"] for x in imp if x["participation"] is not None], default=None), "n_fills_no_volume_data": int(sum(1 for x in imp if x["participation"] is None)),
            "n_fills_over_participation_limit": int(sum(1 for x in imp if x["flag"].startswith("超出"))), "start": str(liq.index[0].date()), "end": str(liq.index[-1].date())}
