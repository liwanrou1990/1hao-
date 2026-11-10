"""第十六阶段：BTC 日线拼接无缺口且来源可追溯；成本模型不使用价格；链上 / 成本滞后 1 日；周线只在周日更新；
成本模型可靠性检查（原版未通过、修正版通过，均如实记录）；战役引擎次日开盘成交、恒等式成立、X3 未武装不卖、r 从不 ≤ 1 时不建仓；
基准与第十阶段一致；运行只写输出目录。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.holding.engine import HoldingBacktester
from brando_pa.holding.metrics import summarize
from brando_pa.pa15 import data15
from brando_pa.pa16 import data16, phase16
from brando_pa.pa16.engine16 import CampaignBacktester

ROOT = Path(__file__).resolve().parents[1]
P10 = ROOT / "docs" / "brando_pa_validation" / "phase10_holding" / "phase10_results.json"
pytestmark = pytest.mark.skipif(not (data16.RAW16 / "coinbase_btc_usd_1d.csv").exists() or not (data15.RAW / "coinmetrics_btc_1d.csv").exists(), reason="raw snapshots missing")


@pytest.fixture(scope="module")
def ctx():
    d = data16.load_btc_daily(); f = data16.signal_frame(d)
    return {"d": d, "f": f}


def test_price_splice_has_no_gaps_and_sources_in_order(ctx):
    d = ctx["d"]; ts = pd.DatetimeIndex(d["ts"])
    assert ts.is_monotonic_increasing and len(pd.date_range(ts.min(), ts.max(), freq="1D").difference(ts)) == 0
    seg = d.groupby("source")["ts"].agg(["min", "max"]).sort_values("min")
    assert list(seg.index) == ["coinmetrics_close_only", "coinbase_btc_usd", "phase10(binance/okx)"]
    assert not d.loc[d.source == "coinmetrics_close_only", "ohlc_ok"].any() and d.loc[d.source != "coinmetrics_close_only", "ohlc_ok"].all()
    assert (d["high"] >= d[["open", "close"]].max(axis=1) - 1e-9).all() and (d["low"] <= d[["open", "close"]].min(axis=1) + 1e-9).all()


def test_cost_model_does_not_use_price():
    p = data15.build_panel("btc"); a = data16.miner_cost(p)
    q = p.copy(); q["PriceUSD"] = q["PriceUSD"] * 7.0; q["bci_price_usd"] = 1.0; q["bci_miners_revenue_usd"] = 0.0; q["CapMVRVCur"] = 9.9
    b = data16.miner_cost(q)
    pd.testing.assert_series_equal(a["cost_mid"], b["cost_mid"])                                     # 价格 / 收入 / MVRV 改变，成本不变


def test_cost_and_onchain_lagged_one_day_and_weekly_only_on_sundays(ctx):
    f = ctx["f"]; raw = data16.miner_cost(); mv = data15.build_panel("btc")["CapMVRVCur"]
    for d in ("2015-01-14", "2018-12-15", "2020-05-12", "2022-11-21", "2026-01-05"):
        t = pd.Timestamp(d, tz="UTC")
        assert f.loc[t, "cost_mid"] == pytest.approx(raw.loc[t - pd.Timedelta(days=1), "cost_mid"])
        assert f.loc[t, "mvrv"] == pytest.approx(mv.loc[t - pd.Timedelta(days=1)])
    wk = f[f["is_week_close"]]
    assert (pd.DatetimeIndex(wk.index).dayofweek == 6).all() and f.loc[~f["is_week_close"], "w_close"].isna().all()


def test_reliability_check_outcomes_are_recorded_honestly():
    pre = data16.reliability_checks(variant="preregistered"); cor = data16.reliability_checks(variant="corrected")
    assert pre["verifiable"] is False and len(pre["jump_days_gt50pct_excl_halving"]) > 0                  # 预登记原版未通过（如实保留）
    assert cor["verifiable"] is True and cor["vs_phase15_log_corr"] >= 0.9 and 0.7 <= cor["vs_phase15_median_ratio"] <= 1.4


def test_halving_steps_cost_on_the_day():
    c = data16.miner_cost()
    for h in data16.HALVINGS[1:]:
        before, after = c.loc[h - pd.Timedelta(days=2), "cost_mid"], c.loc[h + pd.Timedelta(days=1), "cost_mid"]
        assert 1.6 < after / before < 2.4, h                                                            # 补贴减半当天成本约翻倍


def test_engine_fills_next_open_identity_and_records_basis(ctx):
    d, f = ctx["d"], ctx["f"]
    for e, x in (("A", "X3"), ("D", "X1"), ("B", "X4"), ("A0", "X5")):
        r = CampaignBacktester(d, f, e, x, 20.0, 5.0, "2014-01-01", None).run()
        assert r.final["identity_ok"]
        o = d.set_index(d["ts"].dt.strftime("%Y-%m-%d"))["open"]
        for fl in r.fills.to_dict("records"):
            assert fl["ref_price"] == pytest.approx(float(o.loc[fl["ts"]]))                               # 次日开盘成交
            assert pd.Timestamp(fl["basis"]["date"]) < pd.Timestamp(fl["ts"])                              # 依据日期早于成交日
            assert fl["rule"] and fl["data_label"] == "历史结果（回测）"
            if fl["side"] == "BUY":
                assert fl["fill_price"] == pytest.approx(fl["ref_price"] * 1.0005)


def test_no_entry_when_cost_ratio_never_low(ctx):
    d, f = ctx["d"], ctx["f"].copy(); f["r"] = 5.0
    for e in ("A", "A0", "B", "C", "D"):
        r = CampaignBacktester(d, f, e, "X3", 5.0, 5.0, "2014-01-01", None, c_confirm=np.ones(len(d), bool)).run()
        assert r.final["n_fills"] == 0 and r.final["liquidation_usdt"] == pytest.approx(10000.0)


def test_x3_never_sells_before_weekly_trend_arms(ctx):
    d, f = ctx["d"], ctx["f"].copy(); f["sma40w"] = np.where(f["is_week_close"], 1e12, np.nan)       # 周线永远低于 40 周 SMA → 永不武装
    r = CampaignBacktester(d, f, "A0", "X3", 5.0, 5.0, "2014-01-01", None).run()
    assert (r.fills["side"] == "BUY").sum() == 1 and (r.fills["side"] == "SELL").sum() == 0


def test_baselines_match_phase10_on_overlapping_window():
    if not P10.exists():
        pytest.skip("phase10 results missing")
    p10 = json.loads(P10.read_text()); end = p10["latest_complete_day"]; ref = {r["name"]: r for r in p10["rows"] if r["window"] == "oos" and r["cost_bps"] == 15}
    d = data16.load_btc_daily(); bars = {"BTCUSDT": d[["ts", "open", "high", "low", "close", "volume"]]}
    s = summarize(HoldingBacktester(bars, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, "2024-01-01", end).run())
    assert s["liquidation_net_profit_usdt"] == pytest.approx(ref["B1_BTC_SMA200_daily_repro"]["liquidation_net_profit_usdt"], abs=1e-6)
    s = summarize(HoldingBacktester(bars, {"name": "BH", "assets": ["BTCUSDT"], "mode": "buyhold"}, 10.0, 5.0, "2024-01-01", end).run())
    assert s["liquidation_net_profit_usdt"] == pytest.approx(ref["S8a_BUYHOLD_BTC"]["liquidation_net_profit_usdt"], abs=1e-6)


def test_episodes_definition(ctx):
    eps = phase16.episodes(ctx["f"], 1.0)
    assert eps and eps[0]["first_date"].startswith("2015-01") and all(e["further_drawdown"] <= 0 for e in eps)
    assert all(e["days_to_min"] >= 0 for e in eps)


def test_run_writes_only_to_output_dir(tmp_path):
    def h(dd: Path):
        return {str(p.relative_to(dd)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dd.rglob("*")) if p.is_file()} if dd.exists() else {}
    watch = [data16.RAW16, data15.RAW, ROOT / "data_store" / "brando_pa" / "phase10_holding", ROOT / "reports_out" / "brando_pa" / "phase8_forward"]
    before = [h(x) for x in watch]
    r = phase16.run_phase16(cfg(), out_dir=tmp_path, windows={"valid": ("2018-01-01", "2021-12-31")}, costs=[10], combos=[("A", "X3")])
    assert (tmp_path / "phase16_results.json").exists() and r["verifiable"] and r["real_trading"].startswith("disabled")
    assert {x["name"] for x in r["rows"]} == {"BH_BTC", "B1_BTC_SMA200", "B2_modelB_state_machine", "A_X3"}
    assert [h(x) for x in watch] == before


def test_current_readings_are_research_only_and_label_invalidation():
    from brando_pa.pa16 import current16
    r = current16.readings(online=False)
    assert "研究性观察" in r["label"] and r["forward_signal"].startswith("无")
    inv = r["invalidation"]
    if inv["last_confirmed_daily_swing_low"] is not None and inv["last_confirmed_daily_swing_low"] >= r["price_used"]:
        assert inv["applicable"] is False and all(v is None for v in r["reward_risk_to_first_cost_exit"].values())   # 失效位高于现价 → 不适用，不算盈亏比
    assert r["cost_band"]["low"] < r["cost_band"]["mid"] < r["cost_band"]["high"]


def test_forward_observation_log_is_hash_chained(tmp_path):
    from brando_pa.pa16 import current16
    p = tmp_path / "obs.jsonl"; a = current16.append_observation(online=False, path=p); b = current16.append_observation(online=False, path=p)
    assert a["prev_hash"] == "0" * 64 and b["prev_hash"] == a["hash"] and len(p.read_text().splitlines()) == 2


def test_dashboard_page_has_no_executable_signal(tmp_path):
    if not (phase16.OUT_DIR / "phase16_results.json").exists():
        pytest.skip("run phase16 first")
    from brando_pa.pa16 import dashboard16
    page = dashboard16.build(online=False, out=tmp_path).read_text(encoding="utf-8")
    assert "BTC矿工成本与长期投资周期" in page and "前瞻信号：无" in page and "研究性观察" in page and "历史结果（回测）" in page and "当前研究估计" in page
    assert "可执行买入信号：" not in page and "立即买入" not in page
