"""第三阶段受控研究模式（只用已收盘执行 K 线与已确认摆动点，跨周期因果由 MarketContext 保证）。

共同的“微结构确认”机制（以多头为例；空头镜像）：
  1. 价格到达关键区域：执行 K 线最低价进入 [zone.lo − tol, zone.hi + tol]（tol = touch_tol_atr × ATR）→ 记录 touch_idx。
  2. 微结构低点：触及之后，执行周期出现一个**已确认**的摆动低点（分形 k = micro_k，第 i+k 根收盘才确认），且该低点位于区域附近（≤ zone.hi + tol、≥ zone.lo − max_undercut_atr×ATR）。
  3. 微结构突破（BOS）：确认后，收盘高于 “摆动低点之后到确认之间的最高价”（micro_high）→ 入场信号（下一根开盘成交）。
  4. 止损候选：micro_stop = 摆动低点 − micro_buffer_atr×ATR（默认 stop）；structure_price = 区域另一侧（zone.lo）；atr_buffer 版 = zone.lo − sl_buffer_atr×ATR。
     exits.stop_mode 决定实际使用哪一个；初始风险在入场时确定。
  5. 失效：收盘跌破 zone.lo − invalidate_atr×ATR（支撑丢失）或超过 max_bars_after_touch 根未触发 → 放弃。

模式 A（StructureEntryAtZone）：不要求事先突破；区域为当前价格下方的支撑（多）/ 上方的阻力（空），来自 zone_tfs。
模式 B（BreakoutSecondConfirm）：先要求 4h 收盘有效突破 1W/1D 区域（与原策略 A 相同的 htf_events），然后等待**首次回踩**进入区域并完成微结构确认；
  若价格直接离开没有回踩（max_wait_retest_bars 内未触及）→ 放弃（允许错过）。
两者分别独立统计。参数空间在 config 中固定且很小（预先登记）。
"""
from __future__ import annotations

from .base import BarContext, Signal, Strategy, choose_take_profit


