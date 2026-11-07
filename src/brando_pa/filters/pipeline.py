"""可选信号过滤模块。每个过滤器返回 {name, ok, detail}；任一关键过滤器不通过 → 信号被拒绝，理由完整保留供看板显示。

过滤器全部只用决策时刻可得的数据：
  volume     突破/假突破根或信号根的相对成交量（过去 N 根均量，不含当前根）
  volatility 执行周期 ATR/价格 的上下限；止损距离（ATR 倍数）上下限
  regime     日线环境：强趋势中禁止逆势；可选高波动环境禁止开新仓
  cost       往返成本 / 止损风险 上限；净盈亏比下限（含手续费、滑点、预估资金费）
  derivatives（可选）资金费率拥挤方向过滤；无数据 → 标记 skipped
  onchain    （可选）交易所净流入 z 分数；按 published_at 对齐，无数据 → skipped
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..risk.costs import estimate_round_trip_cost, net_rr
from ..strategies.base import BarContext, Signal


@dataclass
class FilterResult:
    accepted: bool
    checks: list[dict] = field(default_factory=list)

    def rejected_reasons(self) -> list[str]:
        return [f"{c['name']}: {c['detail']}" for c in self.checks if c["status"] == "FAIL"]

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "checks": self.checks}


class FilterPipeline:
    def __init__(self, filters_cfg: dict, costs_cfg: dict, risk_cfg: dict, instrument: str = "perp"):
        self.f = filters_cfg
        self.costs = costs_cfg
        self.risk = risk_cfg
        self.instrument = instrument

    def _chk(self, name: str, ok: bool | None, detail: str) -> dict:
        return {"name": name, "status": "SKIP" if ok is None else ("OK" if ok else "FAIL"), "detail": detail}

    def apply(self, sig: Signal, ctx: BarContext) -> FilterResult:
        checks: list[dict] = []
        if not self.f.get("enabled", True):
            return FilterResult(True, [self._chk("filters", None, "过滤模块已关闭（原始策略）")])
        # 成交量
        v = self.f.get("volume", {})
        if v.get("enabled", True):
            if sig.strategy in ("A_breakout_retest", "B_reclaim"):
                rv, thr = sig.break_rel_vol, float(v["min_ratio_breakout"])
                ok = None if rv is None or not np.isfinite(rv) else rv >= thr
                checks.append(self._chk("volume", ok, f"突破根相对成交量 {rv if rv is None else round(rv, 2)} vs 下限 {thr}"))
            else:
                rv, thr = sig.signal_rel_vol, float(v["min_ratio_rejection"])
                ok = None if rv is None or not np.isfinite(rv) else rv >= thr
                checks.append(self._chk("volume", ok, f"信号根相对成交量 {rv if rv is None else round(rv, 2)} vs 下限 {thr}"))
        # 波动率
        vo = self.f.get("volatility", {})
        if vo.get("enabled", True):
            ok = float(vo["atr_pct_min"]) <= ctx.atr_pct <= float(vo["atr_pct_max"])
            checks.append(self._chk("volatility", ok, f"ATR/价格 {ctx.atr_pct:.4%} 允许区间 [{float(vo['atr_pct_min']):.2%}, {float(vo['atr_pct_max']):.2%}]"))
            sd = sig.risk_per_unit / ctx.atr if ctx.atr > 0 else np.inf
            ok2 = float(vo["stop_distance_min_atr"]) <= sd <= float(vo["stop_distance_max_atr"])
            checks.append(self._chk("stop_distance", ok2, f"止损距离 {sd:.2f} ATR 允许 [{vo['stop_distance_min_atr']}, {vo['stop_distance_max_atr']}]"))
        # 环境
        rg = self.f.get("regime", {})
        if rg.get("enabled", True):
            tr = ctx.regime.get("trend", "range")
            counter = (sig.direction == "long" and tr == "strong_down") or (sig.direction == "short" and tr == "strong_up")
            if rg.get("block_counter_trend_in_strong_trend", True):
                checks.append(self._chk("regime_trend", not counter, f"日线环境 {tr}，信号 {sig.direction}" + ("（强趋势逆势，拒绝）" if counter else "")))
            if rg.get("block_new_in_highvol", False):
                checks.append(self._chk("regime_highvol", not ctx.regime.get("highvol", False), f"高波动环境={ctx.regime.get('highvol')}"))
        # 衍生品（可选）
        dv = self.f.get("derivatives", {})
        if dv.get("enabled", False):
            fr = ctx.extras.get("funding_rate")
            if fr is None:
                checks.append(self._chk("funding", None, "无资金费率数据（交易所接口不可达），过滤器跳过"))
            else:
                crowded = (sig.direction == "long" and fr > float(dv["funding_abs_max"])) or (sig.direction == "short" and fr < -float(dv["funding_abs_max"]))
                checks.append(self._chk("funding", not crowded, f"资金费率 {fr:.5f}，上限 ±{dv['funding_abs_max']}"))
        # 链上（可选）
        oc = self.f.get("onchain", {})
        if oc.get("enabled", False):
            z = ctx.extras.get("netflow_z")
            if z is None or not np.isfinite(z):
                checks.append(self._chk("onchain_netflow", None, "无按发布时间对齐的链上数据，过滤器跳过"))
            else:
                bad = (sig.direction == "long" and z > float(oc["block_long_if_netflow_z_gt"])) or (sig.direction == "short" and z < float(oc["block_short_if_netflow_z_lt"]))
                checks.append(self._chk("onchain_netflow", not bad, f"交易所净流入 z={z:.2f}（发布时间 ≤ 决策时刻）"))
        # 成本
        co = self.f.get("cost", {})
        if co.get("enabled", True):
            hold = sig.max_hold_bars or 96
            cost = estimate_round_trip_cost(sig.ref_price, sig.stop_price, self.costs, self.instrument, sig.direction, hold_bars=hold, bar_seconds=ctx.extras.get("bar_seconds", 900))
            ratio = cost["cost_per_unit"] / sig.risk_per_unit if sig.risk_per_unit > 0 else np.inf
            checks.append(self._chk("cost_to_risk", ratio <= float(co["max_cost_to_risk"]), f"往返成本/止损风险 {ratio:.1%}（上限 {float(co['max_cost_to_risk']):.0%}；成本 {cost['cost_bps']:.1f} bps）"))
            nrr = net_rr(sig.ref_price, sig.stop_price, sig.take_profit, cost["cost_per_unit"])
            checks.append(self._chk("net_rr", nrr >= float(co["min_net_rr"]), f"净盈亏比 {nrr:.2f}（下限 {co['min_net_rr']}；毛 {sig.rr_gross:.2f}）"))
        accepted = all(c["status"] != "FAIL" for c in checks)
        return FilterResult(accepted, checks)
