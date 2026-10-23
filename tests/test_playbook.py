"""历史盈利路径模板：约束（时间顺序、本金、流动性、成交价不用高低点）、目标优先 + 低风险、达标截断、不可达如实、跨资产切换、事后标注。"""
import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.decision.capability import capability_for
from bgw_quant.playbook.optimizer import optimize_playbook
from bgw_quant.rotation.universe import make_asset_day
from tests.conftest import SAMPLE_DAY


def _asset(sym, bars):
    prof = load_cost_profile("crypto_spot" if sym in ("BTC", "ETH", "SOL", "HYPE") else "token_dex_optimistic")
    return make_asset_day(sym, bars, prof, capability_for(sym), SAMPLE_DAY)


@pytest.fixture(scope="module")
def assets():
    src = SimulatedSource()
    out = [_asset(s, apply_quality_flags(src.fetch_minute_bars(s, SAMPLE_DAY, venue="token"))) for s in ("NVDA", "TSLA")]
    out += [_asset(c, apply_quality_flags(src.fetch_crypto_day(c, SAMPLE_DAY))) for c in ("SOL",)]
    return out


def test_constraints_and_ledger(assets):
    r = optimize_playbook(assets, 60_000, 540, str(SAMPLE_DAY), tier="T2")
    assert r.n_trades == len(r.trades) and r.trades
    by = {a.symbol: a for a in assets}
    liq_used = {a.symbol: np.zeros(1440) for a in assets}
    for t in r.trades:
        a = by[t.symbol]
        assert t.entry_grid < t.exit_grid and t.exit_grid - t.entry_grid <= 240          # 时间顺序、最长持有
        bi, si = a.bar_at_grid[t.entry_grid], a.bar_at_grid[t.exit_grid]
        assert bi >= 0 and si >= 0
        close = a.bars["close"].to_numpy(float)
        side = a.cost.proportional_side_cost()
        assert t.entry_price >= close[bi] * (1 + side) - 1e-9 and t.exit_price <= close[si] * (1 - side) + 1e-9   # 不用最低/最高价成交
        assert t.cost_usd > 0 and t.size_usd > 0 and t.direction == "long"
        liq_used[t.symbol][t.entry_grid] += t.size_usd
        liq_used[t.symbol][t.exit_grid] += t.size_usd
    for a in assets:                                                                      # 不超过每分钟可成交量
        amt = np.zeros(1440); amt[a.grid_of_bar] = a.bars["amount"].to_numpy(float) * 0.10
        assert (liq_used[a.symbol] <= amt + 1e-6).all()
    dep = np.zeros(1440)
    for t in r.trades:
        dep[t.entry_grid:t.exit_grid + 1] += t.size_usd
    assert dep.max() <= 60_000 + 1e-6 and r.peak_deployed_usd <= 60_000 + 1e-6       # 不超本金
    cum = np.cumsum([t.net_pnl_usd for t in r.trades])
    assert np.allclose(cum, [t.cum_net_pnl_usd for t in r.trades])
    assert r.net_usd == pytest.approx(float(cum[-1]))
    assert "事后" in r.disclaimer and "不代表未来" in r.disclaimer


def test_target_first_then_min_risk_and_truncation(assets):
    r = optimize_playbook(assets, 60_000, 540, str(SAMPLE_DAY), tier="T2")
    if r.target_met:
        assert r.trades[-1].reaches_target and r.trades[-1].cum_net_pnl_usd >= 540 - 1e-9
        assert all(not t.reaches_target for t in r.trades[:-1])                           # 首次达标即截断
        assert r.time_to_target_ts == r.trades[-1].exit_ts
        assert r.max_achievable_net_usd >= r.net_usd - 1e-6                             # 最高可实现 ≥ 模板净收益
        chosen = min(r.candidates, key=lambda c: (c["risk_score_usd"], c["n_trades"]))
        assert chosen["risk_score_usd"] == pytest.approx(r.risk_score_usd)               # 候选中风险最小者
    else:
        assert r.gap_usd > 0 and any("不可达" in x for x in r.limits)


def test_infeasible_target_reports_max_not_fake(assets):
    r = optimize_playbook(assets, 60_000, 50_000, str(SAMPLE_DAY), tier="X")              # 目标 83%/天，不可达
    assert not r.target_met and r.net_usd == pytest.approx(r.max_achievable_net_usd) and r.gap_usd > 0
    assert any("不可达" in x and "最高可实现" in x for x in r.limits)
    assert not any(t.reaches_target for t in r.trades)


def test_cross_asset_switching_and_single_mode(assets):
    r = optimize_playbook(assets, 650_000, 7_800, str(SAMPLE_DAY), tier="T4")
    assert set(r.assets_used) <= {"NVDA", "TSLA", "SOL"}
    sw = sum(1 for a, b in zip(r.trades, r.trades[1:]) if a.symbol != b.symbol)
    assert r.n_switches == sw
    single = optimize_playbook([assets[0]], 650_000, 7_800, str(SAMPLE_DAY), mode="single:NVDA", tier="T4")
    assert single.assets_used in ([], ["NVDA"]) and single.n_switches == 0
    # 跨资产最高可实现 ≥ 单资产最高可实现（更多候选区间）
    assert r.max_achievable_net_usd >= single.max_achievable_net_usd - 1e-6


def test_no_tradable_direction_means_no_trades():
    src = SimulatedSource()
    spcx = _asset("SPCX", apply_quality_flags(src.fetch_minute_bars("SPCX", SAMPLE_DAY, venue="token")))
    r = optimize_playbook([spcx], 60_000, 540, str(SAMPLE_DAY))
    assert r.n_trades == 0 and not r.target_met and r.limits
