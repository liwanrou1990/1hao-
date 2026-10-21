"""多笔分批可执行模拟、理论最优对照、失败原因分解。"""
import numpy as np
import pytest

from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.simple_sim.engine import load_simple_config
from bgw_quant.simple_sim.multi import MultiParams, auto_search, failure_analysis, simulate_day_multi, theoretical_optimum, theoretical_profile
from bgw_quant.target_model.execution import bar_liquidity
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def tok():
    return apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", SAMPLE_DAY, venue="token"))


def test_multi_respects_shared_liquidity_and_limits(tok):
    cfg = load_simple_config()
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    r = simulate_day_multi(tok, cm, 2_000_000, 28_000, cfg, MultiParams(max_open=8, min_trade_net_pct=0.1), allow_short=False)
    liq = bar_liquidity(tok, cfg.participation_rate)
    used = np.zeros(len(tok))
    for t in r.trades:
        assert t.size_usd <= 2_000_000 / 8 + 1e-6                    # 单笔 ≤ 本金/并行数
        assert t.mae_pct >= -cfg.stop_loss_pct - 1e-9
        assert t.net_ret >= 0.1 / 100 - 1e-9
        for f in t.open_fill.fills + t.close_fill.fills:
            used[f.idx] += f.notional_usd
    assert (used <= liq + 1e-6).all()                                 # 各仓位共享流动性，不重复计算
    for i in range(len(tok)):
        assert sum(1 for t in r.trades if t.open_fill.start_idx <= i <= t.close_fill.end_idx) <= 8
    assert sum(t.size_usd for t in r.trades if t.open_fill.start_idx <= 0 <= t.close_fill.end_idx) <= 2_000_000
    assert r.realized_net_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
    assert r.n_short == 0 and r.max_concurrent <= 8 and 0 <= r.avg_utilization <= r.capital_utilization <= 1
    if r.trades:
        assert r.trades[-1].cum_net_pnl_usd == pytest.approx(r.realized_net_usd)


def test_auto_search_prefers_target_then_profit(tok):
    cfg = load_simple_config()
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    best, tried = auto_search(tok, cm, 650_000, 7_800, cfg, grid={"max_open": [1, 4], "min_trade_net_pct": [0.1]})
    assert len(tried) == 2 and best.hindsight_tuned
    if any(t["target_met"] for t in tried):
        assert best.target_met
    else:
        assert best.realized_net_usd == max(t["realized_net_usd"] for t in tried)


def test_theoretical_is_upper_bound_and_separated(tok):
    cfg = load_simple_config()
    prof = load_cost_profile("token_dex_optimistic")
    cm = CostModel(prof)
    th = theoretical_optimum(tok, prof, 650_000, 7_800)
    ex, _ = auto_search(tok, cm, 650_000, 7_800, cfg, grid={"max_open": [4], "min_trade_net_pct": [0.1]})
    assert th.max_net_profit_usd >= ex.realized_net_usd - 1e-6
    assert theoretical_profile(prof).impact_coeff == 0 and "纯上限" in th.notes[0]
    fa = failure_analysis(tok, cm, 650_000, 7_800, cfg, th, ex)
    assert {"round_trip_cost_pct", "move_p90_pct", "theoretical_max_usd", "executable_usd", "reasons"} <= set(fa)
    if not ex.target_met:
        assert fa["reasons"]


def test_unmet_large_tier_has_reasons(tok):
    cfg = load_simple_config()
    prof = load_cost_profile("token_dex")
    cm = CostModel(prof)
    ex, _ = auto_search(tok, cm, 7_500_000, 150_000, cfg, grid={"max_open": [8], "min_trade_net_pct": [0.1]})
    th = theoretical_optimum(tok, prof, 7_500_000, 150_000)
    fa = failure_analysis(tok, cm, 7_500_000, 150_000, cfg, th, ex)
    assert not ex.target_met and ex.realized_net_usd < 150_000
    assert any("流动性" in r or "成本" in r for r in fa["reasons"])