def _micro_state_machine(self: Strategy, ctx: BarContext, s: dict, p: dict, swings: list) -> Signal | None:
    """推进一个已触及区域的设置。swings：执行周期已确认摆动点列表（来自 ctx.extras['exec_swings']，按 idx 升序，只含 confirmed_idx ≤ ctx.i）。"""
    z, d, a = s["zone"], s["direction"], ctx.atr
    tol = float(p["touch_tol_atr"]) * a
    inv = float(p["invalidate_atr"]) * a
    if d == "long":
        if ctx.c < z.lo - inv:
            self._count("setup_invalidated"); return "drop"
    else:
        if ctx.c > z.hi + inv:
            self._count("setup_invalidated"); return "drop"
    if ctx.i - s["touch_idx"] > int(p["max_bars_after_touch"]):
        self._count("setup_timeout"); return "drop"
    # 寻找触及之后的已确认微结构极值
    if s.get("micro_idx") is None:
        cand = [sw for sw in swings if sw.idx >= s["touch_idx"] and sw.confirmed_idx <= ctx.i and sw.kind == ("L" if d == "long" else "H")]
        for sw in cand:
            near = (sw.price <= z.hi + tol and sw.price >= z.lo - float(p["max_undercut_atr"]) * a) if d == "long" else (sw.price >= z.lo - tol and sw.price <= z.hi + float(p["max_undercut_atr"]) * a)
            if near:
                s["micro_idx"], s["micro_price"], s["micro_confirmed_idx"] = sw.idx, sw.price, sw.confirmed_idx
                # micro_high：从摆动低点之后到确认根（含）之间的最高价（多）；空头取最低价
                seg = ctx.extras["exec_h"][sw.idx + 1: sw.confirmed_idx + 1] if d == "long" else ctx.extras["exec_l"][sw.idx + 1: sw.confirmed_idx + 1]
                s["micro_bos"] = float(max(seg)) if (d == "long" and len(seg)) else (float(min(seg)) if len(seg) else None)
                self._count("micro_structure_found")
                break
    if s.get("micro_idx") is None or s.get("micro_bos") is None:
        return None
    if ctx.i <= s["micro_confirmed_idx"]:
        return None                                             # 确认根本身不能同时作为突破根（避免用同根信息）
    # 微结构突破：收盘越过 micro_bos
    if d == "long" and ctx.c > s["micro_bos"] and ctx.c > ctx.o:
        micro_stop = s["micro_price"] - float(p["micro_buffer_atr"]) * a
        struct_stop = z.lo - float(p["sl_buffer_atr"]) * a
        stop = micro_stop if p.get("default_stop", "micro") == "micro" else struct_stop
        tp, tpz, tpr = choose_take_profit("long", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
        self._count("signal_confirmed")
        return Signal(ctx.symbol, self.key, "long", ctx.c, stop, tp, z.to_dict(),
                      [f"价格触及 {'/'.join(z.tfs)} 支撑区 [{z.lo:.2f},{z.hi:.2f}]（第 {s['touch_idx']} 根）" + (f"，此前 4h 有效突破（{s.get('break_ts', '')[:16]}）" if s.get("break_ts") else ""),
                       f"执行周期已确认摆动低 {s['micro_price']:.2f}（第 {s['micro_idx']} 根，第 {s['micro_confirmed_idx']} 根确认）", f"收盘 {ctx.c:.2f} 突破微结构高点 {s['micro_bos']:.2f}", tpr],
                      f"收盘跌破微结构低点 {s['micro_price']:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s.get("break_rel_vol"), ctx.rel_vol,
                      structure_price=z.lo, invalidation_price=s["micro_price"], limit_price=min(ctx.c, s["micro_bos"]), micro_stop_price=micro_stop,
                      diag={"zone_lo": z.lo, "zone_hi": z.hi, "zone_height_atr": (z.hi - z.lo) / a, "touch_idx": s["touch_idx"], "micro_price": s["micro_price"], "micro_bos": s["micro_bos"],
                            "micro_to_zone_edge_atr": (z.hi - s["micro_price"]) / a, "entry_to_micro_atr": (ctx.c - s["micro_price"]) / a, "entry_to_zone_far_atr": (ctx.c - z.lo) / a,
                            "bars_touch_to_entry": ctx.i - s["touch_idx"], "micro_stop_atr": (ctx.c - micro_stop) / a, "struct_stop_atr": (ctx.c - struct_stop) / a})
    if d == "short" and ctx.c < s["micro_bos"] and ctx.c < ctx.o:
        micro_stop = s["micro_price"] + float(p["micro_buffer_atr"]) * a
        struct_stop = z.hi + float(p["sl_buffer_atr"]) * a
        stop = micro_stop if p.get("default_stop", "micro") == "micro" else struct_stop
        tp, tpz, tpr = choose_take_profit("short", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
        self._count("signal_confirmed")
        return Signal(ctx.symbol, self.key, "short", ctx.c, stop, tp, z.to_dict(),
                      [f"价格触及 {'/'.join(z.tfs)} 阻力区 [{z.lo:.2f},{z.hi:.2f}]（第 {s['touch_idx']} 根）" + (f"，此前 4h 有效跌破（{s.get('break_ts', '')[:16]}）" if s.get("break_ts") else ""),
                       f"执行周期已确认摆动高 {s['micro_price']:.2f}（第 {s['micro_idx']} 根，第 {s['micro_confirmed_idx']} 根确认）", f"收盘 {ctx.c:.2f} 跌破微结构低点 {s['micro_bos']:.2f}", tpr],
                      f"收盘突破微结构高点 {s['micro_price']:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s.get("break_rel_vol"), ctx.rel_vol,
                      structure_price=z.hi, invalidation_price=s["micro_price"], limit_price=max(ctx.c, s["micro_bos"]), micro_stop_price=micro_stop,
                      diag={"zone_lo": z.lo, "zone_hi": z.hi, "zone_height_atr": (z.hi - z.lo) / a, "touch_idx": s["touch_idx"], "micro_price": s["micro_price"], "micro_bos": s["micro_bos"],
                            "micro_to_zone_edge_atr": (s["micro_price"] - z.lo) / a, "entry_to_micro_atr": (s["micro_price"] - ctx.c) / a, "entry_to_zone_far_atr": (z.hi - ctx.c) / a,
                            "bars_touch_to_entry": ctx.i - s["touch_idx"], "micro_stop_atr": (micro_stop - ctx.c) / a, "struct_stop_atr": (struct_stop - ctx.c) / a})
    return None


class StructureEntryAtZone(Strategy):
    """模式 A：到达关键区域 → 执行周期微结构确认入场。"""

    def on_bar(self, ctx: BarContext) -> Signal | None:
        p = self.p; a = ctx.atr; self.wait_reasons = []
        swings = ctx.extras.get("exec_swings", [])
        tol = float(p["touch_tol_atr"]) * a
        allow_counter = bool(p.get("allow_counter_trend", False))
        # 新触及
        for z in ctx.zones:
            if not self._zone_tfs_ok(z, p.get("zone_tfs", ["1w", "1d"])):
                continue
            if any(s["zone"].zone_id == z.zone_id for s in self.setups):
                continue
            if ctx.prev_close > z.hi and ctx.l <= z.hi + tol and ctx.c >= z.lo - float(p["invalidate_atr"]) * a:
                if ctx.trend_4h == "down" and not allow_counter:
                    self._count("blocked_counter_trend"); continue
                self._count("setup_armed"); self.setups.append({"id": f"A2:{z.zone_id}:long:{ctx.i}", "zone": z, "direction": "long", "touch_idx": ctx.i})
            elif ctx.prev_close < z.lo and ctx.h >= z.lo - tol and ctx.c <= z.hi + float(p["invalidate_atr"]) * a:
                if ctx.trend_4h == "up" and not allow_counter:
                    self._count("blocked_counter_trend"); continue
                self._count("setup_armed"); self.setups.append({"id": f"A2:{z.zone_id}:short:{ctx.i}", "zone": z, "direction": "short", "touch_idx": ctx.i})
        keep, signal = [], None
        for s in self.setups:
            r = _micro_state_machine(self, ctx, s, p, swings) if signal is None else None
            if r == "drop":
                continue
            if isinstance(r, Signal):
                signal = r; continue
            keep.append(s); self.wait_reasons.append(f"A2 {s['zone'].zone_id} {s['direction']} 等待微结构（触及后第 {ctx.i - s['touch_idx']} 根，微结构{'已' if s.get('micro_idx') is not None else '未'}确认）")
        self.setups = keep
        if not self.setups and signal is None:
            self.wait_reasons.append("A2 无触及 1W/1D 区域的设置")
        return signal


class BreakoutSecondConfirm(Strategy):
    """模式 B：4h 有效突破 → 等待首次回踩进入区域 → 执行周期微结构确认入场；无回踩则放弃。"""

    def on_bar(self, ctx: BarContext) -> Signal | None:
        p = self.p; a = ctx.atr; self.wait_reasons = []
        swings = ctx.extras.get("exec_swings", [])
        tol = float(p["touch_tol_atr"]) * a
        for ev in ctx.htf_events:
            z = ev["zone"]
            if not self._zone_tfs_ok(z, p.get("zone_tfs", ["1w", "1d"])) or ev["event"] not in ("break_up", "break_down"):
                continue
            d = "long" if ev["event"] == "break_up" else "short"
            if any(s["zone"].zone_id == z.zone_id and s["direction"] == d for s in self.setups):
                continue
            self._count("setup_armed")
            self.setups.append({"id": f"A3:{z.zone_id}:{d}:{ctx.i}", "zone": z, "direction": d, "armed_idx": ctx.i, "touch_idx": None, "break_rel_vol": ev.get("rel_vol"), "break_ts": str(ev.get("ts"))})
        keep, signal = [], None
        for s in self.setups:
            z, d = s["zone"], s["direction"]
            if s["touch_idx"] is None:
                if ctx.i - s["armed_idx"] > int(p["max_wait_retest_bars"]):
                    self._count("missed_no_pullback"); continue                       # 直接走了，允许错过
                touched = (ctx.l <= z.hi + tol) if d == "long" else (ctx.h >= z.lo - tol)
                if touched and ctx.i > s["armed_idx"]:
                    s["touch_idx"] = ctx.i; self._count("pullback_touched")
                else:
                    keep.append(s); self.wait_reasons.append(f"A3 {z.zone_id} {d} 等待首次回踩（突破后第 {ctx.i - s['armed_idx']} 根）"); continue
            r = _micro_state_machine(self, ctx, s, p, swings) if signal is None else None
            if r == "drop":
                continue
            if isinstance(r, Signal):
                signal = r; continue
            keep.append(s); self.wait_reasons.append(f"A3 {z.zone_id} {d} 回踩后等待微结构确认（第 {ctx.i - s['touch_idx']} 根）")
        self.setups = keep
        if not self.setups and signal is None:
            self.wait_reasons.append("A3 无 4h 有效突破后的回踩设置")
        return signal
