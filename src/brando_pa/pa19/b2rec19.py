"""第十九阶段 B2 冷静期提前结束变体 V7（预登记 §6）：熔断后 ≥ 30 天，且任一资产在周线收盘时“周收盘 > 40 周均线”并且“最近完整一周最低价 > 熔断以来最低周低点”，
才提前进入 RECOVERY（上限 50%、60 天、10% 再熔断不变）。原 B2 不改；数据故障不可绕过（继承第十八阶段 RecoveryBreaker 的 _emit 逻辑）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pa18 import phase18
from ..pa18.variants18 import RecoveryBacktester, RecoveryBreaker, market_context

MIN_DAYS = 30


def context40(bars: dict, assets: list[str], idx: pd.DatetimeIndex) -> dict:
    ctx = market_context(bars, assets, idx)
    for s in assets:
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); w = b.set_index("ts")["close"].astype(float).resample("W-SUN", label="right", closed="right").last().dropna()
        ctx[s]["sma40w"] = w.rolling(40).mean().reindex(idx)
    return ctx


class V7Breaker(RecoveryBreaker):
    def _early_ok(self, ts):
        if self.halt_ts is None or (ts - self.halt_ts).days < MIN_DAYS:
            return None
        for s, df in self.ctx.items():
            if ts not in df.index:
                continue
            r = df.loc[ts]
            if not np.isfinite(r.get("w_close", np.nan)) or not np.isfinite(r.get("sma40w", np.nan)):
                continue
            prev = self.wk_lows.get(s, [])
            if len(prev) >= 1 and r["w_close"] > r["sma40w"] and r["w_low"] > min(prev):
                return f"early_V7: {s} 周收盘 {r['w_close']:.0f} > 40 周均线 {r['sma40w']:.0f} 且周低点高于熔断以来最低 {min(prev):.0f}"
        return None


class V7Backtester(RecoveryBacktester):
    def __init__(self, bars, spec, fee_bps, slip_bps, start, end, risk_params=None, initial: float = 10000.0):
        super().__init__(bars, spec, fee_bps, slip_bps, start, end, variant="V4_weekly_structure", initial=initial, risk_params=risk_params)
        self.variant_name = "V7_weekly40_structure"
        self.risk = V7Breaker((risk_params or {}).get("state_machine", {}), {"early": "weekly40"}, context40(bars, self.assets, self.idx))


def run_v7(c: dict, windows: dict | None = None, costs=(15, 25)) -> dict:
    bars = phase18.load_bars(); bb = {k: v for k, v in bars.items() if not k.startswith("_")}; latest = str(bb["BTCUSDT"]["ts"].iloc[-1].date())
    windows = windows or phase18.WINDOWS18; out = {}
    for wn, (a, b) in windows.items():
        b = b or latest
        for cost in costs:
            fee, slip = phase18.COSTS18[cost]
            bt = V7Backtester(bb, {"name": "V7", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, slip, a, b, risk_params=c.get("trendsys", {}).get("risk", {})); res = bt.run()
            m = phase18.metrics(bt, res, bars, cost); bt0, r0 = phase18.run_variant(bars, "V0_original", cost, a, b, c); m0 = phase18.metrics(bt0, r0, bars, cost)
            early = [t for t in bt.risk.transitions if t["to"] == "RECOVERY" and str(t["why"]).startswith("early_V7")]
            out[f"{wn}|{cost}"] = {"V7": m, "V0": m0, "early_recoveries": [{"ts": t["ts"], "why": t["why"]} for t in early],
                                   "net_ok": m["liquidation_net_profit_usdt"] >= m0["liquidation_net_profit_usdt"], "mdd_ok": m["max_drawdown"] >= m0["max_drawdown"] - 0.03,
                                   "reentry_ok": not (len(m["reentries"]) >= 3 and m0["reentry_loss_rate_60d"] is not None and m["reentry_loss_rate_60d"] is not None and m["reentry_loss_rate_60d"] > m0["reentry_loss_rate_60d"] + 0.20)}
    need = [f"{w}|{cc}" for w in ("early_2017", "dev_2018_2021", "repeat_2022_2026") for cc in costs]
    ok = all(out[k]["net_ok"] and out[k]["mdd_ok"] and out[k]["reentry_ok"] for k in need if k in out)
    fy, f0 = out.get("full|15", {}).get("V7", {}).get("yearly_return", {}), out.get("full|15", {}).get("V0", {}).get("yearly_return", {})
    yrs = [y for y in fy if y in f0 and abs(fy[y] - f0[y]) > 1e-12]; better = sum(1 for y in yrs if fy[y] > f0[y])
    full_ok = "full|15" in out and out["full|15"]["V7"]["liquidation_net_profit_usdt"] > out["full|15"]["V0"]["liquidation_net_profit_usdt"] and yrs and better >= len(yrs) / 2
    out["verdict"] = {"advantage": bool(ok and full_ok), "years_better": better, "years_differ": len(yrs), "text": "具有增量优势 → 可进入独立影子观察（原 B2 不变）" if (ok and full_ok) else "无增量优势 → 保留原 B2 规则不变"}
    return out
