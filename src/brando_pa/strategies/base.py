"""策略基类与数据结构。

BarContext：引擎在每根执行周期 K 线收盘后构造，只包含“此刻已知”的信息（已收盘 K 线、已确认摆动点、已收盘高周期区域）。
Signal：候选交易（方向、参考入场价=当前收盘、结构止损、止盈、毛盈亏比、理由、失效条件）；是否可执行由过滤器与风控决定。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis.levels import Zone, nearest_zone


@dataclass
class BarContext:
    symbol: str
    i: int
    ts: pd.Timestamp
    o: float; h: float; l: float; c: float
    prev_close: float
    volume: float
    rel_vol: float
    atr: float                                  # 执行周期 ATR
    atr_pct: float
    zones: list[Zone]                           # 跨周期合并后的区域（相对当前价格的角色由 zone.role(c) 决定）
    zones_by_tf: dict[str, list[Zone]]
    trend_4h: str                               # up / down / range / undefined
    regime: dict                                # 日线环境：{trend: strong_up/up/range/down/strong_down, highvol: bool, adx: float}
    htf_events: list[dict]                      # 本决策点刚收盘的 4h K 线对区域的事件 [{zone, event, rel_vol, ts}]
    extras: dict = field(default_factory=dict)  # funding_rate / netflow_z / 等可选 PIT 数据

    @property
    def rng(self) -> float:
        return max(self.h - self.l, 1e-12)

    @property
    def close_pct(self) -> float:
        """收盘在振幅中的位置 0=最低 1=最高。"""
        return (self.c - self.l) / self.rng

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def upper_wick(self) -> float:
        return self.h - max(self.o, self.c)

    @property
    def lower_wick(self) -> float:
        return min(self.o, self.c) - self.l


@dataclass
class Signal:
    symbol: str
    strategy: str
    direction: str                      # long / short
    ref_price: float                    # 信号 K 线收盘（实际按下一根开盘成交）
    stop_price: float
    take_profit: float
    zone: dict
    reasons: list[str]
    invalidation: str
    setup_id: str
    bar_idx: int
    ts: pd.Timestamp
    atr: float
    trail_after_rr: float | None = None
    max_hold_bars: int | None = None
    tp_zone: dict | None = None
    break_rel_vol: float | None = None  # 突破/假突破那根 K 线的相对成交量（供过滤器使用）
    signal_rel_vol: float | None = None
    structure_price: float | None = None   # 结构失效点（不含 ATR 缓冲）；stop_mode=fixed_structure 时用它作止损
    invalidation_price: float | None = None  # 执行周期收盘越过此价 → 结构失效（structure_exit 用）
    limit_price: float | None = None       # 入场模式 2 的挂单价（由引擎按区域边界计算）
    micro_stop_price: float | None = None  # 低周期微结构止损位（stop_mode=micro 时使用）
    diag: dict = field(default_factory=dict)  # 逐笔诊断：突破价/回踩价/确认价/区域边界/结构点/微结构点 等

    @property
    def risk_per_unit(self) -> float:
        return abs(self.ref_price - self.stop_price)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.take_profit - self.ref_price)

    @property
    def rr_gross(self) -> float:
        return self.reward_per_unit / self.risk_per_unit if self.risk_per_unit > 0 else 0.0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "strategy": self.strategy, "direction": self.direction, "ref_price": self.ref_price, "stop_price": self.stop_price,
                "take_profit": self.take_profit, "rr_gross": round(self.rr_gross, 3), "zone": self.zone, "tp_zone": self.tp_zone, "reasons": self.reasons,
                "invalidation": self.invalidation, "setup_id": self.setup_id, "bar_idx": self.bar_idx, "ts": self.ts.isoformat(), "atr": self.atr,
                "trail_after_rr": self.trail_after_rr, "max_hold_bars": self.max_hold_bars, "break_rel_vol": self.break_rel_vol, "signal_rel_vol": self.signal_rel_vol,
                "structure_price": self.structure_price, "invalidation_price": self.invalidation_price, "limit_price": self.limit_price,
                "micro_stop_price": self.micro_stop_price, "diag": self.diag}


def choose_take_profit(direction: str, ref: float, stop: float, zones: list[Zone], tp_mode: str, tp_rr: float, min_rr: float) -> tuple[float, dict | None, str]:
    """止盈：下一关键区域前沿（若盈亏比 ≥ min_rr），否则按 tp_rr 倍风险。"""
    risk = abs(ref - stop)
    rr_tp = ref + tp_rr * risk if direction == "long" else ref - tp_rr * risk
    if tp_mode == "rr":
        return rr_tp, None, f"止盈 = {tp_rr}R"
    z = nearest_zone(zones, ref, "up" if direction == "long" else "down")
    if z is not None:
        front = z.lo if direction == "long" else z.hi
        rr = abs(front - ref) / risk if risk > 0 else 0
        if rr >= min_rr:
            return float(front), z.to_dict(), f"止盈 = 下一关键区域前沿 {front:.2f}（{rr:.2f}R，{'/'.join(z.tfs)}）"
        return rr_tp, z.to_dict(), f"下一区域 {front:.2f} 仅 {rr:.2f}R 不足，止盈改用 {tp_rr}R"
    return rr_tp, None, f"上方/下方无已识别区域，止盈 = {tp_rr}R"


class Strategy:
    key: str = "base"

    def __init__(self, key: str, params: dict):
        self.key = key
        self.p = dict(params)
        self.reset()

    def reset(self) -> None:
        self.setups: list[dict] = []
        self.last_signal_idx: dict[str, int] = {}
        self.wait_reasons: list[str] = []
        self.counters: dict[str, int] = {}

    def _count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def on_bar(self, ctx: BarContext) -> Signal | None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def state_summary(self) -> dict:
        return {"active_setups": [{k: (v if not isinstance(v, float) else round(v, 4)) for k, v in s.items() if k != "zone"} | {"zone": s["zone"].to_dict()} for s in self.setups],
                "wait_reasons": self.wait_reasons[-5:]}

    @staticmethod
    def _zone_tfs_ok(z: Zone, tfs: list[str]) -> bool:
        return any(t in tfs for t in (z.tfs or [z.tf]))

    @staticmethod
    def _finite(*xs: float) -> bool:
        return all(np.isfinite(x) for x in xs)
