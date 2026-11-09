"""入场识别（预登记规则，全部用 ≤ i 已确认信息）：
  PullbackConfirmation（策略 A 趋势回调）、BreakoutRetestDetector（策略 B 突破回踩）、FailedBreakdownDetector（策略 C 假跌破收复，实验组）、
  VolumeAndOrderFlowConfirmation（只有 K 线代理：量能 / 收盘位置；无逐笔订单流，覆盖范围 0）、EntryRiskRewardEvaluator（入场前的 T1/T2/失效价/净盈亏比/仓位）。
每个函数返回 dict：{"signal": bool, "reason": str, ...}；形成 BUY 的候选还附 plan（entry_zone, invalidation, target_1, target_2, ...）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .structure import DailyStructureDetector, WeeklyTrendDetector, structure_state
from .zones import SupportResistanceZones, Zone

ORDER_FLOW_COVERAGE = {"tick_data": False, "aggressive_buy_volume": False, "order_flow_delta": False, "absorption": False, "proxy": "K 线量能 + 收盘位置 + 影线；覆盖范围 0 条逐笔记录；未合成任何订单流"}


class VolumeAndOrderFlowConfirmation:
    """K 线代理：量能相对 20 日均量、收盘在当日振幅中的位置、下影线占比。无真实订单流。"""

    @staticmethod
    def bar(d: DailyStructureDetector, i: int) -> dict:
        o, h, l, c = (float(d.d[k].iloc[i]) for k in ("open", "high", "low", "close")); rng = max(h - l, 1e-12)
        v = d.vol[i]; ma = d.vol_ma20[i]; rel = float(v / ma) if np.isfinite(ma) and ma > 0 else np.nan
        return {"vol_rel20": rel, "close_pos": (c - l) / rng, "lower_wick": (min(o, c) - l) / rng, "up_day": c > o, "coverage": ORDER_FLOW_COVERAGE}

    @staticmethod
    def selling_pressure_weakening(d: DailyStructureDetector, start: int, end: int) -> bool:
        """回调窗口 [start, end] 内至少一个下跌日成交量 < 20 日均量。"""
        for j in range(max(start, 1), end + 1):
            if d.close[j] < d.close[j - 1] and np.isfinite(d.vol_ma20[j]) and d.vol[j] < d.vol_ma20[j]:
                return True
        return False


class EntryRiskRewardEvaluator:
    def __init__(self, min_rr: float = 1.5, min_t1_risk_mult: float = 1.5):
        self.min_rr = min_rr; self.min_t1_mult = min_t1_risk_mult

    def plan(self, entry_ref: float, invalidation: float, zones: list[Zone], st: dict, cost_one_side: float, entry_zone: tuple, reason: str) -> dict:
        risk = entry_ref - invalidation
        if risk <= 0:
            return {"ok": False, "reason": f"{reason}; 失效价不低于入场价 → WAIT"}
        res = SupportResistanceZones.resistances_above(zones, entry_ref)
        t1 = next((z for z in res if z.low - entry_ref >= self.min_t1_mult * risk), None)
        if t1 is None:
            return {"ok": False, "reason": f"{reason}; 上方无满足 ≥{self.min_t1_mult}R 的已确认阻力区 → WAIT"}
        above = [z for z in res if z.low > t1.low]
        if above:
            t2 = above[0].low; t2_basis = f"下一个已确认阻力区 [{above[0].low:.0f},{above[0].high:.0f}]"
        elif st.get("last_high") and st.get("prev_low") is not None and st["last_high"] > st["prev_low"]:
            t2 = entry_ref + (st["last_high"] - st["prev_low"]); t2_basis = "测量目标：最近已确认摆动低→高幅度"
        else:
            t2 = None; t2_basis = "无"
        c = cost_one_side
        net_gain = (t1.low - entry_ref) - 2 * c * entry_ref; net_risk = risk + 2 * c * entry_ref
        rr = net_gain / net_risk; exp_ret = net_gain / entry_ref
        ok = rr >= self.min_rr
        return {"ok": ok, "entry_zone": [float(entry_zone[0]), float(entry_zone[1])], "entry_ref": float(entry_ref), "invalidation": float(invalidation), "target_1": float(t1.low), "target_1_zone": [t1.low, t1.high], "target_1_strength": t1.strength, "target_2": float(t2) if t2 else None, "target_2_basis": t2_basis,
                "expected_net_return": float(exp_ret), "expected_net_reward_risk": float(rr), "risk_per_unit": float(risk), "cost_one_side": c, "reason": reason + ("" if ok else f"; 净盈亏比 {rr:.2f} < {self.min_rr} → WAIT")}


class PullbackConfirmation:
    """策略 A：上涨趋势回调到支撑区并被确认。"""

    def __init__(self, max_depth: float = 0.618, vol_rel_min: float = 0.8):
        self.max_depth = max_depth; self.vol_rel_min = vol_rel_min

    def check(self, d: DailyStructureDetector, zones_obj: SupportResistanceZones, i: int, zones: list[Zone]) -> dict:
        st = structure_state(d.sw, i); close = d.close[i]; a = d.atr[i]
        if st["last_high_idx"] is None or st["prev_low"] is None or st["last_low_idx"] is None or not np.isfinite(a):
            return {"signal": False, "reason": "结构点不足"}
        hi_idx = st["last_high_idx"]; H = st["last_high"]
        lows_before = d.sw[(d.sw.kind == "L") & (d.sw.idx < hi_idx) & (d.sw.confirmed_at <= i)]
        if lows_before.empty:
            return {"signal": False, "reason": "高点前无已确认低点"}
        L0 = float(lows_before.price.iloc[-1])
        if H <= L0 or hi_idx >= i - 1:
            return {"signal": False, "reason": "无有效上行波段或回调未开始"}
        seg_low = float(d.d["low"].iloc[hi_idx + 1:i + 1].min()); depth = (H - seg_low) / (H - L0)
        if depth > self.max_depth:
            return {"signal": False, "reason": f"回调深度 {depth:.0%} > 61.8%，结构破坏"}
        if depth <= 0.05:
            return {"signal": False, "reason": "尚未回调"}
        z = SupportResistanceZones.zone_containing(zones, seg_low)
        if z is None or z.low >= H:
            return {"signal": False, "reason": "回调低点未触及任何已确认支撑区"}
        if not VolumeAndOrderFlowConfirmation.selling_pressure_weakening(d, hi_idx + 1, i - 1):
            return {"signal": False, "reason": "回调期间未见卖压减弱（缩量下跌日）"}
        b = VolumeAndOrderFlowConfirmation.bar(d, i); prev_high = float(d.d["high"].iloc[i - 1])
        conf = close > z.high and close > prev_high and b["close_pos"] >= 0.6 and (np.isfinite(b["vol_rel20"]) and b["vol_rel20"] >= self.vol_rel_min)
        if not conf:
            return {"signal": False, "reason": f"等待确认：收盘>区域上沿={close > z.high}，收盘>昨高={close > prev_high}，收盘位置={b['close_pos']:.2f}，量比={b['vol_rel20']:.2f}"}
        inval = max(z.low - 0.5 * a, st["last_low"] - 0.25 * a)
        return {"signal": True, "zone": z, "invalidation": inval, "depth": depth, "reason": f"A 回调买入：回调 {depth:.0%} 至支撑区 [{z.low:.0f},{z.high:.0f}]（强度 {z.strength}），缩量后确认日收盘 {close:.0f} > 区域上沿与昨高，收盘位置 {b['close_pos']:.2f}，量比 {b['vol_rel20']:.2f}"}


class BreakoutRetestDetector:
    """策略 B：阻力区被收盘突破 → 3–25 日内回踩区域上沿 ±0.5 ATR → 企稳确认。"""

    def __init__(self, min_days: int = 3, max_days: int = 25, retest_atr: float = 0.5, buffer_atr: float = 0.1):
        self.min_days, self.max_days, self.retest_atr, self.buffer_atr = min_days, max_days, retest_atr, buffer_atr

    def check(self, d: DailyStructureDetector, zones_obj: SupportResistanceZones, i: int, zones: list[Zone]) -> dict:
        a = d.atr[i]; close = d.close[i]
        if not np.isfinite(a) or i < self.max_days + 2:
            return {"signal": False, "reason": "数据不足"}
        b = VolumeAndOrderFlowConfirmation.bar(d, i)
        for j in range(i - self.min_days, i - self.max_days - 1, -1):               # 突破日 j
            zj = zones_obj.zones_at(j - 1, d.close[j - 1]); res = SupportResistanceZones.resistances_above(zj, d.close[j - 1])
            if not res:
                continue
            z = res[0]; aj = d.atr[j]
            if not (np.isfinite(aj) and d.close[j] > z.high * (1 + self.buffer_atr * aj / d.close[j])):
                continue
            # 回踩日 r ∈ (j, i)
            for r in range(j + 1, i):
                lo = float(d.d["low"].iloc[r])
                if z.high - self.retest_atr * aj <= lo <= z.high + self.retest_atr * aj and d.close[r] >= z.low:
                    rh = float(d.d["high"].iloc[r])
                    if close > rh and close > z.high and np.isfinite(b["vol_rel20"]) and b["vol_rel20"] >= 0.8:
                        st = structure_state(d.sw, i); inval = max(z.low - 0.5 * a, (st["last_low"] - 0.25 * a) if st["last_low"] else -np.inf)
                        if inval >= close:
                            inval = z.low - 0.5 * a
                        return {"signal": True, "zone": z, "invalidation": inval, "breakout_idx": j, "retest_idx": r, "reason": f"B 突破回踩：{str(d.d['ts'].iloc[j].date())} 收盘突破阻力区 [{z.low:.0f},{z.high:.0f}]（强度 {z.strength}），{str(d.d['ts'].iloc[r].date())} 回踩上沿，今日收盘 {close:.0f} > 回踩日高点，量比 {b['vol_rel20']:.2f}"}
                    break                                                                   # 已有回踩但未确认 → 等待
        return {"signal": False, "reason": "无已确认的突破-回踩-企稳序列"}


class FailedBreakdownDetector:
    """策略 C（实验组）：收盘跌破支撑区下沿后 5 日内收复区域上沿，放量且收盘强。"""

    def __init__(self, max_days: int = 5):
        self.max_days = max_days

    def check(self, d: DailyStructureDetector, zones_obj: SupportResistanceZones, i: int, zones: list[Zone]) -> dict:
        a = d.atr[i]; close = d.close[i]
        if not np.isfinite(a) or i < 10:
            return {"signal": False, "reason": "数据不足"}
        b = VolumeAndOrderFlowConfirmation.bar(d, i)
        for j in range(i - 1, i - self.max_days - 1, -1):
            zj = zones_obj.zones_at(j - 1, d.close[j - 1]); z = SupportResistanceZones.nearest_support(zj, d.close[j - 1])
            if z is None or not d.close[j] < z.low:
                continue
            bl = float(d.d["low"].iloc[j:i + 1].min())
            if close > z.high and np.isfinite(b["vol_rel20"]) and b["vol_rel20"] >= 1.2 and b["close_pos"] >= 0.7:
                return {"signal": True, "zone": z, "invalidation": bl - 0.5 * a, "breakdown_idx": j, "reason": f"C 假跌破收复：{str(d.d['ts'].iloc[j].date())} 收盘跌破支撑区 [{z.low:.0f},{z.high:.0f}]，今日收盘 {close:.0f} 收复上沿，量比 {b['vol_rel20']:.2f}，收盘位置 {b['close_pos']:.2f}"}
            return {"signal": False, "reason": "跌破后尚未收复或量能/收盘不足"}
        return {"signal": False, "reason": "近 5 日无跌破"}
