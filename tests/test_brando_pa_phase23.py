"""第二十三阶段：信号无未来数据（截断重算一致）；同一 60 天高点同一档只计一次；档位边界；事件收益 / 成本 / 最大不利波动；
周线趋势失效退出时点；完整策略单一持仓且次日开盘成交；不影响第二十一阶段冻结。（合成数据只用于检验代码。）"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.pa21 import freeze21
from brando_pa.pa23 import pullback23 as P

T = lambda s: pd.Timestamp(s, tz="UTC")


@pytest.fixture(scope="module")
def btc():
    return P.load("BTCUSDT")


def test_signals_no_future_data(btc):
    full = P.signals(btc, "2019-01-01"); cols = ["ts", "depth", "confirm", "weekly_ok", "bucket", "signal"]
    for cut in ("2020-06-30", "2021-11-15", "2024-03-01"):
        part = P.signals(btc[btc["ts"] <= T(cut)].reset_index(drop=True), "2019-01-01")
        a = full[full["ts"] <= T(cut)][cols].reset_index(drop=True); b = part[cols].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_one_signal_per_high_and_bucket_bounds(btc):
    s = P.signals(btc, "2016-01-01"); g = s[s["signal"]]
    assert not g.duplicated(["bucket", "H_day"]).any()
    for b, (lo, hi) in P.BUCKETS.items():
        x = g[g["bucket"] == b]["depth"]; assert ((x >= lo) & (x < hi)).all()
    assert (g["weekly_ok"] & g["confirm"]).all() and not (g["depth"] > 0.25).any()


def _synthetic():
    n = 400; ts = pd.date_range(T("2020-01-01"), periods=n, freq="D"); c = np.linspace(100, 200, n)
    f = pd.DataFrame({"ts": ts, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "w_tf": False}); f.loc[300, "w_tf"] = True; return f


def test_event_returns_costs_mae_and_tf_exit():
    f = _synthetic(); sig = pd.DataFrame({"i": [100], "ts": [f["ts"].iloc[100]], "H": [150.0], "depth": [0.1], "bucket": ["浅档 5–10%"], "signal": [True]})
    e = P.events(f, sig, side_bps=15).iloc[0]; op = f["open"].to_numpy(); c = 0.0015
    assert e["entry"] == op[101] and e["ret_H30"] == pytest.approx(op[131] * (1 - c) / (op[101] * (1 + c)) - 1)
    assert e["mae_H30"] == pytest.approx(f["low"].iloc[101:131].min() / op[101] - 1) and e["days_TF"] == 301 - 101          # 第 300 天失效 → 第 301 天开盘卖出
    assert e["discount"] == pytest.approx(1 - op[101] / 150.0)


def test_strategy_single_position_next_open(btc):
    s = P.signals(btc, "2016-01-01"); m, tr = P.strategy(btc, s, "BTCUSDT", "浅档 5–10%", "H90", "2016-01-01")
    for t in tr:
        sd = T(t["signal_day"]); ed = T(t["entry_day"]); assert ed == sd + pd.Timedelta(days=1)
    closed = [t for t in tr if t.get("exit_day")]
    for a, b in zip(closed[:-1], tr[1:]):
        assert T(b["entry_day"]) >= T(a["exit_day"])                                                           # 不重叠持仓
    assert m["initial_usdt"] == 100_000.0 and m["n_trades"] == len(tr)


def test_zigzag_descriptive_only_and_freeze_untouched():
    f = _synthetic(); f.loc[200:, "close"] = np.linspace(200, 80, 200); lab, piv = P.zigzag_phases(f)
    assert set(lab.dropna()) <= {"牛市", "熊市"} and lab.iloc[-1] == "熊市"
    assert freeze21.verify()["ok"]
