"""策略 A：突破 → 回踩 → 确认。

1. 触发（ARMED）：4h K 线收盘越过 1W/1D 区域边界 ≥ break_buffer_atr×ATR(4h)（引擎在 4h 收盘时生成 htf_events）。
2. 等待回踩：max_wait_retest_bars 根执行 K 线内，价格回到区域边界 ± retest_tolerance_atr×ATR(exec)；
   期间若执行 K 线收盘回到区域另一侧超过 invalidate_close_beyond_atr×ATR → 设置失效。
3. 确认：回踩后 confirm_within_bars 根内出现确认 K 线——收盘回到突破侧区域边界之外、收盘位于振幅上 confirm_close_pct（多）/下（空）、实体同向。
4. 信号：止损 = min(回踩最低, 区域下沿) − sl_buffer_atr×ATR（多；空对称）；止盈 = 下一区域前沿或 tp_rr×风险。
"""
from __future__ import annotations

from .base import BarContext, Signal, Strategy, choose_take_profit


class BreakoutRetest(Strategy):
    def on_bar(self, ctx: BarContext) -> Signal | None:
        p = self.p
        self.wait_reasons = []
        # 1) 新的 4h 突破事件 → 建立设置
        for ev in ctx.htf_events:
            z = ev["zone"]
            if not self._zone_tfs_ok(z, p.get("zone_tfs", ["1w", "1d"])):
                continue
            if ev["event"] not in ("break_up", "break_down"):
                continue
            direction = "long" if ev["event"] == "break_up" else "short"
            sid = f"A:{z.zone_id}:{direction}:{ctx.i}"
            if any(s["zone"].zone_id == z.zone_id and s["direction"] == direction for s in self.setups):
                continue
            self._count("setup_armed")
            self.setups.append({"id": sid, "zone": z, "direction": direction, "armed_idx": ctx.i, "retested": False, "retest_idx": None,
                                "retest_extreme": None, "break_rel_vol": ev.get("rel_vol"), "break_ts": str(ev.get("ts"))})
        if not self.setups:
            self.wait_reasons.append("无 4h 收盘有效突破 1W/1D 关键区域的设置")
            return None
        # 2) 推进每个设置
        a = ctx.atr
        keep: list[dict] = []
        signal: Signal | None = None
        for s in self.setups:
            z, d = s["zone"], s["direction"]
            inv = float(p["invalidate_close_beyond_atr"]) * a
            tol = float(p["retest_tolerance_atr"]) * a
            if d == "long" and ctx.c < z.lo - inv:
                self.wait_reasons.append(f"A {z.zone_id} 多头设置失效：收盘 {ctx.c:.2f} 回到区域下方"); self._count("setup_invalidated"); continue
            if d == "short" and ctx.c > z.hi + inv:
                self.wait_reasons.append(f"A {z.zone_id} 空头设置失效：收盘 {ctx.c:.2f} 回到区域上方"); self._count("setup_invalidated"); continue
            if not s["retested"] and ctx.i - s["armed_idx"] > int(p["max_wait_retest_bars"]):
                self.wait_reasons.append(f"A {z.zone_id} 超时未回踩，设置作废"); self._count("setup_expired_no_retest"); continue
            if d == "long":
                touched = ctx.l <= z.hi + tol
            else:
                touched = ctx.h >= z.lo - tol
            if touched:
                if ctx.i == s["armed_idx"]:
                    pass  # 突破根本身不算回踩
                else:
                    s["retested"] = True; s["retest_idx"] = ctx.i
                    ext = ctx.l if d == "long" else ctx.h
                    s["retest_extreme"] = ext if s["retest_extreme"] is None else (min(s["retest_extreme"], ext) if d == "long" else max(s["retest_extreme"], ext))
            if s["retested"] and s["retest_idx"] is not None and ctx.i - s["retest_idx"] > int(p["confirm_within_bars"]):
                self.wait_reasons.append(f"A {z.zone_id} 回踩后 {p['confirm_within_bars']} 根内未确认，设置作废"); self._count("setup_expired_no_confirm"); continue
            # 3) 确认
            if s["retested"] and signal is None and ctx.i > s["retest_idx"] or (s["retested"] and signal is None and ctx.i == s["retest_idx"] and self._confirm_same_bar(ctx, z, d, p)):
                cp = float(p["confirm_close_pct"])
                if d == "long" and ctx.c > z.hi and ctx.c > ctx.o and ctx.close_pct >= cp:
                    struct_px = min(s["retest_extreme"], z.lo); stop = struct_px - float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("long", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    signal = Signal(ctx.symbol, self.key, "long", ctx.c, stop, tp, z.to_dict(),
                                    [f"4h 收盘有效突破 {'/'.join(z.tfs)} 阻力区 [{z.lo:.2f},{z.hi:.2f}]（{s['break_ts'][:16]}）", f"回踩至 {s['retest_extreme']:.2f}（区域上沿 {z.hi:.2f}）",
                                     f"确认 K 线收盘 {ctx.c:.2f} 回到区域上方，收盘位置 {ctx.close_pct:.0%} ≥ {cp:.0%}", tpr],
                                    f"执行周期收盘跌破 {z.lo - float(p['invalidate_close_beyond_atr'])*a:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s.get("break_rel_vol"), ctx.rel_vol,
                                    structure_price=struct_px, invalidation_price=z.lo - float(p["invalidate_close_beyond_atr"]) * a, limit_price=min(ctx.c, z.hi))
                    continue
                if d == "short" and ctx.c < z.lo and ctx.c < ctx.o and ctx.close_pct <= 1 - cp:
                    struct_px = max(s["retest_extreme"], z.hi); stop = struct_px + float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("short", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    signal = Signal(ctx.symbol, self.key, "short", ctx.c, stop, tp, z.to_dict(),
                                    [f"4h 收盘有效跌破 {'/'.join(z.tfs)} 支撑区 [{z.lo:.2f},{z.hi:.2f}]（{s['break_ts'][:16]}）", f"反抽至 {s['retest_extreme']:.2f}（区域下沿 {z.lo:.2f}）",
                                     f"确认 K 线收盘 {ctx.c:.2f} 回到区域下方，收盘位置 {ctx.close_pct:.0%} ≤ {1-cp:.0%}", tpr],
                                    f"执行周期收盘站回 {z.hi + float(p['invalidate_close_beyond_atr'])*a:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s.get("break_rel_vol"), ctx.rel_vol,
                                    structure_price=struct_px, invalidation_price=z.hi + float(p["invalidate_close_beyond_atr"]) * a, limit_price=max(ctx.c, z.lo))
                    continue
            keep.append(s)
            self.wait_reasons.append(f"A {z.zone_id} {d} 等待{'确认' if s['retested'] else '回踩'}（已 {ctx.i - s['armed_idx']} 根）")
        self.setups = keep
        if signal is not None:
            self._count("signal_confirmed")
        return signal

    @staticmethod
    def _confirm_same_bar(ctx: BarContext, z, d: str, p: dict) -> bool:
        """回踩与确认发生在同一根（影线触及、收盘强势回到突破侧）。"""
        cp = float(p["confirm_close_pct"])
        if d == "long":
            return ctx.c > z.hi and ctx.c > ctx.o and ctx.close_pct >= cp
        return ctx.c < z.lo and ctx.c < ctx.o and ctx.close_pct <= 1 - cp
