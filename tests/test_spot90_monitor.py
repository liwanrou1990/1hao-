"""第四阶段测试：趋势/风险规则、新鲜度判定、参考价多源回退（Mock）、持仓计算、方案比较引擎（合成数据）、快照结构。"""
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from spot90.monitor.live import daily_bar_freshness, fetch_live_prices
from spot90.monitor.plans import simulate, summarize
from spot90.monitor.position import PositionPlan, Tranche, scenario_table
from spot90.monitor.rules import enrich, risk_score_frame, trend_state


def test_trend_state_rules():
    assert trend_state(110, 105, 100, 0.02) == "强势上涨"
    assert trend_state(90, 95, 100, -0.02) == "强势下跌"
    assert trend_state(106, 110, 100, 0.02) == "上涨"
    assert trend_state(95, 90, 100, -0.01) == "下跌"
    assert trend_state(101, 99, 100, -0.01) == "震荡"
    assert trend_state(100, float("nan"), float("nan"), float("nan")) == "数据不足"


def test_freshness():
    now = datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)
    assert daily_bar_freshness(pd.Timestamp("2026-10-27", tz="UTC"), now)["lag_days"] == 0
    f = daily_bar_freshness(pd.Timestamp("2026-10-24", tz="UTC"), now)
    assert f["lag_days"] == 3 and "延迟" in f["status"]


def test_live_price_fallback_and_delay_flag():
    def handler(req: httpx.Request):
        if "bitget" in req.url.host:
            return httpx.Response(403)
        if "llama" in req.url.host:
            return httpx.Response(200, json={"coins": {"coingecko:bitcoin": {"price": 80000.0, "timestamp": 1_700_000_000}}})   # 很旧的时间戳 → 延迟
        return httpx.Response(200, json={"ethereum": {"usd": 2300.0, "last_updated_at": int(datetime.now(timezone.utc).timestamp())}})
    out = fetch_live_prices(["BTCUSDT", "ETHUSDT"], transport=httpx.MockTransport(handler))
    assert out["BTCUSDT"]["source"] == "defillama_coingecko_current" and "延迟" in out["BTCUSDT"]["status"]
    assert out["ETHUSDT"]["source"] == "coingecko_simple_price" and out["ETHUSDT"]["status"] == "正常"

    def all_fail(req):
        return httpx.Response(500)
    out2 = fetch_live_prices(["BTCUSDT"], transport=httpx.MockTransport(all_fail))
    assert out2["BTCUSDT"]["price"] is None and out2["BTCUSDT"]["status"] == "不可用"


def test_position_math():
    r = PositionPlan([Tranche(100.0, 1000.0)], stop_loss_pct=0.10, fee_bps=10, slippage_bps=5).compute(100.0)
    assert r["quantity"] == pytest.approx(9.985) and r["avg_cost"] == pytest.approx(1000 / 9.985)
    assert r["stop_price"] == pytest.approx(r["avg_cost"] * 0.9)
    assert r["loss_at_stop_usdt"] < -100 * 0.9                     # 止损亏损 ≥ 10% + 双边费用
    assert r["breakeven_price"] > r["avg_cost"] > 100.0
    tab = scenario_table(100.0, 1000.0, discounts_pct=(0, 5), stop_loss_pcts=(0.1,))
    assert len(tab) == 2 and tab[1]["买入价"] == 95.0 and tab[1]["止损亏损USDT"] < 0


def _synthetic(n=700, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
    df = pd.DataFrame({"ts": pd.date_range("2022-01-01", periods=n, tz="UTC"), "symbol": "BTCUSDT", "close": close})
    df["open"] = df["close"].shift(1).fillna(100.0) * (1 + rng.normal(0, 0.002, n))
    df["high"] = np.maximum(df["open"], df["close"]) * (1 + np.abs(rng.normal(0, 0.01, n)))
    df["low"] = np.minimum(df["open"], df["close"]) * (1 - np.abs(rng.normal(0, 0.01, n)))
    df["volume"] = 1.0; df["quote_volume"] = 1.0; df["source"] = "synthetic"; df["tf"] = "1d"; df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    return df


def test_plans_engine_consistency():
    df = _synthetic()
    sim = simulate(df, H=90, dca_n=6, dca_k=7, wait_d=3.0, fallback="market", cash_yield=0.0)
    assert len(sim) > 100
    assert (sim["P1_mdd"] <= 0).all() and (sim["P1_mae"] <= sim["P1_ret"] + 1e-9).all()
    # 一次性买入的净收益 = 窗口末收盘/次日开盘 − 费用
    i = 400 + 10
    exp = df["close"].iloc[i + 90] * (1 - 0.0015) / (df["open"].iloc[i + 1] * (1 + 0.0015)) - 1   # capital*(1-mkt)/open 买入，再乘 (1-mkt) 卖出
    got = sim["P1_ret"].iloc[10]
    assert got == pytest.approx(df["close"].iloc[i + 90] * (1 - 0.0015) ** 2 / df["open"].iloc[i + 1] - 1, rel=1e-9)
    # 等待方案：成交率在 (0,1) 之间；全部持币变体的 MDD 更小
    assert 0 < sim["P3_filled"].mean() < 1
    sim_cash = simulate(df, H=90, wait_d=3.0, fallback="cash", cash_yield=0.04)
    assert sim_cash["P3_mdd"].mean() >= sim["P3_mdd"].mean()
    s = summarize(sim, 90)
    assert set(s["plans"]) == {"P1", "P2", "P3"} and "diff_vs_P1_ci95" in s["plans"]["P2"]


def test_risk_score_bounds():
    df = _synthetic(900)
    e = enrich(df)
    rs = risk_score_frame(e).dropna()
    assert rs.between(0, 100).all() and e["risk_level"].isin(["低", "中", "高", "极高", "nan"]).all()
