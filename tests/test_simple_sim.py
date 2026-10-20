"""简化版多空教学模拟：约束（止损/仓位/流动性/不重叠）、达标即停、做空仅限核实品种、成本与资金成本、如实未达标。"""
import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.simple_sim.engine import DISCLAIMER, SimpleSimConfig, load_simple_config, shortable, simulate_day
from bgw_quant.target_model.execution import bar_liquidity
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def tok():
    return apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", SAMPLE_DAY, venue="token"))


def test_default_not_shortable():
    ok, info = shortable("NVDA")
    assert ok is False and info == {}


def test_constraints_and_stop_at_target(tok):
    cfg = load_simple_config()
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    r = simulate_day(tok, cm, 100_000, 1_000, cfg, allow_short=False)
    assert r.disclaimer == DISCLAIMER and r.n_short == 0 and not r.shortable
    liq = bar_liquidity(tok, cfg.participation_rate)
    for t in r.trades:
        assert t.size_usd <= 100_000 * cfg.position_fraction + 1e-6
        assert t.mae_pct >= -cfg.stop_loss_pct - 1e-9
        assert t.net_ret >= cfg.min_trade_net_pct / 100 - 1e-9
        for f in t.open_fill.fills + t.close_fill.fills:
            assert f.notional_usd <= liq[f.idx] + 1e-6
        assert t.capital_cost_usd >= 0
    for a, b in zip(r.trades, r.trades[1:]):
        assert a.close_fill.end_idx < b.open_fill.start_idx
    assert r.realized_net_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
    assert r.n_buys == r.n_sells == len(r.trades) and r.n_long == len(r.trades)
    if r.target_met:
        assert r.trades[-1].cum_net_pnl_usd >= 1_000 - 1e-9
        assert all(t.cum_net_pnl_usd < 1_000 for t in r.trades[:-1])     # 达标后立即停止
        assert r.target_hit_ts and r.stop_reason
    assert len(r.cum_curve) == len(tok) and r.cum_curve[-1] == pytest.approx(r.realized_net_usd)


def test_short_trades_only_when_allowed(tok):
    cfg = load_simple_config()
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    # 构造持续下跌的一天：只有做空能赚钱
    down = tok.copy()
    f = np.linspace(1.0, 0.94, len(down))
    for c in ["open", "high", "low", "close"]:
        down[c] = down[c].to_numpy() * f
    r_no = simulate_day(down, cm, 50_000, 500, cfg, allow_short=False)
    r_yes = simulate_day(down, cm, 50_000, 500, cfg, allow_short=True, short_borrow_annual=0.05)
    assert r_no.n_short == 0
    assert r_yes.n_short >= 1 and r_yes.realized_net_usd > r_no.realized_net_usd
    for t in r_yes.trades:
        if t.direction == "short":
            assert t.avg_close < t.avg_open and t.capital_cost_usd > 0      # 做空需价格下跌且计提借券费
            assert t.net_pnl_usd == pytest.approx(t.open_fill.filled_usd - t.close_fill.filled_usd
                                                   - sum(cm.p.gas_usd * max(1, int(np.ceil(x.notional_usd / cm.p.max_tx_usd))) for x in t.open_fill.fills + t.close_fill.fills)
                                                   - t.capital_cost_usd, rel=1e-6)


def test_unmet_target_reported_honestly(tok):
    cfg = load_simple_config()
    cm = CostModel(load_cost_profile("token_dex"))
    r = simulate_day(tok, cm, 5_000_000, 100_000, cfg, allow_short=False)
    assert not r.target_met and r.realized_net_usd < 100_000 and any("未达标" in n for n in r.notes)
    assert r.capital_utilization < 1.0 and any("流动性限制" in n for n in r.notes)


def test_daily_loss_limit_and_no_leverage():
    cfg = SimpleSimConfig(position_fraction=3.0)      # 配置超过 1 也会被截断到 1
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=40, freq="1min", tz="UTC"), "symbol": "X", "venue": "token",
                         "source": "s", "quality": 0, "close": np.linspace(100, 104, 40), "amount": 1e7})
    bars["open"] = bars["close"]; bars["high"] = bars["close"] * 1.001; bars["low"] = bars["close"] * 0.999
    bars["ts_ny"] = bars["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    r = simulate_day(bars, CostModel(load_cost_profile("native_broker")), 10_000, 50, cfg, allow_short=False)
    assert all(t.size_usd <= 10_000 + 1e-6 for t in r.trades)
    assert r.target_met and r.trades
