"""brando_pa：摆动点确认滞后、区域聚类/宽度上限、K 线事件分类、跨周期对齐无未来数据。"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.analysis.levels import Zone, build_zones, classify_bar, merge_zones, nearest_zone
from brando_pa.analysis.mtf import align_last_closed, assert_no_lookahead
from brando_pa.analysis.structure import label_swings, structure_state
from brando_pa.analysis.swings import Swing, confirmed_up_to, find_swings
from brando_pa.config import cfg
from brando_pa.schema import normalize_rows, resample_bars


def _bars(prices, tf="1d", start="2024-01-01", vol=100.0):
    ts = pd.date_range(start, periods=len(prices), freq={"1d": "1D", "4h": "4h", "15m": "15min", "1h": "1h"}[tf], tz="UTC")
    p = np.asarray(prices, float)
    return pd.DataFrame({"symbol": "T", "tf": tf, "ts": ts, "open": p, "high": p + 1.0, "low": p - 1.0, "close": p, "volume": vol, "quote_volume": vol * p, "source": "test", "ingested_at": ts[0]})


def test_swing_confirmation_lag():
    prices = [10, 11, 12, 15, 12, 11, 10, 9, 8, 9, 10, 11]
    df = _bars(prices)
    sw = find_swings(df, k=2)
    highs = [s for s in sw if s.kind == "H"]
    assert highs and highs[0].idx == 3 and highs[0].confirmed_idx == 5
    assert confirmed_up_to(sw, 4) == [] or all(s.confirmed_idx <= 4 for s in confirmed_up_to(sw, 4))
    assert highs[0] in confirmed_up_to(sw, 5)
    assert highs[0] not in confirmed_up_to(sw, 4)


def test_zone_width_capped_and_touch_counting():
    c = cfg()["levels"]
    # 价格在 100 附近反复触及 100 的支撑：构造 3 次回落到 100 再弹起
    base = []
    for _ in range(4):
        base += [104, 103, 102, 101, 100, 101, 102, 103, 104, 105]
    df = _bars(base, "1d")
    atr = pd.Series(np.full(len(df), 2.0))
    sw = find_swings(df, 3)
    zones = build_zones(df, sw, len(df) - 1, atr.to_numpy(), "1d", c)
    assert zones, "应识别出 100 支撑区"
    z = min(zones, key=lambda z: abs(z.center - 100))
    assert abs(z.center - 99.0) < 0.6          # 合成 K 线 low = price-1，摆动低点在 99
    assert z.hi - z.lo <= 2 * c["zone_max_width_pct"] * z.center + 1e-9
    assert z.touches >= 3


def test_classify_bar_events():
    z = Zone("1d", 99.0, 101.0, 100.0, [100.0], ["L"], 0)
    buf = 0.2
    assert classify_bar(102, 103, 100.5, 102.5, 102.0, z, buf) == "touch"            # 影线进入区域，收盘仍在上方
    assert classify_bar(102, 102.5, 97.0, 97.5, 102.0, z, buf) == "break_down"      # 收盘跌破下沿超过 buf
    assert classify_bar(102, 102.5, 97.0, 101.5, 102.0, z, buf) == "wick_down"      # 影线跌破但收盘回到上方 → 噪声/假突破
    assert classify_bar(98, 103.0, 97.5, 98.5, 98.0, z, buf) == "wick_up"
    assert classify_bar(98, 103.0, 97.5, 102.0, 98.0, z, buf) == "break_up"
    assert classify_bar(105, 106, 104, 105, 105, z, buf) == "none"


def test_merge_zones_only_when_centers_overlap_and_nearest():
    a = Zone("1d", 99, 101, 100, [100], ["L"], 0, strength=2); b = Zone("4h", 99.8, 101.8, 100.8, [100.8], ["L"], 0, strength=1); far = Zone("4h", 101.2, 103.2, 102.2, [102.2], ["L"], 0, strength=1); c_ = Zone("1w", 103, 105, 104, [104], ["H"], 0, strength=3)
    m = merge_zones([[a], [b], [c_]])
    assert len(m) == 2 and m[0].lo == 99 and m[0].hi == 101.8 and sorted(m[0].tfs) == ["1d", "4h"]
    assert len(merge_zones([[a], [far]])) == 2            # 仅边界重叠、中心不在对方区域内 → 不合并（防链式黏连）
    assert nearest_zone(m, 102.6, "up").center == 104 and nearest_zone(m, 102.6, "down").lo == 99


def test_structure_labels_and_trend():
    sw = [Swing(0, "L", 90, 3, pd.Timestamp("2024-01-01", tz="UTC")), Swing(5, "H", 100, 8, pd.Timestamp("2024-01-06", tz="UTC")), Swing(10, "L", 95, 13, pd.Timestamp("2024-01-11", tz="UTC")),
          Swing(15, "H", 108, 18, pd.Timestamp("2024-01-16", tz="UTC")), Swing(20, "L", 99, 23, pd.Timestamp("2024-01-21", tz="UTC")), Swing(25, "H", 115, 28, pd.Timestamp("2024-01-26", tz="UTC"))]
    labels = [l for _, l in label_swings(sw, atr_value=2.0, range_atr=0.5)]
    assert labels == ["L", "H", "HL", "HH", "HL", "HH"]
    st = structure_state(sw, last_closed_idx=28, atr_value=2.0, range_atr=0.5)
    assert st.trend == "up"
    st2 = structure_state(sw, last_closed_idx=20, atr_value=2.0, range_atr=0.5)   # 第 25 根摆动高尚未确认（confirmed_idx=28）
    assert all(s.confirmed_idx <= 20 for s in confirmed_up_to(sw, 20)) and st2.last_high == 108


def test_mtf_alignment_never_uses_unclosed_htf_bar():
    ex = _bars(np.linspace(100, 110, 96 * 3), "15m", start="2024-01-01")
    d1 = _bars([100, 105, 110, 112], "1d", start="2024-01-01")
    idx = align_last_closed(ex["ts"], "15m", d1["ts"], "1d")
    # 1 月 1 日 00:00 ~ 23:30 开盘的 15m K 线：日线 1 月 1 日尚未收盘 → 索引 -1；23:45 开盘的 15m 在 00:00 收盘，与日线同时收盘 → 可用索引 0
    assert idx[0] == -1 and idx[94] == -1 and idx[95] == 0 and idx[190] == 0 and idx[191] == 1
    assert_no_lookahead(ex["ts"], "15m", d1["ts"], "1d", idx)
    with pytest.raises(AssertionError):
        assert_no_lookahead(ex["ts"], "15m", d1["ts"], "1d", idx + 1)


def test_normalize_drops_unclosed_and_resample_week_starts_monday():
    now = pd.Timestamp("2024-01-10 12:00", tz="UTC")
    rows = [[int(pd.Timestamp("2024-01-09", tz="UTC").timestamp() * 1000), 1, 2, 0.5, 1.5, 10, 15], [int(pd.Timestamp("2024-01-10", tz="UTC").timestamp() * 1000), 1, 2, 0.5, 1.5, 10, 15]]
    df = normalize_rows(rows, "BTCUSDT", "1d", "test", now_utc=now)
    assert len(df) == 1 and df["ts"].iloc[0] == pd.Timestamp("2024-01-09", tz="UTC")
    d1 = _bars(np.arange(100, 128), "1d", start="2024-01-01")      # 2024-01-01 是周一
    w = resample_bars(d1, "1d", "1w")
    assert w["ts"].iloc[0] == pd.Timestamp("2024-01-01", tz="UTC") and w["ts"].dt.dayofweek.eq(0).all() and len(w) == 4
