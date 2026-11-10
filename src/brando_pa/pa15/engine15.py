"""第十五阶段回测引擎：第十阶段 HoldingBacktester 的子类，加入因子门控 / 因子信号（预登记 §4）。
时间规则不变：第 t 日收盘后决策（SMA200 用 ≤ t 收盘；因子用 ≤ t−1 的链上值），第 t+1 日开盘成交。
spec 新增：
  signal: "trend"（默认，SMA200）| "factor"（入场 / 退出全由因子布尔序列决定）| 继承 "buyhold"
  entry_gate: {symbol: bool Series}  trend 模式下，只有 True 才允许新入场（不影响已持仓）
  force_exit: {symbol: bool Series}  True → 次日开盘退出（任一模式）
  factor_entry / factor_exit: {symbol: bool Series}  factor 模式的入场 / 退出
不传任何门控时与第十阶段引擎逐数字一致（测试断言）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..holding.engine import HoldingBacktester


class FactorBacktester(HoldingBacktester):
    def __init__(self, bars: dict, spec: dict, fee_bps: float, slip_bps: float, start, end, initial: float = 10000.0, sma: int = 200, risk_params: dict | None = None):
        super().__init__(bars, spec, fee_bps, slip_bps, start, end, initial=initial, sma=sma, risk_params=risk_params)
        self.signal = spec.get("signal", "trend")
        self.gates = {k: self._bool(spec.get(k) or {}) for k in ("entry_gate", "force_exit", "factor_entry", "factor_exit")}

    def _bool(self, d: dict) -> dict:
        out = {}
        for s, ser in d.items():
            if s not in self.assets:
                continue
            x = pd.Series(ser).copy(); x.index = pd.DatetimeIndex(pd.to_datetime(x.index, utc=True))
            out[s] = x.reindex(self.idx).fillna(False).astype(bool).to_numpy()
        return out

    def _g(self, kind: str, s: str, i: int, default: bool) -> bool:
        arr = self.gates[kind].get(s); return bool(arr[i]) if arr is not None else default

    def _want(self, i: int) -> tuple[set, dict]:
        mode = self.spec["mode"]
        if mode == "buyhold" and self.signal != "factor":
            return super()._want(i)
        want = set(); reasons = {}
        for s in self.assets:
            p = self.pos.get(s); close = float(self.close[s][i])
            if self.signal == "factor":
                if p is None:
                    if self._g("factor_entry", s, i, False):
                        want.add(s)
                    continue
                if self._g("factor_exit", s, i, False) or self._g("force_exit", s, i, False):
                    reasons[s] = "factor_exit"; continue
                want.add(s); continue
            on = bool(self.sig[s][i])
            if p is None:
                if on and self._g("entry_gate", s, i, True):
                    want.add(s)
                continue
            if self._g("force_exit", s, i, False):
                reasons[s] = "factor_exit"; continue
            dd = self.spec.get("risk_exit_dd")
            if dd is not None and close <= p.entry_price * (1 - float(dd)):
                reasons[s] = "risk_exit"; continue
            mn = int(self.spec.get("min_hold", 0) or 0)
            if not on and p.days_held >= mn:
                reasons[s] = "trend_off"; continue
            want.add(s)
        return want, reasons
