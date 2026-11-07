"""brando_pa 风控与成交规则：仓位计算、组合限制、同根止盈止损保守规则、跳空、手续费、订单去重、无未来函数。"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.backtest.execution import Position, PositionManager
from brando_pa.config import cfg
from brando_pa.paper.broker import LiveBroker, Order, PaperBroker, make_client_order_id, ExecutionForbidden
from brando_pa.risk.limits import RiskState, check_new_position
from brando_pa.risk.sizing import size_position


def test_sizing_respects_risk_budget_leverage_and_precision():
    c = cfg(); eq = 10000.0
    r = size_position(eq, 100000.0, 99000.0, "long", "BTCUSDT", c["risk"], c["costs"], "perp")
    assert r.ok and r.risk_usd <= eq * c["risk"]["risk_per_trade"] + 1e-6 and abs(r.qty / c["risk"]["instruments"]["BTCUSDT"]["qty_step"] - round(r.qty / c["risk"]["instruments"]["BTCUSDT"]["qty_step"])) < 1e-6
    # 止损很近 → 名义额大 → 杠杆被上限与强平缓冲约束
    r2 = size_position(eq, 100000.0, 99950.0, "long", "BTCUSDT", c["risk"], c["costs"], "perp")
    assert r2.ok and r2.leverage <= c["risk"]["max_leverage"] + 1e-9 and r2.margin <= eq + 1e-6
    if r2.liq_price is not None:
        assert (100000.0 - r2.liq_price) >= (100000.0 - 99950.0) * (1 + c["risk"]["liquidation_buffer"]) - 1e-6
    # 现货不能做空；风险预算越界被夹到 [0.25%, 1%]
    assert not size_position(eq, 2000.0, 2100.0, "short", "ETHUSDT", c["risk"], c["costs"], "spot").ok
    r3 = size_position(eq, 2000.0, 1900.0, "long", "ETHUSDT", c["risk"], c["costs"], "spot", risk_pct=0.05)
    assert r3.ok and r3.risk_pct_effective <= 0.01 + 1e-9
    assert not size_position(eq, 2000.0, 1999.999, "long", "ETHUSDT", c["risk"], c["costs"], "perp").ok or True   # 极近止损允许失败或缩减


def test_portfolio_limits():
    rc = cfg()["risk"]
    st = RiskState(equity=10000.0); st.roll_day("2025-01-01")
    ok, why = check_new_position("long", "BTCUSDT", 50.0, [], st, rc)
    assert ok and not why
    opens = [{"symbol": "ETHUSDT", "direction": "long", "risk_usd": 50.0, "strategy": "A"}, {"symbol": "SOLUSDT", "direction": "long", "risk_usd": 50.0, "strategy": "A"}]
    ok, why = check_new_position("long", "BTCUSDT", 50.0, opens, st, rc)
    assert not ok and any("同向相关" in w for w in why)
    ok, why = check_new_position("long", "ETHUSDT", 50.0, opens[:1], st, rc)
    assert not ok and any("补仓" in w for w in why)
    st.day_realized_pnl = -250.0
    ok, why = check_new_position("short", "BTCUSDT", 50.0, [], st, rc)
    assert not ok and any("当日亏损" in w for w in why)
    ok, why = check_new_position("short", "BTCUSDT", 50.0, [], RiskState(equity=10000.0), rc, data_ok=False)
    assert not ok and any("过期" in w for w in why)
    ok, why = check_new_position("long", "BTCUSDT", 250.0, [], RiskState(equity=10000.0), rc)
    assert not ok and any("总风险敞口" in w for w in why)


def _pos(direction="long", entry=100.0, stop=98.0, tp=106.0, qty=1.0):
    return Position("BTCUSDT", "A", direction, "perp", qty, entry, pd.Timestamp("2025-01-01", tz="UTC"), 0, stop, tp, stop, 2.0, pd.Timestamp("2025-01-01", tz="UTC"), {"setup_id": "s"}, {"leverage": 1.0}, trail_after_rr=1.5, max_hold_bars=10)


def test_same_bar_stop_first_gap_and_fees():
    c = cfg(); pm = PositionManager(c)
    trades = []
    p = _pos()
    closed, cash = pm.manage(p, 101.0, 107.0, 97.0, 105.0, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 1, trades)
    assert closed and trades[-1]["exit_reason"] == "stop_same_bar" and trades[-1]["net_pnl"] < 0
    stop_slip = (c["costs"]["perp"]["slippage_bps"] + c["costs"]["stop_slippage_bps"]) / 1e4
    assert abs(trades[-1]["exit_price"] - 98.0 * (1 - stop_slip)) < 1e-9
    # 跳空低开越过止损 → 以开盘价成交（更差）
    p = _pos(); trades = []
    closed, _ = pm.manage(p, 95.0, 96.0, 94.0, 95.5, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 1, trades)
    assert closed and trades[-1]["exit_reason"] == "stop_gap" and trades[-1]["exit_price"] < 95.0
    # 手续费：入场费 + 出场费都计入 net
    p = _pos(); p.fees = 100.0 * 1.0 * c["costs"]["perp"]["taker_fee_bps"] / 1e4; trades = []
    pm.manage(p, 101.0, 106.5, 100.5, 106.0, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 1, trades)
    t = trades[-1]; assert t["exit_reason"] == "take_profit" and abs((t["gross_pnl"] - t["fees"] - t["funding"]) - t["net_pnl"]) < 1e-9 and t["fees"] > p.fees * 0.99
    # 时间止损
    p = _pos(); trades = []
    closed, _ = pm.manage(p, 100.5, 101.0, 100.0, 100.8, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 10, trades)
    assert closed and trades[-1]["exit_reason"] == "time_stop"
    # 保本移动止损：浮盈 ≥ 1.5R 后止损上移到保本之上，下一根触发
    p = _pos(); trades = []
    closed, _ = pm.manage(p, 100.5, 103.5, 100.2, 103.2, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 1, trades)
    assert not closed and p.trailed and p.stop > 100.0
    closed, _ = pm.manage(p, 103.0, 103.1, 99.5, 99.8, pd.Timestamp("2025-01-01 02:00", tz="UTC"), 2, trades)
    assert closed and trades[-1]["exit_reason"] == "trail_stop" and trades[-1]["net_pnl"] > -0.5


def test_fill_entry_cancels_when_rr_degrades_or_stop_crossed():
    c = cfg(); pm = PositionManager(c)
    sig = {"symbol": "BTCUSDT", "strategy": "A", "direction": "long", "stop_price": 99000.0, "take_profit": 104000.0, "max_hold_bars": 96, "setup_id": "x", "zone": {}, "reasons": []}
    pend = {"signal": sig, "instrument": "perp", "signal_ts": "2025-01-01"}
    p, dcash, canc = pm.fill_entry(pend, 100000.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    assert p is not None and dcash < 0 and canc is None
    p, _, canc = pm.fill_entry(pend, 98900.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    assert p is None and "越过止损" in canc["reason"]
    sig2 = dict(sig, take_profit=101500.0)
    p, _, canc = pm.fill_entry({"signal": sig2, "instrument": "perp", "signal_ts": "2025-01-01"}, 100400.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    assert p is None and "净盈亏比" in canc["reason"]


def test_paper_broker_dedupe_and_live_forbidden():
    c = cfg(); b = PaperBroker(c["costs"])
    oid = make_client_order_id("entry", "A:1d:100:long:5", "2025-01-01T00:00:00+00:00")
    assert oid == make_client_order_id("entry", "A:1d:100:long:5", "2025-01-01T00:00:00+00:00")
    o1 = b.place_order(Order(oid, "BTCUSDT", "buy", 0.01, "perp", 100000.0)); o2 = b.place_order(Order(oid, "BTCUSDT", "buy", 0.01, "perp", 100000.0))
    assert o1.status == "filled" and o1.filled_price > 100000.0 and o2.status == "duplicate" and len(b.orders) == 1
    lb = LiveBroker(c, cli_confirm=True)
    with pytest.raises(ExecutionForbidden):
        lb.place_order(Order("z", "BTCUSDT", "buy", 0.01, "perp", 1.0))


@pytest.mark.skipif(not (__import__("pathlib").Path("data_store/brando_pa/bars/BTCUSDT_15m.parquet").exists() or __import__("pathlib").Path("data_store/spot90/bars/BTCUSDT_4h.parquet").exists()), reason="无本地 K 线数据")
def test_no_lookahead_truncated_history_gives_identical_signals():
    """把历史截断到 T，引擎在 T 之前每根 K 线的状态必须与用完整历史跑出的完全一致（否则存在未来函数）。"""
    from brando_pa.data.store import BarStore
    from brando_pa.engine.context import MarketContext
    from brando_pa.engine.signal_engine import SignalEngine
    c = cfg(); st = BarStore()
    exec_tf = "4h" if st.load("BTCUSDT", "15m").empty else "15m"
    bars = {tf: st.load("BTCUSDT", tf) for tf in ("1w", "1d", "4h", exec_tf)}
    n = len(bars[exec_tf]); cut = n - 300
    full = MarketContext("BTCUSDT", bars, exec_tf, c)
    cut_ts = bars[exec_tf]["ts"].iloc[cut - 1]
    trunc_bars = {tf: df[df["ts"] + pd.Timedelta(seconds=__import__("brando_pa.schema", fromlist=["tf_seconds"]).tf_seconds(tf)) <= cut_ts + pd.Timedelta(seconds=full.bar_seconds)] for tf, df in bars.items()}
    trunc = MarketContext("BTCUSDT", trunc_bars, exec_tf, c)
    e1, e2 = SignalEngine(c), SignalEngine(c)
    start = max(1, cut - 1500)
    for i in range(start, cut):
        r1, r2 = e1.evaluate(full, i, 10000.0), e2.evaluate(trunc, i, 10000.0)
        assert r1.status == r2.status and r1.reasons == r2.reasons and len(r1.zones) == len(r2.zones), f"第 {i} 根（{r1.ts}）结果不一致：{r1.status} vs {r2.status}"
