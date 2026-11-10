"""第二十阶段执行模型（预登记 §1）：数量精度、最小订单、平方根流动性冲击。
ImpactLedger 继承 SpotLedger：每笔成交按当日（截至前一日的波动率、当日成交额）计算冲击并临时加到滑点上；其余记账逻辑完全沿用父类。
冲击 = Y × σ × √(订单金额 ÷ 当日计价成交额)，Y = 1.0，上限 100 bps；无成交量数据（2015-07-19 之前）按 25 bps 并标记。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..spot.ledger import SpotLedger

STEP = {"BTCUSDT": 1e-5, "ETHUSDT": 1e-4}
MIN_NOTIONAL = 5.0
Y = 1.0
CAP_BPS = 100.0
FALLBACK_BPS = 25.0
PARTICIPATION_FLAG = 0.01


def liquidity_table(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.set_index("ts").sort_index()
    c = b["close"].astype(float); lr = np.log(c).diff()
    qv = (b["volume"].astype(float) * c).where(b["volume"].astype(float) > 0)
    return pd.DataFrame({"quote_vol": qv, "sigma": lr.rolling(20, min_periods=10).std().shift(1)})


class ImpactLedger(SpotLedger):
    def setup(self, base_slip_bps: float, liq: dict):
        self.base_slip = float(base_slip_bps); self.liq = {s: t for s, t in liq.items()}; self.impacts: list[dict] = []
        return self

    def _impact(self, symbol, ts, notional) -> float:
        t = pd.Timestamp(ts); t = t.tz_localize("UTC") if t.tzinfo is None else t; t = t.normalize(); tab = self.liq.get(symbol)
        row = tab.loc[t] if (tab is not None and t in tab.index) else None
        qv = float(row["quote_vol"]) if row is not None and np.isfinite(row["quote_vol"]) else None; sg = float(row["sigma"]) if row is not None and np.isfinite(row["sigma"]) else None
        if qv is None or sg is None:
            bps, part, flag = FALLBACK_BPS, None, "无成交量数据：按 25 bps 保守冲击"
        else:
            part = notional / qv; bps = min(CAP_BPS, 1e4 * Y * sg * math.sqrt(part)); flag = "超出流动性参与上限（> 日成交额 1%）" if part > PARTICIPATION_FLAG else ""
        self.impacts.append({"ts": str(t.date()), "symbol": symbol, "notional": notional, "impact_bps": bps, "participation": part, "flag": flag, "impact_usdt": notional * bps / 1e4})
        return bps

    def buy(self, symbol, qty, ref_price, ts, strategy, stop, atr, risk_usd):
        step = STEP.get(symbol, 1e-8); qty = math.floor(qty / step) * step
        imp = self._impact(symbol, ts, qty * ref_price); self.slippage_bps = self.base_slip + imp
        px = ref_price * (1 + self.slippage_bps / 1e4) * (1 + self.fee_bps / 1e4)
        if qty * px > self.cash:
            qty = math.floor(self.cash / px / step) * step
        try:                                                                             # 最小订单：各引擎在调用前已拒绝 < 10 USDT 的订单（比交易所 5 USDT 更严）
            return super().buy(symbol, qty, ref_price, ts, strategy, stop, atr, risk_usd)
        finally:
            self.slippage_bps = self.base_slip

    def sell(self, symbol, qty, ref_price, ts, reason, lot_ids=None):
        held = self.qty(symbol); step = STEP.get(symbol, 1e-8)
        qty = held if qty >= held * 0.9999 else math.floor(qty / step) * step
        qty = min(qty, held)
        imp = self._impact(symbol, ts, qty * ref_price); self.slippage_bps = self.base_slip + imp
        try:
            return super().sell(symbol, qty, ref_price, ts, reason, lot_ids)
        finally:
            self.slippage_bps = self.base_slip


def make_ledger(initial: float, fee_bps: float, slip_bps: float, liq: dict) -> ImpactLedger:
    return ImpactLedger(initial, fee_bps, slip_bps).setup(slip_bps, liq)


def attach(bt, liq: dict):
    """把任一回测引擎的账本换成 ImpactLedger（在 run() 之前调用）。"""
    bt.L = make_ledger(bt.L.cash, bt.L.fee_bps, bt.L.slippage_bps, liq); return bt


def final_liquidation_with_impact(L: ImpactLedger, prices: dict, ts) -> float:
    """期末假设清仓（含当日冲击）。只计算，不改账本。"""
    v = L.cash
    for s in list(L.lots):
        q = L.qty(s)
        if q > 0:
            n = q * prices[s]; t = pd.Timestamp(ts); t = (t.tz_localize("UTC") if t.tzinfo is None else t).normalize(); tab = L.liq.get(s)
            row = tab.loc[t] if (tab is not None and t in tab.index) else None
            if row is not None and np.isfinite(row["quote_vol"]) and np.isfinite(row["sigma"]):
                bps = min(CAP_BPS, 1e4 * Y * row["sigma"] * math.sqrt(n / row["quote_vol"]))
            else:
                bps = FALLBACK_BPS
            v += prices[s] * (1 - (L.base_slip + bps) / 1e4) * q * (1 - L.fee_bps / 1e4)
    return float(v)
