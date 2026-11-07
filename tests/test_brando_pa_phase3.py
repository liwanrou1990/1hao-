"""第三阶段测试：微结构入场状态机（因果顺序）、微结构止损模式、订单事件序列、数据时序/异常检测、止损诊断分解。"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.analysis.levels import Zone
from brando_pa.analysis.swings import Swing
from brando_pa.backtest.execution import PositionManager
from brando_pa.config import cfg, deep_update
from brando_pa.diagnostics.stop_distance import decompose, summarize
from brando_pa.strategies import make_strategies
from brando_pa.strategies.base import BarContext

Z = Zone("1d", 99.0, 101.0, 100.0, [100.0], ["L"], 0, touches=3, strength=5.0, tfs=["1d"], zone_id="1d:100.00")
ATR = 1.0


def _ctx(i, o, h, l, c, prev_close, swings, H, L, trend="up", events=()):
    return BarContext("T", i, pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=i), o, h, l, c, prev_close, 100.0, 1.5, ATR, ATR / c, [Z], {"1d": [Z]}, trend,
                      {"trend": "up", "highvol": False}, list(events), {"bar_seconds": 3600, "exec_swings": [sw for sw in swings if sw.confirmed_idx <= i], "exec_h": H, "exec_l": L})


def _strats():
    c = cfg()["strategies"]; c["A2_structure_entry"]["enabled"] = True; c["A3_breakout_second_confirm"]["enabled"] = True
    return make_strategies(c)


def test_mode_A_requires_confirmed_micro_low_then_bos_and_sets_micro_stop():
    A2 = _strats()["A2_structure_entry"]
    # 价格序列：第 1 根触及支撑区（low 100.8），第 2 根做出低点 99.6（摆动低，k=3 → 第 5 根确认），第 3~5 根反弹高 100.9/101.2/101.0，第 6 根收盘 101.5 突破 micro_high 101.2
    H = np.array([103, 102.0, 100.5, 100.9, 101.2, 101.0, 101.6, 101.8]); L = np.array([102, 100.8, 99.6, 100.2, 100.6, 100.4, 100.9, 101.2])
    swings = [Swing(2, "L", 99.6, 5, pd.Timestamp("2024-01-01", tz="UTC"))]
    assert A2.on_bar(_ctx(1, 102.5, 102.0, 100.8, 101.3, 102.5, swings, H, L)) is None and len(A2.setups) == 1
    assert A2.on_bar(_ctx(2, 101.3, 100.5, 99.6, 100.1, 101.3, swings, H, L)) is None
    assert A2.on_bar(_ctx(3, 100.1, 100.9, 100.2, 100.7, 100.1, swings, H, L)) is None and A2.setups[0].get("micro_idx") is None     # 低点尚未确认
    assert A2.on_bar(_ctx(4, 100.7, 101.2, 100.6, 101.1, 100.7, swings, H, L)) is None
    assert A2.on_bar(_ctx(5, 101.1, 101.0, 100.4, 100.6, 101.1, swings, H, L)) is None and A2.setups[0]["micro_idx"] == 2 and A2.setups[0]["micro_bos"] == 101.2   # 第 5 根确认，但确认根不能同时触发
    sig = A2.on_bar(_ctx(6, 100.6, 101.6, 100.9, 101.5, 100.6, swings, H, L))
    assert sig is not None and sig.direction == "long" and sig.micro_stop_price == pytest.approx(99.6 - 0.2) and sig.structure_price == 99.0
    assert sig.stop_price == sig.micro_stop_price and sig.diag["micro_bos"] == 101.2 and sig.diag["bars_touch_to_entry"] == 5
    assert (sig.ref_price - sig.stop_price) / ATR < 2.5


def test_mode_A_blocks_counter_trend_and_mode_B_requires_breakout_then_pullback():
    A2 = _strats()["A2_structure_entry"]
    H = np.array([103, 102.0]); L = np.array([102, 100.8])
    assert A2.on_bar(_ctx(1, 102.5, 102.0, 100.8, 101.3, 102.5, [], H, L, trend="down")) is None and not A2.setups and A2.counters.get("blocked_counter_trend") == 1
    A3 = _strats()["A3_breakout_second_confirm"]
    ZR = Zone("1d", 99.0, 101.0, 100.0, [100.0], ["H"], 0, touches=3, strength=5.0, tfs=["1d"], zone_id="1d:100.00")
    ev = [{"zone": ZR, "event": "break_up", "rel_vol": 1.5, "ts": pd.Timestamp("2024-01-01", tz="UTC"), "close": 101.8}]
    H = np.array([101.9, 103.0, 103.5, 104.0]); L = np.array([101.2, 101.6, 102.2, 102.8])
    assert A3.on_bar(_ctx(1, 101.2, 101.9, 101.2, 101.8, 100.5, [], H, L, events=ev)) is None and len(A3.setups) == 1 and A3.setups[0]["touch_idx"] is None
    # 价格直接上涨不回踩 → 超过 max_wait_retest_bars 后放弃（允许错过）
    A3.p["max_wait_retest_bars"] = 2
    A3.on_bar(_ctx(2, 101.8, 103.0, 101.6, 102.9, 101.8, [], H, L)); A3.on_bar(_ctx(3, 102.9, 103.5, 102.2, 103.3, 102.9, [], H, L)); A3.on_bar(_ctx(4, 103.3, 104.0, 102.8, 103.9, 103.3, [], H, L))
    assert not A3.setups and A3.counters.get("missed_no_pullback") == 1


def test_micro_stop_mode_never_widens():
    c = deep_update(cfg(), {"exits": {"stop_mode": "micro"}}); pm = PositionManager(c)
    sig = {"symbol": "BTCUSDT", "strategy": "A2_structure_entry", "direction": "long", "stop_price": 98.0, "take_profit": 106.0, "structure_price": 99.0, "micro_stop_price": 98.9, "atr": 1.0, "max_hold_bars": 50, "setup_id": "s", "zone": {}, "reasons": []}
    p, _, _ = pm.fill_entry({"signal": sig, "instrument": "perp", "signal_ts": "2025-01-01"}, 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 3600)
    assert p.stop == 98.9
    sig2 = dict(sig, micro_stop_price=97.0)       # 微结构止损更宽 → 不允许放宽，保留策略止损
    p2, _, _ = pm.fill_entry({"signal": sig2, "instrument": "perp", "signal_ts": "2025-01-01"}, 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 3600)
    assert p2.stop == 98.0


def test_spread_param_widens_market_fill_only_when_set():
    from brando_pa.backtest.execution import slip_rate
    c = cfg()
    assert slip_rate(c["costs"], "perp") == pytest.approx(5 / 1e4)
    c2 = deep_update(cfg(), {"costs": {"perp": {"spread_bps": 4}}})
    assert slip_rate(c2["costs"], "perp") == pytest.approx(7 / 1e4)


def test_stop_distance_decomposition_matches_definition():
    sl = pd.DataFrame({"accepted": [True, False], "first_fail": ["", "stop_distance"], "stop_distance_atr": [3.0, 5.0], "stop_distance_pct": [0.02, 0.03], "zone_tfs": ["1d", "1d/1w"], "symbol": ["BTCUSDT", "BTCUSDT"], "direction": ["long", "long"],
                       "diag_zone_height_atr": [2.0, 4.0], "diag_retest_depth_atr": [0.3, 0.2], "diag_confirm_to_zone_edge_atr": [0.5, 0.5], "diag_structure_is_zone_far_side": [True, True], "diag_bars_armed_to_retest": [3, 5], "diag_bars_retest_to_confirm": [1, 2]})
    d = decompose(sl, 0.5)
    assert d["recon"].tolist() == pytest.approx([3.0, 5.0])      # 区域高度 + 确认偏移 + 缓冲 = 实际止损距离
    s = summarize(sl, pd.DataFrame(), 0.5, "1h")
    assert s["decomposition_share"]["part_zone_height"] > 0.5 and any("区域的另一侧" in v for v in s["verdict"])


def test_order_event_sequence_is_causal():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.backtest.simulator import Backtester
    from pathlib import Path
    if not Path("data_store/brando_pa/bars/ETHUSDT_1h.parquet").exists():
        pytest.skip("无数据")
    c = cfg(); ctxs = load_contexts(["ETHUSDT"], "1h", c)
    res = Backtester(c, "1h", ["A_breakout_retest"], True, {"execution_modes": {"entry_mode": "limit"}}, start="2024-01-01", end="2024-12-31").run(ctxs)
    ev = pd.DataFrame(res.order_events)
    assert set(ev["event"]).issuperset({"signal_generated", "order_submitted"})
    for sid, g in ev[ev["setup_id"].notna()].groupby("setup_id"):
        seq = g["event"].tolist(); ts = pd.to_datetime(g["ts"])
        assert seq[0] == "signal_generated" and seq[1] == "order_submitted" and ts.is_monotonic_increasing
        assert sum(e == "order_filled" for e in seq) <= 1 and not ("order_filled" in seq and "order_cancelled" in seq)
    # 限价：触达但未穿透不得成交
    touched = ev[ev["event"] == "order_touched_not_filled"]
    filled_ids = set(ev.loc[ev["event"] == "order_filled", "setup_id"])
    assert all((sid not in filled_ids) or (ev[(ev.setup_id == sid) & (ev.event == "order_filled")]["ts"].iloc[0] > r["ts"]) for sid, r in zip(touched["setup_id"], touched.to_dict("records")))


def test_data_timeline_and_anomaly_checks():
    from brando_pa.audit.data_quality import audit_bars
    ts = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    df = pd.DataFrame({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0, "source": "t"})
    ok = audit_bars(df, "1h", ts[-1] + pd.Timedelta(hours=2))
    assert ok["status"] == "ok" and ok["n_gaps"] == 0 and ok["duplicates"] == 0
    bad = pd.concat([df, df.iloc[[50]]]).drop(df.index[20:23]); bad.loc[bad.index[70], "ts"] += pd.Timedelta(minutes=7); bad.loc[bad.index[80], "close"] = 150.0; bad.loc[bad.index[80], "high"] = 150.0
    rep = audit_bars(bad.sort_values("ts").reset_index(drop=True), "1h", ts[-1] + pd.Timedelta(hours=2))
    assert rep["duplicates"] == 1 and rep["misaligned"] == 1 and rep["n_gaps"] >= 1 and rep["n_range_gt_25pct"] >= 1 and rep["status"] == "issues"
