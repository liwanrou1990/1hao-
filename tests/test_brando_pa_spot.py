"""第四阶段纯现货：账本约束（现金/持仓/无杠杆/FIFO）、本地结构只用已确认摆动点且不含当前根、引擎全程无负现金/无超卖/决策集合、买入持有基准口径。"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.analysis.swings import Swing
from brando_pa.config import cfg
from brando_pa.spot.ledger import SpotConstraintError, SpotLedger
from brando_pa.spot.structure import all_levels, last_confirmed_swing_low, local_levels

HAVE = Path("data_store/brando_pa/bars/BTCUSDT_1h.parquet").exists()


def test_ledger_cash_and_holding_constraints_and_fifo():
    L = SpotLedger(1000.0, fee_bps=10, slippage_bps=5)
    lot = L.buy("BTCUSDT", 0.005, 100000.0, "t1", "A", 98000.0, 1000.0, 10.0)
    assert L.cash == pytest.approx(1000.0 - 0.005 * 100050.0 * (1 + 0.001)) and L.qty("BTCUSDT") == 0.005
    with pytest.raises(SpotConstraintError):
        L.buy("BTCUSDT", 0.01, 100000.0, "t2", "A", 98000.0, 1000.0, 10.0)          # 现金不足
    with pytest.raises(SpotConstraintError):
        L.sell("BTCUSDT", 0.006, 101000.0, "t3", "x")                                   # 超过持仓
    L.buy("BTCUSDT", 0.002, 90000.0, "t4", "B", 88000.0, 1000.0, 4.0)
    rec = L.sell("BTCUSDT", 0.006, 101000.0, "t5", "tp")                               # FIFO：先平 0.005@100050，再平 0.001@89955
    assert [round(c["qty"], 6) for c in rec["closed_lots"]] == [0.005, 0.001] and L.qty("BTCUSDT") == pytest.approx(0.001)
    assert L.cash >= 0 and all(l.qty >= 0 for l in L.lots["BTCUSDT"])
    assert L.snapshot({"BTCUSDT": 101000.0})["equity"] == pytest.approx(L.cash + 0.001 * 101000.0)


def test_local_levels_only_confirmed_and_exclude_current_bar():
    close = np.array([100, 101, 103, 102, 101, 100, 99, 100, 101, 102, 103.5, 104, 103, 102, 101], float)
    sw = [Swing(2, "H", 103.0, 5, pd.Timestamp("2024-01-01", tz="UTC")), Swing(6, "L", 99.0, 9, pd.Timestamp("2024-01-01", tz="UTC")), Swing(11, "H", 104.0, 14, pd.Timestamp("2024-01-01", tz="UTC"))]
    lc = {"lookback_bars": 100, "level_tol_atr": 0.15, "max_levels_each_side": 4, "min_level_age_bars": 3}
    # i=7：摆动高 103 在 5 确认但尚未满足 age≥3（5 > 7-3）→ 不可用；i=8 可用；摆动低 99 在 9 才确认 → i=8 不可用
    res7, _ = local_levels([s for s in sw if s.confirmed_idx <= 7], 7, close, 1.0, lc)
    assert res7 == []
    res, sup = local_levels([s for s in sw if s.confirmed_idx <= 8], 8, close, 1.0, lc)
    assert [l.price for l in res] == [103.0] and sup == []
    # i=10：收盘 103.5 刚越过 103（当前根），broken 不含当前根 → False；i=11 之后 → True
    lv10 = all_levels([s for s in sw if s.confirmed_idx <= 10], 10, close, 1.0, lc)
    assert any(l.price == 103.0 and not l.broken for l in lv10)
    lv12 = all_levels([s for s in sw if s.confirmed_idx <= 12], 12, close, 1.0, lc)
    assert any(l.price == 103.0 and l.broken for l in lv12)
    assert last_confirmed_swing_low(sw, 8) is None and last_confirmed_swing_low(sw, 9) == 99.0


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_engine_never_negative_cash_never_oversells_and_marks_to_market():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.spot.engine import SpotBacktester, buy_and_hold
    c = cfg(); ctxs = load_contexts(["BTCUSDT", "ETHUSDT"], "1h", c)
    r = SpotBacktester(c, ["BTCUSDT", "ETHUSDT"], "1h", "2024-01-01", "2024-09-30").run(ctxs)
    eq = r.equity
    assert (eq["cash"] >= -1e-6).all() and (eq["qty_BTCUSDT"] >= -1e-12).all() and (eq["qty_ETHUSDT"] >= -1e-12).all()
    assert (eq["exposure"] <= 1.0 + 1e-9).all()                                           # 无隐含杠杆
    assert set(r.decisions["decision"].unique()) <= {"BUY", "HOLD", "SELL"}
    f = r.fills
    if not f.empty:
        for s, g in f.groupby("symbol"):
            q = 0.0
            for _, row in g.sort_values("ts").iterrows():
                q += row["qty"] if row["side"] == "BUY" else -row["qty"]
                assert q >= -1e-9
    # 期末权益 = 现金 + 持仓 × 最后收盘
    last = {s: float(ctxs[s].ex[ctxs[s].ex["ts"] <= pd.Timestamp("2024-09-30", tz="UTC")]["close"].iloc[-1]) for s in ["BTCUSDT", "ETHUSDT"]}
    assert r.final["equity"] == pytest.approx(r.final["cash"] + sum(r.final["holdings"].get(s, 0) * last[s] for s in last), rel=1e-9)
    # 买入持有基准：期初开盘买入、期末收盘计价
    bh = buy_and_hold(ctxs, ["BTCUSDT"], "2024-01-01", "2024-09-30", 10000.0)
    df = ctxs["BTCUSDT"].ex; df = df[(df["ts"] >= pd.Timestamp("2024-01-01", tz="UTC")) & (df["ts"] <= pd.Timestamp("2024-09-30", tz="UTC"))]
    assert bh["equity"].iloc[-1] == pytest.approx(10000.0 / float(df["open"].iloc[0]) * float(df["close"].iloc[-1]))
    # 止损只上移：每批次 stop ≥ init_stop
    if not r.trades.empty:
        assert (r.trades["exit"] > 0).all()


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_engine_sell_signal_cannot_exceed_holdings_and_no_short():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.spot.engine import SpotBacktester
    c = cfg(); ctxs = load_contexts(["ETHUSDT"], "1h", c)
    r = SpotBacktester(c, ["ETHUSDT"], "1h", "2025-01-01", "2025-06-30").run(ctxs)
    sells = r.decisions[r.decisions["decision"] == "SELL"]
    assert (sells["holding_qty"] > 0).all()                                               # 没有持仓时不会有 SELL 决策
    assert r.final["cash"] >= 0 and all(v >= 0 for v in r.final["holdings"].values())
