"""brando_pa 三套策略的状态机：用合成 BarContext 逐根驱动，验证触发条件、失效与信号参数。"""
import pandas as pd

from brando_pa.analysis.levels import Zone
from brando_pa.config import cfg
from brando_pa.strategies import make_strategies
from brando_pa.strategies.base import BarContext

Z = Zone("1d", 99.0, 101.0, 100.0, [100.0], ["L"], 0, touches=3, strength=5.0, tfs=["1d"], zone_id="1d:100.00")
Z_UP = Zone("1d", 109.0, 111.0, 110.0, [110.0], ["H"], 0, touches=2, strength=4.0, tfs=["1d"], zone_id="1d:110.00")
ATR = 1.0


def ctx(i, o, h, l, c, prev_close, htf_events=(), trend="up", regime_trend="up", rel_vol=1.5):
    return BarContext("T", i, pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(minutes=15 * i), o, h, l, c, prev_close, 100.0, rel_vol, ATR, ATR / c, [Z, Z_UP], {"1d": [Z, Z_UP]}, trend,
                      {"trend": regime_trend, "highvol": False, "adx": 25.0}, list(htf_events), {"bar_seconds": 900})


def test_strategy_A_break_retest_confirm_long():
    A = make_strategies(cfg()["strategies"])["A_breakout_retest"]
    # 第 1 根：4h 收盘突破 Z_UP（阻力）→ 建立多头设置
    ev = [{"zone": Z_UP, "event": "break_up", "rel_vol": 1.8, "ts": pd.Timestamp("2024-01-01", tz="UTC")}]
    assert A.on_bar(ctx(1, 111.5, 112.5, 111.2, 112.0, 111.0, ev)) is None and len(A.setups) == 1
    # 第 2 根：回踩进入区域上沿附近（low 110.9 ≤ hi 111 + 0.25 ATR），收盘弱 → 仅记录回踩
    assert A.on_bar(ctx(2, 112.0, 112.1, 110.9, 111.3, 112.0)) is None and A.setups[0]["retested"]
    # 第 3 根：确认 K 线：收盘 > hi，阳线，收盘位置 ≥ 60%
    sig = A.on_bar(ctx(3, 111.3, 112.8, 111.1, 112.6, 111.3))
    assert sig is not None and sig.direction == "long" and sig.strategy == "A_breakout_retest"
    assert sig.stop_price < min(110.9, Z_UP.lo) and sig.take_profit > sig.ref_price and sig.rr_gross > 1
    assert sig.break_rel_vol == 1.8 and not A.setups


def test_strategy_A_invalidation_on_close_back_inside():
    A = make_strategies(cfg()["strategies"])["A_breakout_retest"]
    ev = [{"zone": Z_UP, "event": "break_up", "rel_vol": 1.8, "ts": pd.Timestamp("2024-01-01", tz="UTC")}]
    A.on_bar(ctx(1, 111.5, 112.5, 111.2, 112.0, 111.0, ev))
    A.on_bar(ctx(2, 112.0, 112.1, 108.0, 108.5, 112.0))     # 收盘回到区域下方 → 失效
    assert not A.setups and any("失效" in r for r in A.wait_reasons)


def test_strategy_B_reclaim_long_and_too_deep_abandon():
    B = make_strategies(cfg()["strategies"])["B_reclaim"]
    # 收盘跌破支撑 Z（lo=99）深度 0.5 ATR（在 [0.15, 1.5] 内）
    assert B.on_bar(ctx(1, 99.5, 99.6, 98.3, 98.5, 99.5)) is None and len(B.setups) == 1
    # 下一根收复：收盘 ≥ lo，阳线，收盘位置 ≥ 55%
    sig = B.on_bar(ctx(2, 98.5, 100.2, 98.2, 100.0, 98.5))
    assert sig is not None and sig.direction == "long" and sig.stop_price < 98.2 and "收复" in " ".join(sig.reasons)
    B.reset()
    B.on_bar(ctx(1, 99.5, 99.6, 98.3, 98.5, 99.5))
    B.on_bar(ctx(2, 98.5, 98.6, 96.0, 96.5, 98.5))          # 深度 2.5 ATR > 1.5 → 真突破，放弃
    assert not B.setups


def test_strategy_C_rejection_requires_quantified_wick_and_trend():
    C = make_strategies(cfg()["strategies"])["C_rejection"]
    # 触及支撑 Z，下影线长：o=100.6 h=100.8 l=99.2 c=100.7 → 振幅 1.6 ATR，下影线 1.4/1.6=87%，收盘位置 94%
    sig = C.on_bar(ctx(1, 100.6, 100.8, 99.2, 100.7, 100.8, trend="up"))
    assert sig is not None and sig.direction == "long" and sig.stop_price < 99.2
    C.reset()
    # 逆势（4h down）默认不允许
    assert C.on_bar(ctx(1, 100.6, 100.8, 99.2, 100.7, 100.8, trend="down")) is None and any("逆势" in r for r in C.wait_reasons)
    C.reset()
    # 影线不够长（下影线 0.3/1.6）→ 无信号
    assert C.on_bar(ctx(1, 99.5, 100.8, 99.2, 100.7, 100.8, trend="up")) is None
    C.reset()
    # 同一区域冷却期内不重复
    assert C.on_bar(ctx(1, 100.6, 100.8, 99.2, 100.7, 100.8)) is not None
    assert C.on_bar(ctx(2, 100.6, 100.8, 99.2, 100.7, 100.8)) is None
