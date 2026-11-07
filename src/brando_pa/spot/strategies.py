"""纯现货价格行为策略（执行周期本地结构）。全部只用已收盘 K 线与已确认摆动点。

A_breakout_retest_buy：收盘有效突破本地阻力（已确认摆动高、未被突破过）→ 回踩至阻力位附近 → 确认收盘回到阻力上方 → BUY；止损 = min(回踩低点, 阻力位) − buffer。
B_breakdown_reclaim_buy：收盘跌破本地支撑（深度 ∈ [min, max] ATR）→ N 根内收盘收复支撑且收盘强势 → BUY；止损 = 跌破期间最低价 − buffer。
C_resistance_rejection_sell：持有现货时，价格触及本地阻力或高周期阻力区并出现量化拒绝 K 线 → SELL（卖出 sell_fraction）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .structure import LocalLevel


@dataclass
class SpotSignal:
    action: str                 # BUY / SELL
    strategy: str
    symbol: str
    ref_price: float
    stop: float | None
    reasons: list[str]
    diag: dict = field(default_factory=dict)
    sell_fraction: float = 1.0
    setup_id: str = ""


class SpotBar:
    """引擎每根传给策略的只读上下文。"""
    def __init__(self, symbol, i, ts, o, h, l, c, prev_close, atr, res: list[LocalLevel], sup: list[LocalLevel], all_lv: list[LocalLevel], htf_res_zones: list, regime: dict, holding_qty: float):
        self.symbol, self.i, self.ts, self.o, self.h, self.l, self.c, self.prev_close, self.atr = symbol, i, ts, o, h, l, c, prev_close, atr
        self.res, self.sup, self.all_lv, self.htf_res_zones, self.regime, self.holding_qty = res, sup, all_lv, htf_res_zones, regime, holding_qty

    @property
    def rng(self) -> float:
        return max(self.h - self.l, 1e-12)

    @property
    def close_pct(self) -> float:
        return (self.c - self.l) / self.rng


class BreakoutRetestBuy:
    key = "A_breakout_retest_buy"

    def __init__(self, p: dict):
        self.p = p; self.setups: list[dict] = []; self.counters: dict = {}; self.wait: list[str] = []

    def _count(self, k):
        self.counters[k] = self.counters.get(k, 0) + 1

    def on_bar(self, b: SpotBar) -> SpotSignal | None:
        p, a = self.p, b.atr; self.wait = []
        buf = float(p["break_buffer_atr"]) * a
        # 1) 新突破：上一根收盘在阻力之下、本根收盘越过阻力 ≥ buf（阻力此前未被突破）
        for lv in b.all_lv:
            if lv.kind != "R" or lv.broken:
                continue
            if b.prev_close <= lv.price and b.c > lv.price + buf and not any(s["level"] == lv.price for s in self.setups):
                self._count("setup_armed"); self.setups.append({"id": f"A:{lv.price:.2f}:{b.i}", "level": lv.price, "armed_idx": b.i, "retested": False, "retest_idx": None, "retest_low": None, "touches": lv.touches})
        keep, sig = [], None
        tol = float(p["retest_tol_atr"]) * a
        for s in self.setups:
            R = s["level"]
            if b.c < R - buf:
                self._count("setup_invalidated"); continue                       # 收盘回到阻力下方 → 突破失败
            if not s["retested"] and b.i - s["armed_idx"] > int(p["max_wait_retest_bars"]):
                self._count("setup_expired_no_retest"); continue
            if b.i > s["armed_idx"] and b.l <= R + tol:
                s["retested"] = True; s["retest_idx"] = b.i; s["retest_low"] = b.l if s["retest_low"] is None else min(s["retest_low"], b.l)
            if s["retested"] and b.i - s["retest_idx"] > int(p["confirm_within_bars"]):
                self._count("setup_expired_no_confirm"); continue
            if s["retested"] and sig is None and b.c > R and b.c > b.o and b.close_pct >= float(p["confirm_close_pct"]):
                stop = min(s["retest_low"], R) - float(p["stop_buffer_atr"]) * a
                self._count("signal")
                sig = SpotSignal("BUY", self.key, b.symbol, b.c, stop, [f"收盘有效突破本地阻力 {R:.2f}（已确认摆动高，触及 {s['touches']} 次）", f"回踩至 {s['retest_low']:.2f} 后确认收盘 {b.c:.2f} 回到阻力上方（收盘位置 {b.close_pct:.0%}）", f"结构失效止损 {stop:.2f}（{(b.c - stop) / a:.2f} ATR）"],
                                 {"level": R, "retest_low": s["retest_low"], "confirm_close": b.c, "stop_atr": (b.c - stop) / a, "bars_armed_to_retest": s["retest_idx"] - s["armed_idx"], "bars_retest_to_confirm": b.i - s["retest_idx"]}, setup_id=s["id"])
                continue
            keep.append(s)
        self.setups = keep
        return sig


class BreakdownReclaimBuy:
    key = "B_breakdown_reclaim_buy"

    def __init__(self, p: dict):
        self.p = p; self.setups: list[dict] = []; self.counters: dict = {}; self.wait: list[str] = []

    def _count(self, k):
        self.counters[k] = self.counters.get(k, 0) + 1

    def on_bar(self, b: SpotBar) -> SpotSignal | None:
        p, a = self.p, b.atr
        mn, mx = float(p["min_break_depth_atr"]) * a, float(p["max_break_depth_atr"]) * a
        for lv in b.all_lv:
            if lv.kind != "S" or lv.broken:
                continue
            if b.prev_close >= lv.price and b.c < lv.price and mn <= lv.price - b.c <= mx and not any(s["level"] == lv.price for s in self.setups):
                self._count("setup_armed"); self.setups.append({"id": f"B:{lv.price:.2f}:{b.i}", "level": lv.price, "break_idx": b.i, "extreme": b.l, "touches": lv.touches})
        keep, sig = [], None
        for s in self.setups:
            S = s["level"]
            if b.i == s["break_idx"]:
                keep.append(s); continue
            s["extreme"] = min(s["extreme"], b.l)
            if S - b.c > mx:
                self._count("setup_true_break"); continue
            if b.i - s["break_idx"] > int(p["max_bars_outside"]):
                self._count("setup_timeout"); continue
            if sig is None and b.c >= S and b.c > b.o and b.close_pct >= float(p["reclaim_close_pct"]):
                stop = s["extreme"] - float(p["stop_buffer_atr"]) * a
                self._count("signal")
                sig = SpotSignal("BUY", self.key, b.symbol, b.c, stop, [f"收盘跌破本地支撑 {S:.2f}（深度 {(S - s['extreme']) / a:.2f} ATR，第 {s['break_idx']} 根）", f"{b.i - s['break_idx']} 根内收复：收盘 {b.c:.2f} ≥ {S:.2f}，收盘位置 {b.close_pct:.0%}", f"结构失效止损 {stop:.2f}（{(b.c - stop) / a:.2f} ATR）"],
                                 {"level": S, "extreme": s["extreme"], "depth_atr": (S - s["extreme"]) / a, "stop_atr": (b.c - stop) / a, "bars_outside": b.i - s["break_idx"]}, setup_id=s["id"])
                continue
            keep.append(s)
        self.setups = keep
        return sig


class ResistanceRejectionSell:
    key = "C_resistance_rejection_sell"

    def __init__(self, p: dict):
        self.p = p; self.counters: dict = {}; self.last_idx: int = -10**9; self.wait: list[str] = []

    def _count(self, k):
        self.counters[k] = self.counters.get(k, 0) + 1

    def on_bar(self, b: SpotBar) -> SpotSignal | None:
        p, a = self.p, b.atr
        if b.holding_qty <= 0 or b.i - self.last_idx < int(p["cooldown_bars"]):
            return None
        if b.rng < float(p["range_atr_min"]) * a or b.c >= b.o:
            return None
        wick = (b.h - max(b.o, b.c)) / b.rng
        if wick < float(p["wick_ratio_min"]) or b.close_pct > float(p["close_pct_max"]):
            return None
        tol = 0.15 * a
        hit = None
        for lv in b.res + [l for l in b.all_lv if l.kind == "R" and not l.broken]:
            if b.h >= lv.price - tol and b.c < lv.price:
                hit = ("本地阻力", lv.price); break
        if hit is None and p.get("use_htf_resistance", True):
            for z in b.htf_res_zones:
                if b.h >= z.lo and b.c < z.hi:
                    hit = (f"高周期阻力区 {'/'.join(z.tfs)}", z.lo); break
        if hit is None:
            return None
        self.last_idx = b.i; self._count("signal")
        return SpotSignal("SELL", self.key, b.symbol, b.c, None, [f"触及{hit[0]} {hit[1]:.2f}，上影线/振幅 {wick:.0%} ≥ {float(p['wick_ratio_min']):.0%}，收盘位置 {b.close_pct:.0%}，振幅 {b.rng / a:.2f} ATR", f"卖出持仓 {float(p['sell_fraction']):.0%}"],
                          {"level": hit[1], "wick_ratio": wick, "close_pct": b.close_pct}, sell_fraction=float(p["sell_fraction"]), setup_id=f"C:{hit[1]:.2f}:{b.i}")
