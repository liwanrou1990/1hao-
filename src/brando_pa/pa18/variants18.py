"""第十八阶段恢复机制变体（预登记 §3）。
- RecoveryBreaker 继承原 CircuitBreaker，只改变“市场回撤熔断之后”的恢复方式：冷静期长度、提前结束条件、恢复期仓位阶梯。
- 熔断触发条件（回撤 20%、单日跌 15%、恢复期再回撤 10%）完全沿用父类；数据故障在 _emit 中强制 max_exposure = 0，阶梯上限之后再次检查，不能被市场信号绕过。
- RecoveryBacktester 继承第十阶段日线引擎；V0 与原 B2 逐位一致（测试断言）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..holding.engine import HoldingBacktester
from ..trendsys.risk_layer import COOLDOWN, EMERGENCY_HALT, NORMAL, RECOVERY, CircuitBreaker

VARIANTS = {
    "V0_original": {},
    "V1_fixed30": {"cooldown_fixed": 30},
    "V2_fixed60": {"cooldown_fixed": 60},
    "V2b_fixed90": {"cooldown_fixed": 90},
    "V3_trend_confirm": {"early": "trend"},
    "V4_weekly_structure": {"early": "weekly"},
    "V5_ladder": {"ladder": True, "topup": True},
    "V6_trend_ladder": {"early": "trend", "ladder": True, "topup": True},
    "C0_original_topup": {"topup": True},
}
MIN_DAYS_AFTER_HALT = 14
TREND_STREAK = 5
LADDER = ((20, 0.25), (40, 0.50), (60, 0.75))


def market_context(bars: dict, assets: list[str], idx: pd.DatetimeIndex) -> dict:
    """每个资产：收盘、SMA200、连续收盘 > SMA200 的天数；周日收盘时的周收盘、20 周 SMA、该周最低价。只用 ≤ t 的数据。"""
    out = {}
    for s in assets:
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.set_index("ts").sort_index()
        c = b["close"].astype(float); sma = c.rolling(200).mean(); above = (c > sma).astype(int)
        streak = above.groupby((above != above.shift()).cumsum()).cumsum() * above
        w = b.resample("W-SUN", label="right", closed="right").agg({"close": "last", "low": "min"}).dropna(); w["sma20w"] = w["close"].rolling(20).mean()
        df = pd.DataFrame({"close": c, "sma200": sma, "above_streak": streak}).join(w.rename(columns={"close": "w_close", "low": "w_low"}), how="left")
        out[s] = df.reindex(idx)
    return out


class RecoveryBreaker(CircuitBreaker):
    def __init__(self, params: dict | None = None, variant: dict | None = None, ctx: dict | None = None):
        super().__init__(params); self.v = variant or {}; self.ctx = ctx or {}; self.halt_ts = None; self.wk_lows: dict[str, list] = {}; self._early_why = None

    def _go(self, ts, new: str, equity: float, why: str) -> None:
        if new == RECOVERY and self._early_why:
            why = self._early_why; self._early_why = None
        super()._go(ts, new, equity, why)
        if new == EMERGENCY_HALT:
            self.halt_ts = pd.Timestamp(ts); self.wk_lows = {}
        if new == COOLDOWN and self.v.get("cooldown_fixed"):
            self.cooldown_until = pd.Timestamp(ts) + pd.Timedelta(days=int(self.v["cooldown_fixed"]))

    def _early_ok(self, ts) -> str | None:
        if self.halt_ts is None or (ts - self.halt_ts).days < MIN_DAYS_AFTER_HALT:
            return None
        for s, df in self.ctx.items():
            if ts not in df.index:
                continue
            r = df.loc[ts]
            if self.v.get("early") == "trend" and np.isfinite(r["above_streak"]) and r["above_streak"] >= TREND_STREAK:
                return f"early_trend: {s} 连续 {int(r['above_streak'])} 日收盘 > SMA200"
            if self.v.get("early") == "weekly" and np.isfinite(r.get("w_close", np.nan)):
                prev = self.wk_lows.get(s, [])
                ok = len(prev) >= 1 and np.isfinite(r["sma20w"]) and r["w_close"] > r["sma20w"] and r["w_low"] > min(prev)
                if ok:
                    return f"early_weekly: {s} 周收盘 > 20 周 SMA 且周低点 {r['w_low']:.0f} 高于熔断以来最低 {min(prev):.0f}"
        return None

    def evaluate(self, ts, equity: float, daily_drop: float = 0.0, exposure: float = 1.0):
        ts = pd.Timestamp(ts)
        if self.state == COOLDOWN and self.v.get("early"):
            why = self._early_ok(ts)
            if why:
                self._early_why = why; self.cooldown_until = ts                                    # 交给父类：“冷静期到期”→ RECOVERY（上限、60 天、再熔断规则不变）
        d = super().evaluate(ts, equity, daily_drop, exposure)
        if self.state == COOLDOWN and self.halt_ts is not None:                                     # 记录熔断以来的周最低价（用于 V4，下一周才比较）
            for s, df in self.ctx.items():
                if ts in df.index and np.isfinite(df.loc[ts].get("w_low", np.nan)):
                    self.wk_lows.setdefault(s, []).append(float(df.loc[ts, "w_low"]))
        if self.v.get("ladder") and self.state == RECOVERY and self.recovery_since is not None:
            days = (ts - self.recovery_since).days; cap = next((c for lim, c in LADDER if days < lim), 0.75)
            d.max_exposure = cap
            if self.data_fault:                                                                      # 技术性停机优先：阶梯不能绕过
                d.max_exposure = 0.0
            self.log[-1]["max_exposure"] = d.max_exposure
        return d


class RecoveryBacktester(HoldingBacktester):
    def __init__(self, bars: dict, spec: dict, fee_bps: float, slip_bps: float, start, end, variant: str = "V0_original", initial: float = 10000.0, sma: int = 200, risk_params: dict | None = None):
        super().__init__(bars, {**spec, "risk_mode": "state_machine"}, fee_bps, slip_bps, start, end, initial=initial, sma=sma, risk_params=risk_params)
        self.variant_name = variant; self.var = VARIANTS[variant]
        if variant != "V0_original":
            self.risk = RecoveryBreaker((risk_params or {}).get("state_machine", {}), self.var, market_context(bars, self.assets, self.idx))
        self._last_cap = 1.0

    def _orders(self, i, eq, cl, want, reasons, dec):
        orders = super()._orders(i, eq, cl, want, reasons, dec)
        cap = max(0.0, min(1.0, float(dec.max_exposure)))
        if self.var.get("topup") and cap > self._last_cap + 1e-9 and not dec.force_exit:
            held = sorted(want); w = cap / len(held) if held else 0.0; thr = max(10.0, 0.005 * eq)
            for s in held:
                if s in self.pos and not any(o["symbol"] == s and o["side"] == "BUY" for o in orders):
                    cur = self.L.qty(s) * cl[s]
                    if w * eq > cur + thr:
                        orders.append({"symbol": s, "side": "BUY", "amount": w * eq - cur, "reason": f"topup_cap_{cap:.2f}"})
        self._last_cap = cap
        return orders
