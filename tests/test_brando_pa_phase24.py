"""第二十四阶段：仓位比例只缩放买入金额且默认等于第二十三阶段原样；V2 信号是 V1 的子集；大赢家依赖与熊市段计算。"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.pa21 import freeze21
from brando_pa.pa23 import pullback23 as P
from brando_pa.pa24 import phase24 as Q

T = lambda s: pd.Timestamp(s, tz="UTC")


@pytest.fixture(scope="module")
def btc():
    f = P.load("BTCUSDT"); return f, P.signals(f, "2019-01-01")


def test_weight_scales_entry_and_default_unchanged(btc):
    f, s = btc; m1, t1 = P.strategy(f, s, "BTCUSDT", "浅档 5–10%", "H90", "2019-01-01"); m25, t25, nav = P.strategy(f, s, "BTCUSDT", "浅档 5–10%", "H90", "2019-01-01", weight=0.25, return_nav=True)
    assert [t["entry_day"] for t in t1] == [t["entry_day"] for t in t25] and t25[0]["cost_usdt"] == pytest.approx(0.25 * t1[0]["cost_usdt"], rel=1e-3)               # 数量精度取整与平方根冲击使比例略有差异
    first = nav[pd.to_datetime(nav["ts"], utc=True) == T(t25[0]["entry_day"])].iloc[0]; assert 0.2 < first["exposure"] < 0.26


def test_v2_subset_of_v1(btc):
    f, s = btc; st = f["w_state"].to_numpy(); v2 = s["signal"] & (st[s["i"].to_numpy()] == "UPTREND")
    assert (v2 <= s["signal"]).all() and v2.sum() < s["signal"].sum()


def test_winner_dependence_and_bear_perf():
    tr = [{"exit_day": "x", "ret": 1.0, "entry_day": "a"}, {"exit_day": "x", "ret": 0.1, "entry_day": "b"}, {"exit_day": "x", "ret": -0.5, "entry_day": "c"}]
    w = Q.winner_dependence(tr, 100_000 * 2 * 1.1 * 0.5, 100_000, 1.0); assert w["final_share_without_top1"] == pytest.approx(0.5) and w["top_trades"][0]["entry_day"] == "a"
    nav = pd.DataFrame({"ts": pd.date_range(T("2022-01-01"), periods=40, freq="D"), "liquidation": np.r_[np.linspace(100, 50, 20), np.linspace(50, 60, 20)]})
    b = Q.bear_perf(nav, [(T("2022-01-01"), T("2022-02-09"))])[0]; assert b["return"] == pytest.approx(-0.4) and b["max_drawdown"] == pytest.approx(-0.5)
    assert freeze21.verify()["ok"]
