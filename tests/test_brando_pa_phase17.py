"""第十七阶段：成本审计的参数化模型在默认参数下与第十六阶段一致且不使用价格；空仓期分类规则；
错过机会分析使用的是未修改的 B2（与第十阶段逐位一致）；前瞻评估：365 天前不评级、长期踏空警报、买入持有基准公式、拒绝写入账户目录。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.holding.metrics import summarize
from brando_pa.pa15 import data15
from brando_pa.pa16 import data16
from brando_pa.pa17 import costaudit17, forward_eval17 as FE, missed17

ROOT = Path(__file__).resolve().parents[1]
P10 = ROOT / "docs" / "brando_pa_validation" / "phase10_holding" / "phase10_results.json"
raw_ok = (data15.RAW / "coinmetrics_btc_1d.csv").exists() and (data16.RAW16 / "coinbase_btc_usd_1d.csv").exists()


@pytest.mark.skipif(not raw_ok, reason="raw snapshots missing")
def test_parameterized_fleet_matches_phase16_default_and_ignores_price():
    p = data15.build_panel("btc"); hr = p["bci_hashrate_ths"].combine_first(p["HashRate"]).dropna(); hr = hr[hr.index >= pd.Timestamp("2012-06-01", tz="UTC")]
    a = costaudit17.fleet(hr, "newest", 90, 5, 10.0); b = data16.vintage_fleet(hr)["fleet_jth"]
    pd.testing.assert_series_equal(a.rename(None), b.rename(None), check_names=False, check_freq=False)
    q = p.copy(); q["PriceUSD"] *= 3.0; q["bci_miners_revenue_usd"] = 0.0
    hr2, h30, btc_day = costaudit17._base(q); _, h30b, btc_dayb = costaudit17._base(p)
    pd.testing.assert_series_equal(btc_day, btc_dayb)                                                   # 分母不依赖价格


def _fills(rows):
    return pd.DataFrame([{"ts": pd.Timestamp(t, tz="UTC"), "symbol": "BTCUSDT", "side": s, "qty": 1.0, "fill_price": p} for t, s, p in rows])


def test_flat_episode_classification_rules():
    px = pd.Series([100, 90, 70, 80, 95, 110, 120], index=pd.date_range("2024-01-01", periods=7, freq="D", tz="UTC"), dtype=float)
    f = _fills([("2024-01-01", "SELL", 100.0), ("2024-01-05", "BUY", 95.0)])
    assert missed17._episode("BTCUSDT", px.index[0], px.index[4], f, px)["class"] == "合理避险"
    f = _fills([("2024-01-01", "SELL", 100.0), ("2024-01-06", "BUY", 110.0)])
    assert missed17._episode("BTCUSDT", px.index[0], px.index[5], f, px)["class"] == "保护有效但回补更贵"          # 期间最大跌幅 −30%
    px2 = pd.Series(np.linspace(100, 115, 60), index=pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC"))
    f = _fills([("2024-01-01", "SELL", 100.0), ("2024-01-21", "BUY", float(px2.iloc[20]))])
    assert missed17._episode("BTCUSDT", px2.index[0], px2.index[20], f, px2)["class"] == "短期来回"
    f = _fills([("2024-01-01", "SELL", 100.0), ("2024-02-29", "BUY", float(px2.iloc[59]))])
    e = missed17._episode("BTCUSDT", px2.index[0], px2.index[59], f, px2)
    assert e["class"] == "不必要的长期空仓" and e["missed_usdt_if_positive"] > 0
    assert missed17._episode("BTCUSDT", px2.index[0], None, _fills([("2024-01-01", "SELL", 100.0)]), px2)["class"] == "进行中"


@pytest.mark.skipif(not P10.exists(), reason="phase10 results missing")
def test_missed_analysis_uses_unmodified_b2():
    res, bars = missed17.run("B2", "2018-01-01", json.loads(P10.read_text())["latest_complete_day"])
    ref = next(r for r in json.loads(P10.read_text())["rows"] if r["name"] == "B2_modelB_state_machine_daily_repro" and r["window"] == "full" and r["cost_bps"] == 15)
    assert summarize(res)["liquidation_net_profit_usdt"] == pytest.approx(ref["liquidation_net_profit_usdt"], abs=1e-6)


def _df(days, liq, btc, held):
    d = pd.date_range("2025-01-01", periods=days, freq="D", tz="UTC")
    return pd.DataFrame({"date": d, "liquidation": liq, "btc": btc, "holdings": [{"BTCUSDT": 1.0} if h else {} for h in held]})


def test_forward_eval_no_rating_before_365_days_and_long_flat_alert():
    n = 200; btc = np.linspace(100, 140, n); m = FE.metrics(_df(n, np.full(n, 10000.0), btc, [False] * n))
    assert m["scorecard"]["status"].startswith("证据不足") and "长期踏空警报" in m["alerts"] and m["up_capture_vs_btc"] == pytest.approx(0.0)
    assert m["horizons"]["365d"] == "未到期" and isinstance(m["horizons"]["180d"], dict)
    q = 10000 / (100 * 1.0005 * 1.001); assert m["bh_btc"]["net_profit_usdt"] == pytest.approx(q * 140 * 0.9995 * 0.999 - 10000)


def test_forward_eval_scorecard_after_one_year():
    n = 400; btc = np.concatenate([np.linspace(100, 60, 150), np.linspace(60, 160, 250)]); liq = 10000 * btc / 100
    m = FE.metrics(_df(n, liq, btc, [True] * n))
    assert set(m["scorecard"]) >= {"capital_protection", "upside_participation", "net_vs_bh", "result"} and m["scorecard"]["capital_protection"] is False   # 回撤与买入持有相同 → 不满足 0.6 倍


def test_forward_eval_refuses_output_inside_log_dir(tmp_path):
    (tmp_path / "acc").mkdir()
    with pytest.raises(SystemExit):
        FE.main(["--log-dir", str(tmp_path), "--out", str(tmp_path / "x")])
