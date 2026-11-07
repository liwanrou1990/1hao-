"""信号引擎：对每个标的在每根执行 K 线收盘后输出 SignalReport（WAIT / LONG / SHORT / EXIT / NO_TRADE）及完整理由。

流程：BarContext → 各策略 on_bar（独立、互不影响）→ 过滤器 → 风控仓位 → 组合限制（由回测/模拟盘传入）→ 状态。
* 仅当全部必要条件满足才给出 LONG/SHORT；候选存在但被过滤/风控拒绝 → NO_TRADE（附拒绝原因）；无候选 → WAIT（附各策略等待原因）。
* 多策略同向触发：按净盈亏比择优，再按 priority 顺序；逆向同时触发 → NO_TRADE（信号冲突）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..filters.pipeline import FilterPipeline
from ..risk.costs import estimate_round_trip_cost, net_rr
from ..risk.sizing import size_position
from ..strategies import make_strategies
from ..strategies.base import BarContext, Signal
from .context import MarketContext

STATUSES = ("WAIT", "LONG", "SHORT", "EXIT", "NO_TRADE")


REGIME4 = {"strong_up": "上涨趋势", "up": "上涨趋势", "strong_down": "下跌趋势", "down": "下跌趋势", "range": "横盘震荡", "undefined": "横盘震荡"}


def regime_class4(regime: dict) -> str:
    if regime.get("highvol"):
        return "高波动异常"
    return REGIME4.get(regime.get("trend", "range"), "横盘震荡")


def mtf_checks(sig, ctx, mtf_cfg: dict) -> list[dict]:
    """多周期决策模式的检查：aligned = 4h 结构 + 日线环境方向一致；zone_limit 另要求区域含 1w/1d。single 不检查。"""
    mode = mtf_cfg.get("mode", "single")
    out = []
    if mode == "single":
        return out
    if mtf_cfg.get("aligned_require_4h", True):
        ok = (sig.direction == "long" and ctx.trend_4h == "up") or (sig.direction == "short" and ctx.trend_4h == "down")
        out.append({"name": "mtf_4h_trend", "status": "OK" if ok else "FAIL", "detail": f"4h 结构 {ctx.trend_4h}，信号 {sig.direction}"})
    if mtf_cfg.get("aligned_require_1d", True):
        tr = ctx.regime.get("trend", "range")
        bad = (sig.direction == "long" and tr in ("down", "strong_down")) or (sig.direction == "short" and tr in ("up", "strong_up"))
        out.append({"name": "mtf_1d_regime", "status": "FAIL" if bad else "OK", "detail": f"日线环境 {tr}"})
    if mode == "zone_limit" and mtf_cfg.get("zone_limit_require_htf_zone", True):
        tfs = sig.zone.get("tfs", [])
        ok = any(t in ("1w", "1d") for t in tfs)
        out.append({"name": "mtf_zone_htf", "status": "OK" if ok else "FAIL", "detail": f"区域周期 {'/'.join(tfs)}"})
    return out


def instrument_for(direction: str, policy: str) -> str | None:
    if policy == "spot_only":
        return "spot" if direction == "long" else None
    if policy == "perp_long_spot":
        return "spot" if direction == "long" else "perp"
    return "perp"


@dataclass
class SignalReport:
    symbol: str
    ts: pd.Timestamp
    status: str
    price: float
    trend_1d: dict
    structure_4h: dict
    zones: list[dict]
    strategy_states: dict
    candidates: list[dict] = field(default_factory=list)      # 每个策略的候选信号 + 过滤结果 + 仓位
    chosen: dict | None = None
    reasons: list[str] = field(default_factory=list)          # 为什么开仓 / 为什么不交易
    data_ok: bool = True
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "ts": self.ts.isoformat(), "status": self.status, "price": self.price, "trend_1d": self.trend_1d, "structure_4h": self.structure_4h,
                "zones": self.zones, "strategy_states": self.strategy_states, "candidates": self.candidates, "chosen": self.chosen, "reasons": self.reasons,
                "data_ok": self.data_ok, "extras": {k: v for k, v in self.extras.items() if k != "bar_seconds"}}


class SignalEngine:
    def __init__(self, c: dict, strategies_override: dict | None = None, filters_enabled: bool | None = None):
        self.c = c
        self.strategies = strategies_override if strategies_override is not None else make_strategies(c["strategies"])
        fcfg = dict(c["filters"])
        if filters_enabled is not None:
            fcfg["enabled"] = filters_enabled
        self.fcfg = fcfg
        self.policy = c["risk"].get("direction_policy", "perp_both")
        self.priority = list(c["strategies"].get("priority", list(self.strategies)))
        self.mtf = c.get("mtf", {"mode": "single"})
        self.regime_policy = c.get("regime_policy", {}) or {}
        self.entry_mode = c.get("execution_modes", {}).get("entry_mode", "market")
        self.limit_cfg = c.get("execution_modes", {}).get("limit", {})

    def reset(self) -> None:
        for s in self.strategies.values():
            s.reset()

    def evaluate(self, mc: MarketContext, i: int, equity: float, data_ok: bool = True, detail: bool = True) -> SignalReport:
        """detail=False 时不生成区域明细与策略状态摘要（回测用，不影响交易决策）。"""
        ts = mc.ts.iloc[i]
        price = float(mc.cl[i])
        if not mc.ready(i):
            return SignalReport(mc.symbol, ts, "WAIT", price, {"trend": "undefined"}, {"trend": "undefined"}, [], {}, reasons=["预热期：ATR/高周期区域尚不可用"], data_ok=data_ok)
        a_cfg = self.c["strategies"].get("A_breakout_retest", {})
        ctx = mc.bar_context(i, a_cfg.get("break_tf", "4h"), a_cfg.get("zone_tfs", ["1w", "1d"]))
        cands: list[dict] = []
        exec_candidates: list[tuple[Signal, dict, dict]] = []
        for key, strat in self.strategies.items():
            sig = strat.on_bar(ctx)
            if sig is None:
                continue
            inst = instrument_for(sig.direction, self.policy)
            entry = {"strategy": key, "signal": sig.to_dict(), "instrument": inst}
            if inst is None:
                entry["filter"] = {"accepted": False, "checks": [{"name": "direction_policy", "status": "FAIL", "detail": f"方向策略 {self.policy} 不允许 {sig.direction}"}]}
                cands.append(entry); continue
            fr = FilterPipeline(self.fcfg, self.c["costs"], self.c["risk"], inst).apply(sig, ctx)
            pre = mtf_checks(sig, ctx, self.mtf)
            allowed = self.regime_policy.get(key)
            cls4 = regime_class4(ctx.regime)
            if allowed:
                pre.append({"name": "regime_policy", "status": "OK" if cls4 in allowed else "FAIL", "detail": f"环境 {cls4}，策略允许 {allowed}"})
            fr.checks = pre + fr.checks
            fr.accepted = fr.accepted and all(c_["status"] != "FAIL" for c_ in pre)
            entry["filter"] = fr.to_dict()
            _cost = estimate_round_trip_cost(sig.ref_price, sig.stop_price, self.c["costs"], inst, sig.direction, sig.max_hold_bars or 96, mc.bar_seconds, ctx.extras.get("funding_rate"))
            entry["cost_bps_est"] = float(_cost["cost_bps"]); entry["cost_to_risk"] = float(_cost["cost_per_unit"] / sig.risk_per_unit) if sig.risk_per_unit > 0 else None
            entry["cost_share_of_reward_est"] = float(_cost["cost_per_unit"] / sig.reward_per_unit) if sig.reward_per_unit > 0 else None
            entry["net_rr_est"] = float(net_rr(sig.ref_price, sig.stop_price, sig.take_profit, _cost["cost_per_unit"]))
            entry["regime4"] = cls4
            entry["stop_distance_pct"] = sig.risk_per_unit / sig.ref_price
            entry["stop_distance_atr"] = sig.risk_per_unit / ctx.atr if ctx.atr > 0 else None
            entry["zone_tfs"] = "/".join(sig.zone.get("tfs", []))
            if self.entry_mode == "limit" and sig.limit_price is not None:
                cap = float(self.limit_cfg.get("improve_cap_atr", 1.0)) * ctx.atr
                lp = max(sig.limit_price, sig.ref_price - cap) if sig.direction == "long" else min(sig.limit_price, sig.ref_price + cap)
                if (sig.direction == "long" and lp <= sig.stop_price) or (sig.direction == "short" and lp >= sig.stop_price):
                    lp = sig.ref_price
                sig.limit_price = float(lp); entry["signal"]["limit_price"] = float(lp)
            if fr.accepted:
                sz = size_position(equity, sig.ref_price, sig.stop_price, sig.direction, mc.symbol, self.c["risk"], self.c["costs"], inst,
                                   hold_bars=sig.max_hold_bars or 96, bar_seconds=mc.bar_seconds, funding_rate_8h=ctx.extras.get("funding_rate"))
                entry["sizing"] = sz.to_dict()
                cost = estimate_round_trip_cost(sig.ref_price, sig.stop_price, self.c["costs"], inst, sig.direction, sig.max_hold_bars or 96, mc.bar_seconds, ctx.extras.get("funding_rate"))
                entry["net_rr"] = float(net_rr(sig.ref_price, sig.stop_price, sig.take_profit, cost["cost_per_unit"]))
                entry["cost_bps"] = float(cost["cost_bps"])
                entry["cost_share_of_reward"] = float(cost["cost_per_unit"] / sig.reward_per_unit) if sig.reward_per_unit > 0 else None
                if sz.ok:
                    exec_candidates.append((sig, entry, sz.to_dict()))
            cands.append(entry)
        zones_d = [z.to_dict() | {"role": z.role(price)} for z in ctx.zones] if detail else []
        states = {k: s.state_summary() for k, s in self.strategies.items()} if detail else {}
        rep = SignalReport(mc.symbol, ts, "WAIT", price, ctx.regime | {"class4": regime_class4(ctx.regime)}, mc.structure_dict(i), zones_d, states, cands, data_ok=data_ok, extras=ctx.extras)
        if not cands:
            rep.reasons = [r for s in self.strategies.values() for r in s.wait_reasons[:2]] or ["无任何策略条件满足"]
            return rep
        if not exec_candidates:
            rep.status = "NO_TRADE"
            for e in cands:
                fails = [f"{c['name']}: {c['detail']}" for c in e.get("filter", {}).get("checks", []) if c["status"] == "FAIL"]
                szr = e.get("sizing", {})
                if fails:
                    rep.reasons.append(f"{e['strategy']} {e['signal']['direction']} 被过滤：" + "；".join(fails))
                elif szr and not szr.get("ok", True):
                    rep.reasons.append(f"{e['strategy']} 仓位不可行：" + "；".join(szr.get("reasons", [])))
            return rep
        dirs = {s.direction for s, _, _ in exec_candidates}
        if len(dirs) > 1:
            rep.status = "NO_TRADE"; rep.reasons = ["多策略方向冲突（同时出现多/空候选），本根不交易"]
            return rep
        exec_candidates.sort(key=lambda t: (-t[1]["net_rr"], self.priority.index(t[0].strategy) if t[0].strategy in self.priority else 99))
        sig, entry, sz = exec_candidates[0]
        if not data_ok:
            rep.status = "NO_TRADE"; rep.reasons = ["行情数据过期，禁止新开仓"] + sig.reasons
            rep.chosen = entry
            return rep
        rep.status = "LONG" if sig.direction == "long" else "SHORT"
        rep.chosen = entry
        rep.reasons = [f"策略 {sig.strategy}：" + "；".join(sig.reasons), f"净盈亏比 {entry['net_rr']:.2f}，成本 {entry['cost_bps']:.1f} bps", f"仓位：{sz['qty']} @ 风险 {sz['risk_usd']:.2f} USDT（{sz['risk_pct_effective']:.2%}），杠杆 {sz['leverage']:.2f}",
                       f"止损 {sig.stop_price:.2f}，止盈 {sig.take_profit:.2f}，失效条件：{sig.invalidation}"]
        return rep
