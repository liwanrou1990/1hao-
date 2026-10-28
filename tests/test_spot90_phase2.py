"""第二阶段测试：链上特征可用性滞后与 PIT 分组、同窗口对照收益标签、衍生品客户端（Mock）、实验特征集构成。"""
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from spot90.data.derivatives import BitgetDerivativesClient
from spot90.features import FEATURES
from spot90.features_onchain import GROUPS, _shift_days, build_onchain_features
from spot90.labels import build_labels


def test_onchain_lag_and_pit_groups():
    st_keys = pd.DataFrame({"ts": pd.date_range("2024-03-01", "2024-03-20", tz="UTC"), "symbol": "BTCUSDT"})
    f, groups = build_onchain_features(st_keys)
    assert set(groups) == set(GROUPS)
    assert groups["exchange_flows"]["pit_safe"] is False and groups["defi_tvl"]["pit_safe"] is False
    assert groups["chain_activity"]["pit_safe"] and groups["stablecoin"]["pit_safe"]
    # 可用性滞后：日 t 的特征值 = 用日 t-1 的 Coin Metrics 原值计算
    from spot90.data.onchain import load_table
    cm = load_table("cm_btc").set_index("time")["CapMVRVCur"]
    if cm.empty:
        pytest.skip("本地无 Coin Metrics 缓存")
    row = f[f["ts"] == pd.Timestamp("2024-03-15", tz="UTC")].iloc[0]
    assert row["oc_mvrv"] == pytest.approx(cm.loc["2024-03-14"]) and row["oc_mvrv"] != pytest.approx(cm.loc["2024-03-15"])


def test_shift_days_moves_forward_only():
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=3, tz="UTC"), "x": [1, 2, 3]})
    out = _shift_days(df, 2)
    assert out["time"].iloc[0] == pd.Timestamp("2024-01-03", tz="UTC") and out["x"].iloc[0] == 1


def test_same_window_label_and_dd_flags():
    n = 400
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    df = pd.DataFrame({"ts": pd.date_range("2023-01-01", periods=n, tz="UTC"), "symbol": "BTCUSDT", "open": close * (1 + rng.normal(0, 0.002, n)),
                       "close": close})
    df["high"] = np.maximum(df["open"], df["close"]) * 1.01; df["low"] = np.minimum(df["open"], df["close"]) * 0.99
    wide, long = build_labels(df)
    assert {"mae10_A", "mae20_A", "mdd10_A", "mdd20_A"} <= set(wide.columns)
    assert (wide["mae10_A"] >= wide["mae15_A"]).all() and (wide["mae15_A"] >= wide["mae20_A"]).all()
    f = long[long["filled"] == 1]
    assert np.isfinite(f["ret_A_same_window"]).all()
    # 未成交时同窗口对照 = ret_A
    u = long[long["filled"] == 0]
    assert np.allclose(u["ret_A_same_window"], u["ret_A_counterfactual"])


def test_derivatives_client_mock(tmp_path, monkeypatch):
    rows = [{"fundingRate": "0.0001", "fundingTime": str(1700000000000 + i * 8 * 3600 * 1000), "symbol": "BTCUSDT"} for i in range(150)]

    def handler(req: httpx.Request):
        if req.url.path == "/api/v2/public/time":
            return httpx.Response(200, json={"code": "00000", "data": "1"})
        if req.url.path == "/api/v2/mix/market/history-fund-rate":
            page = int(req.url.params["pageNo"]); chunk = rows[(page - 1) * 100: page * 100]
            return httpx.Response(200, json={"code": "00000", "data": chunk})
        if req.url.path == "/api/v2/mix/market/open-interest":
            return httpx.Response(200, json={"code": "00000", "data": {"openInterestList": [{"symbol": "BTCUSDT", "size": "12345.6"}]}})
        return httpx.Response(404)

    monkeypatch.setenv("SPOT90_DATA_DIR", str(tmp_path))
    c = BitgetDerivativesClient(transport=httpx.MockTransport(handler))
    st = c.collect(["BTCUSDT"])
    assert st["symbols"]["BTCUSDT"]["funding_rows"] == 150 and st["symbols"]["BTCUSDT"]["open_interest_snapshots"] == 1


def test_experiment_sets_are_nested():
    from spot90.backtest.experiments import experiment_sets
    groups = {k: {"pit_safe": v["pit_safe"], "cols": [f"{k}_{i}" for i in range(3)]} for k, v in GROUPS.items()}
    ex = experiment_sets(groups)
    assert set(ex["A_tech"]["features"]) == set(FEATURES)
    assert set(ex["A_tech"]["features"]) < set(ex["B_pit"]["features"]) < set(ex["B_full"]["features"])
    assert not any(c.startswith(("exchange_flows", "defi_tvl")) for c in ex["B_pit"]["features"])
    assert not any(c.startswith(("exchange_flows", "defi_tvl")) for c in ex["D_pit"]["features"])
