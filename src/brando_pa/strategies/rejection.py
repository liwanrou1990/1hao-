"""策略 C：关键价位拒绝（Rejection）。

执行周期 K 线触及 1W/1D 区域（影线进入区域，收盘未越过区域到另一侧），并满足全部量化条件：
  振幅 ≥ range_atr_min×ATR；反向影线/振幅 ≥ wick_ratio_min；实体 ≥ body_atr_min×ATR；收盘位置 ≥ close_pct_min（多）/≤ 1−close_pct_min（空）；实体同向。
4h 结构过滤：逆势（4h 趋势与信号方向相反）默认不允许；allow_counter_trend=true 时要求影线比例 ≥ counter_trend_wick_ratio_min。
同一区域 cooldown_bars 根内不重复发信号。止损 = 影线极值外 sl_buffer_atr×ATR。
"""
from __future__ import annotations

from .base import BarContext, Signal, Strategy, choose_take_profit


class Rejection(Strategy):
    def on_bar(self, ctx: BarContext) -> Signal | None:
        p = self.p
        a = ctx.atr
        self.wait_reasons = []
        if ctx.rng < float(p["range_atr_min"]) * a:
            self.wait_reasons.append(f"C K 线振幅 {ctx.rng/a:.2f} ATR < {p['range_atr_min']}"); return None
        if ctx.body < float(p["body_atr_min"]) * a:
            self.wait_reasons.append("C 实体过小（十字星噪声）"); return None
        cooldown = int(p.get("cooldown_bars", 12))
        best: Signal | None = None
        for z in ctx.zones:
            if not self._zone_tfs_ok(z, p.get("zone_tfs", ["1w", "1d"])):
                continue
            last = self.last_signal_idx.get(z.zone_id)
            if last is not None and ctx.i - last < cooldown:
                continue
            entered = ctx.l <= z.hi and ctx.h >= z.lo
            if not entered:
                continue
            # 支撑拒绝 → 多：上一根与本根收盘都在区域下沿之上（未被收盘跌破），下影线长
            if ctx.prev_close >= z.lo and ctx.c >= z.lo and ctx.c > ctx.o:
                wick_ratio = ctx.lower_wick / ctx.rng
                ok_trend, why = self._trend_ok(ctx, "long", wick_ratio, p)
                if not ok_trend:
                    self.wait_reasons.append(f"C {z.zone_id} 支撑拒绝形态出现但{why}"); continue
                if wick_ratio >= float(p["wick_ratio_min"]) and ctx.close_pct >= float(p["close_pct_min"]):
                    stop = ctx.l - float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("long", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    sig = Signal(ctx.symbol, self.key, "long", ctx.c, stop, tp, z.to_dict(),
                                 [f"触及 {'/'.join(z.tfs)} 支撑区 [{z.lo:.2f},{z.hi:.2f}]，最低 {ctx.l:.2f}", f"下影线/振幅 {wick_ratio:.0%} ≥ {float(p['wick_ratio_min']):.0%}，收盘位置 {ctx.close_pct:.0%}，振幅 {ctx.rng/a:.2f} ATR",
                                  f"4h 结构 {ctx.trend_4h}{'（顺势）' if ctx.trend_4h == 'up' else ''}", tpr],
                                 f"收盘跌破拒绝 K 线最低 {ctx.l:.2f}", f"C:{z.zone_id}:long:{ctx.i}", ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, None, ctx.rel_vol)
                    if best is None or sig.rr_gross > best.rr_gross:
                        best = sig
                else:
                    self.wait_reasons.append(f"C {z.zone_id} 触及支撑但影线 {wick_ratio:.0%}/收盘位置 {ctx.close_pct:.0%} 不足")
            # 阻力拒绝 → 空
            if ctx.prev_close <= z.hi and ctx.c <= z.hi and ctx.c < ctx.o:
                wick_ratio = ctx.upper_wick / ctx.rng
                ok_trend, why = self._trend_ok(ctx, "short", wick_ratio, p)
                if not ok_trend:
                    self.wait_reasons.append(f"C {z.zone_id} 阻力拒绝形态出现但{why}"); continue
                if wick_ratio >= float(p["wick_ratio_min"]) and ctx.close_pct <= 1 - float(p["close_pct_min"]):
                    stop = ctx.h + float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("short", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    sig = Signal(ctx.symbol, self.key, "short", ctx.c, stop, tp, z.to_dict(),
                                 [f"触及 {'/'.join(z.tfs)} 阻力区 [{z.lo:.2f},{z.hi:.2f}]，最高 {ctx.h:.2f}", f"上影线/振幅 {wick_ratio:.0%} ≥ {float(p['wick_ratio_min']):.0%}，收盘位置 {ctx.close_pct:.0%}，振幅 {ctx.rng/a:.2f} ATR",
                                  f"4h 结构 {ctx.trend_4h}{'（顺势）' if ctx.trend_4h == 'down' else ''}", tpr],
                                 f"收盘突破拒绝 K 线最高 {ctx.h:.2f}", f"C:{z.zone_id}:short:{ctx.i}", ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, None, ctx.rel_vol)
                    if best is None or sig.rr_gross > best.rr_gross:
                        best = sig
                else:
                    self.wait_reasons.append(f"C {z.zone_id} 触及阻力但影线 {wick_ratio:.0%}/收盘位置 {ctx.close_pct:.0%} 不足")
        if best is not None:
            self.last_signal_idx[best.zone["zone_id"]] = ctx.i
        elif not self.wait_reasons:
            self.wait_reasons.append("C 本根未触及 1W/1D 关键区域")
        return best

    @staticmethod
    def _trend_ok(ctx: BarContext, direction: str, wick_ratio: float, p: dict) -> tuple[bool, str]:
        t = ctx.trend_4h
        counter = (direction == "long" and t == "down") or (direction == "short" and t == "up")
        if not counter:
            return True, ""
        if not bool(p.get("allow_counter_trend", False)):
            return False, f"4h 结构为 {t}，逆势不允许"
        if wick_ratio < float(p["counter_trend_wick_ratio_min"]):
            return False, f"逆势要求影线 ≥ {float(p['counter_trend_wick_ratio_min']):.0%}，实际 {wick_ratio:.0%}"
        return True, ""
