"""统一收益决策引擎：能力约束（不做空/不杠杆）、因果性、限频与仓位约束、滚动优化结构、原因归类。"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bgw_quant.calendar_utils import trading_days
from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.decision.analysis import classify
from bgw_quant.decision.capability import ProductCapability, capability_for
from bgw_quant.decision.engine import EngineParams, load_engine_config, run_engine_day
from bgw_quant.decision.optimize import optimize_tier
from bgw_quant.simple_sim.multi import theoretical_optimum
from bgw_quant.target_model.execution import bar_liquidity
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def tok():
    return apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", SAMPLE_DAY, venue="token"))


@pytest.fixture(scope="module")
def cfg():
    return load_engine_config()


def test_capability_defaults_no_short_no_leverage():
    cap = capability_for("NVDA")
    assert cap.spot_long and not cap.short and cap.leverage_long == 1.0 and cap.directions == ["long"] and not cap.verified_by_api
    spcx = capability_for("SPCX")
    assert not spcx.spot_long and spcx.directions == []
    api = {"listed": True, "contracts": [{"chain": "sol", "contract": "C1", "data_source": "xstocks", "status": "online"}],
           "stock_info": {"chain_assets": [{"contract": "C1", "market_status": "open", "tx_maximum_buy_usd": "50000"}]}}
    cap2 = capability_for("NVDA", api)
    assert cap2.verified_by_api and cap2.chain == "sol" and cap2.tx_max_buy_usd == 50000 and cap2.market_status == "open"


def test_engine_never_shorts_without_capability_and_respects_limits(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    cap = ProductCapability("NVDA", spot_long=True, short=False)
    r = run_engine_day(tok, cm, 650_000, 7_800, cfg, EngineParams(z_entry=1.0, tp_atr_mult=1.0, max_open=4), cap)
    assert r.n_short == 0 and all(t.direction == "long" for t in r.trades)
    liq = bar_liquidity(tok, float(cfg.execution["participation_rate"]))
    used = np.zeros(len(tok))
    for t in r.trades:
        assert t.size_usd <= 650_000 / 4 + 1e-6
        for f in t.open_fill.fills + t.close_fill.fills:
            used[f.idx] += f.notional_usd
    assert (used <= liq + 1e-6).all()
    for i in range(len(tok)):
        assert sum(1 for t in r.trades if t.open_fill.start_idx <= i <= t.close_fill.end_idx) <= 4
        assert sum(1 for t in r.trades if t.open_fill.start_idx == i) * 1 <= int(cfg.execution["api_rate_limit_orders_per_min"])
    assert r.realized_net_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
    assert r.execution_cost_usd >= 0 and r.funding_cost_usd >= 0
    assert set(r.mode_counts) <= {"trend_long", "range_long"}


def test_engine_can_short_only_when_capability_allows(cfg):
    n = 200
    close = np.linspace(100, 95, n)
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=n, freq="1min", tz="UTC"), "symbol": "X", "venue": "token", "source": "s",
                         "quality": 0, "close": close, "amount": 5e6})
    rng = np.random.default_rng(1)
    bars["close"] = bars["close"] * (1 + rng.normal(0, 0.0008, n))
    bars["open"] = bars["close"].shift(1).fillna(bars["close"]); bars["high"] = bars[["open", "close"]].max(axis=1) * 1.0005; bars["low"] = bars[["open", "close"]].min(axis=1) * 0.9995
    bars["ts_ny"] = bars["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    cm = CostModel(load_cost_profile("native_broker"))
    p = EngineParams(z_entry=1.0, tp_atr_mult=1.0, max_open=2, trend_thresh=0.3, edge_min_ratio=0.5)
    no = run_engine_day(bars, cm, 50_000, 500, cfg, p, ProductCapability("X", short=False))
    yes = run_engine_day(bars, cm, 50_000, 500, cfg, p, ProductCapability("X", short=True, short_borrow_rate_annual=0.05))
    assert no.n_short == 0 and no.pause_reasons.get("下跌趋势但产品不支持做空", 0) > 0
    assert yes.n_short >= 1
    for t in yes.trades:
        if t.direction == "short":
            assert t.funding_cost_usd > 0


def test_engine_is_causal(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    cap = ProductCapability("NVDA")
    p = EngineParams(z_entry=1.0, tp_atr_mult=1.0, max_open=4)
    full = run_engine_day(tok, cm, 100_000, 950, cfg, p, cap)
    other = apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", date(2026, 10, 15), venue="token")).reset_index(drop=True)
    k = 220
    m = min(len(tok), len(other))
    tail = other.iloc[k:m].copy()
    tail["ts_ny"], tail["ts"] = tok["ts_ny"].iloc[k:m].to_numpy(), tok["ts"].iloc[k:m].to_numpy()
    mixed = pd.concat([tok.iloc[:k], tail], ignore_index=True)
    alt = run_engine_day(mixed, cm, 100_000, 950, cfg, p, cap)
    sig = lambda runs: [(t.open_fill.start_idx, t.close_fill.end_idx, round(t.net_pnl_usd, 6)) for t in runs.trades if t.close_fill.end_idx < k - 6]
    assert sig(full) == sig(alt)


def test_optimize_tier_structure_and_classification(cfg):
    src = SimulatedSource()
    days = trading_days(date(2026, 8, 24), SAMPLE_DAY)
    bars = pd.concat([apply_quality_flags(src.fetch_minute_bars("NVDA", d, venue="token")) for d in days], ignore_index=True)
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    cap = ProductCapability("NVDA")
    small = cfg
    small.grid = {"z_entry": [1.0, 2.0], "tp_atr_mult": [1.0], "max_open": [4]}
    hist = bars[pd.to_datetime(bars["ts_ny"]).dt.date < SAMPLE_DAY]
    opt = optimize_tier(hist, cm, 100_000, 950, small, cap, train_days=12, test_days=4)
    assert opt.folds and opt.oos_stats.n_days == len(opt.oos_days)
    for f in opt.folds:
        assert f.train[1] < f.test[0]
    assert 0 <= opt.oos_stats.attainment_rate <= 1
    day_bars = bars[pd.to_datetime(bars["ts_ny"]).dt.date == SAMPLE_DAY].reset_index(drop=True)
    run = run_engine_day(day_bars, cm, 100_000, 950, small, opt.final_params, cap)
    th = theoretical_optimum(day_bars, load_cost_profile("token_dex_optimistic"), 100_000, 950)
    cls = classify(day_bars, cm, cap, run, th.max_net_profit_usd, 950, 0.1, 5, opt.oos_stats.attainment_rate)
    assert "tags" in cls and "details" in cls and cls["round_trip_cost_pct"] > 0
    if not run.target_met:
        assert cls["tags"]
