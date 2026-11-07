"""策略 B：假突破 → 重新收复（Reclaim）。

* 触发：执行周期 K 线收盘越过区域边界，深度 ∈ [min_break_depth_atr, max_break_depth_atr]×ATR（太浅=噪声，太深=真突破）。
* 收复：随后 max_bars_outside 根内，收盘回到区域内/原侧（reclaim_level=lo 表示收回区域下沿即可；hi 要求收回上沿），
  且收盘位于振幅上 reclaim_close_pct（多）/下（空）；期间若收盘深度超过 max_break_depth → 视为真突破，放弃。
* 可选成交量：突破根相对成交量 ≥ break_volume_ratio_min（0 = 不要求，交给过滤模块）。
* 止损：假突破极值外 sl_buffer_atr×ATR；止盈：下一区域前沿或 tp_rr×风险。
与策略 C 的边界：B 必须至少出现一次“收盘在区域外”；C 只允许影线越过、收盘不越过。
"""
from __future__ import annotations

from .base import BarContext, Signal, Strategy, choose_take_profit


class Reclaim(Strategy):
    def on_bar(self, ctx: BarContext) -> Signal | None:
        p = self.p
        a = ctx.atr
        self.wait_reasons = []
        mn, mx = float(p["min_break_depth_atr"]) * a, float(p["max_break_depth_atr"]) * a
        # 1) 新的假突破候选：收盘越出区域（上一根收盘仍在原侧）
        for z in ctx.zones:
            if not self._zone_tfs_ok(z, p.get("zone_tfs", ["1w", "1d", "4h"])):
                continue
            if any(s["zone"].zone_id == z.zone_id for s in self.setups):
                continue
            if ctx.prev_close >= z.lo and ctx.c < z.lo and mn <= (z.lo - ctx.c) <= mx:
                self.setups.append({"id": f"B:{z.zone_id}:long:{ctx.i}", "zone": z, "direction": "long", "break_idx": ctx.i, "extreme": ctx.l, "break_rel_vol": ctx.rel_vol})
            elif ctx.prev_close <= z.hi and ctx.c > z.hi and mn <= (ctx.c - z.hi) <= mx:
                self.setups.append({"id": f"B:{z.zone_id}:short:{ctx.i}", "zone": z, "direction": "short", "break_idx": ctx.i, "extreme": ctx.h, "break_rel_vol": ctx.rel_vol})
        if not self.setups:
            self.wait_reasons.append("无执行周期收盘越出关键区域的假突破候选")
            return None
        keep: list[dict] = []
        signal: Signal | None = None
        lvl = p.get("reclaim_level", "lo")
        cp = float(p["reclaim_close_pct"])
        vol_min = float(p.get("break_volume_ratio_min", 0.0) or 0.0)
        for s in self.setups:
            z, d = s["zone"], s["direction"]
            if ctx.i == s["break_idx"]:
                keep.append(s); self.wait_reasons.append(f"B {z.zone_id} {d} 假突破候选建立，等待收复"); continue
            if ctx.i - s["break_idx"] > int(p["max_bars_outside"]):
                self.wait_reasons.append(f"B {z.zone_id} 在区域外超过 {p['max_bars_outside']} 根，视为真突破，放弃"); continue
            if d == "long":
                s["extreme"] = min(s["extreme"], ctx.l)
                if z.lo - ctx.c > mx:
                    self.wait_reasons.append(f"B {z.zone_id} 跌破深度超过 {p['max_break_depth_atr']} ATR，视为真突破"); continue
                reclaim_px = z.lo if lvl == "lo" else z.hi
                if ctx.c >= reclaim_px and ctx.c > ctx.o and ctx.close_pct >= cp and signal is None:
                    if vol_min and (s["break_rel_vol"] is None or s["break_rel_vol"] < vol_min):
                        self.wait_reasons.append(f"B {z.zone_id} 突破根成交量不足（{s['break_rel_vol']}）"); continue
                    stop = s["extreme"] - float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("long", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    signal = Signal(ctx.symbol, self.key, "long", ctx.c, stop, tp, z.to_dict(),
                                    [f"收盘跌破 {'/'.join(z.tfs)} 支撑区 [{z.lo:.2f},{z.hi:.2f}] 深度 {(z.lo - min(s['extreme'], z.lo))/a:.2f} ATR（第 {s['break_idx']} 根）",
                                     f"{ctx.i - s['break_idx']} 根内收复：收盘 {ctx.c:.2f} ≥ {reclaim_px:.2f}，收盘位置 {ctx.close_pct:.0%} ≥ {cp:.0%}", tpr],
                                    f"收盘再次跌破假突破低点 {s['extreme']:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s["break_rel_vol"], ctx.rel_vol)
                    continue
            else:
                s["extreme"] = max(s["extreme"], ctx.h)
                if ctx.c - z.hi > mx:
                    self.wait_reasons.append(f"B {z.zone_id} 突破深度超过 {p['max_break_depth_atr']} ATR，视为真突破"); continue
                reclaim_px = z.hi if lvl == "lo" else z.lo
                if ctx.c <= reclaim_px and ctx.c < ctx.o and ctx.close_pct <= 1 - cp and signal is None:
                    if vol_min and (s["break_rel_vol"] is None or s["break_rel_vol"] < vol_min):
                        self.wait_reasons.append(f"B {z.zone_id} 突破根成交量不足"); continue
                    stop = s["extreme"] + float(p["sl_buffer_atr"]) * a
                    tp, tpz, tpr = choose_take_profit("short", ctx.c, stop, ctx.zones, p["tp_mode"], float(p["tp_rr"]), 1.0)
                    signal = Signal(ctx.symbol, self.key, "short", ctx.c, stop, tp, z.to_dict(),
                                    [f"收盘突破 {'/'.join(z.tfs)} 阻力区 [{z.lo:.2f},{z.hi:.2f}] 深度 {(max(s['extreme'], z.hi) - z.hi)/a:.2f} ATR（第 {s['break_idx']} 根）",
                                     f"{ctx.i - s['break_idx']} 根内跌回：收盘 {ctx.c:.2f} ≤ {reclaim_px:.2f}，收盘位置 {ctx.close_pct:.0%} ≤ {1-cp:.0%}", tpr],
                                    f"收盘再次突破假突破高点 {s['extreme']:.2f}", s["id"], ctx.i, ctx.ts, a, p.get("trail_after_rr"), p.get("max_hold_bars"), tpz, s["break_rel_vol"], ctx.rel_vol)
                    continue
            keep.append(s)
            self.wait_reasons.append(f"B {z.zone_id} {d} 等待收复（区域外第 {ctx.i - s['break_idx']} 根）")
        self.setups = keep
        return signal
