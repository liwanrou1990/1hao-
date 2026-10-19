"""事后最优路径：DP 正确性（暴力对照）、约束（资金/流动性/不重叠）、目标达标与最少交易次数、组合。"""
import itertools
import math

import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import CostModel, CostProfile, load_cost_profile
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.hindsight.optimal_path import DISCLAIMER, optimal_path, profit_matrix
from bgw_quant.hindsight.portfolio import portfolio_optimal
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def token_bars():
    return {s: apply_quality_flags(SimulatedSource().fetch_minute_bars(s, SAMPLE_DAY, venue="token")) for s in ["NVDA", "MSFT", "TSLA"]}


def _brute(pi, kmax=None):
    n = pi.shape[0]
    best, best_pairs = 0.0, []
    pairs_all = [(b, s) for b in range(n) for s in range(b + 1, n) if np.isfinite(pi[b, s])]
    for k in range(1, (kmax or n // 2) + 1):
        for combo in itertools.combinations(pairs_all, k):
            ok = all(combo[i][1] < combo[i + 1][0] for i in range(len(combo) - 1))
            if ok:
                v = sum(pi[b, s] for b, s in combo)
                if v > best:
                    best, best_pairs = v, list(combo)
    return best, best_pairs


def test_dp_matches_brute_force_with_liquidity_caps(token_bars):
    cm = CostModel(load_cost_profile("native_broker"))
    small = token_bars["NVDA"].iloc[:10].reset_index(drop=True)
    small.loc[3, "amount"] = 500.0          # 人为制造一根流动性极低的K线
    cap = 50_000.0
    r = optimal_path(small, cm, cap, participation_rate=0.1, target_return=0.0001)
    pi, S, known = profit_matrix(small, cm, cap, 0.1)
    bf, bf_pairs = _brute(pi)
    assert r.max_net_profit_usd == pytest.approx(bf, rel=1e-9)
    assert known and (S <= cap + 1e-9).all()
    assert S[3, 5] == pytest.approx(min(cap, 500 * 0.1, small.loc[5, "amount"] * 0.1))
    # 最少交易次数：k 以内的最优值单调不减，且与暴力一致
    for k, v in r.best_by_k.items():
        assert v == pytest.approx(_brute(pi, kmax=k)[0], rel=1e-9)
    ks = sorted(r.best_by_k)
    assert all(r.best_by_k[a] <= r.best_by_k[b] + 1e-9 for a, b in zip(ks, ks[1:]))


def test_constraints_hold_on_full_day(token_bars):
    cm = CostModel(load_cost_profile("token_dex"))
    for cap in (1_000, 100_000, 5_000_000):
        r = optimal_path(token_bars["NVDA"], cm, cap, target_return=0.008, cost_profile="token_dex")
        for t in r.trades:
            assert t.buy_idx < t.sell_idx
            assert t.size_usd <= cap + 1e-9                       # 不超额使用本金
            assert t.size_usd <= min(token_bars["NVDA"]["amount"].iloc[t.buy_idx], token_bars["NVDA"]["amount"].iloc[t.sell_idx]) * 0.1 + 1e-6
            assert t.net_pnl_usd == pytest.approx(t.proceeds_usd - t.size_usd - 2 * cm.p.gas_usd * t.child_orders, rel=1e-9)
        assert all(a.sell_idx < b.buy_idx for a, b in zip(r.trades, r.trades[1:]))   # 同一时刻只有一个持仓
        assert r.max_net_profit_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
        assert r.disclaimer == DISCLAIMER and r.venue == "token" and r.data_is_simulated
        if cap >= 100_000:
            assert r.liquidity_capped_any and r.max_deployed_usd < cap
    big = optimal_path(token_bars["NVDA"], cm, 5_000_000, target_return=0.02)
    assert big.target_met is False and big.min_trades_for_target is None and any("不可达" in n for n in big.notes)


def test_target_path_uses_min_trades(token_bars):
    cm = CostModel(load_cost_profile("token_dex"))
    r = optimal_path(token_bars["NVDA"], cm, 10_000, target_return=0.008)
    assert r.target_met and r.min_trades_for_target is not None
    assert len(r.target_path) == r.min_trades_for_target
    assert sum(t.net_pnl_usd for t in r.target_path) >= r.target_profit_usd - 1e-6
    assert r.min_trades_for_target <= r.n_trades


def test_unknown_liquidity_is_flagged():
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=6, freq="1min", tz="UTC"), "symbol": "X", "venue": "token",
                         "source": "bitget_rwa:ondo", "quality": 64, "close": [100, 101, 103, 102, 104, 105]})
    bars["ts_ny"] = bars["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    bars["amount"] = np.nan
    r = optimal_path(bars, CostModel(load_cost_profile("token_dex_optimistic")), 1_000, target_return=0.001)
    assert not r.liquidity_known and r.unverifiable and any("流动性未知" in n for n in r.notes)
    assert r.n_trades >= 1


def test_no_trade_when_costs_dominate():
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=5, freq="1min", tz="UTC"), "symbol": "X", "venue": "token",
                         "source": "s", "quality": 0, "close": [100, 100.1, 100.2, 100.1, 100.0], "amount": 1e6})
    bars["ts_ny"] = bars["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    prof = CostProfile("huge", 500, 0, 0, 0, 0, 1e9, 1e9, 1, 1, 0, 1)
    r = optimal_path(bars, CostModel(prof), 1000, target_return=0.001)
    assert r.n_trades == 0 and r.max_net_profit_usd == 0 and r.target_met is False


def test_portfolio_sequential_beats_or_equals_best_single(token_bars):
    cm = CostModel(load_cost_profile("token_dex"))
    cap = 20_000
    pr = portfolio_optimal(token_bars, cm, cap, target_return=0.009, cost_profile="token_dex")
    singles = {s: optimal_path(b, cm, cap).max_net_profit_usd for s, b in token_bars.items()}
    assert pr.sequential_profit_usd >= max(singles.values()) - 1e-6
    # 轮动路径时间不重叠（跨股票）
    seq = sorted(pr.sequential_trades, key=lambda t: t.buy_ts)
    assert all(a.sell_ts < b.buy_ts for a, b in zip(seq, seq[1:]))
    assert sum(pr.parallel_allocation.values()) <= cap + 1e-6     # 并行均分不超本金
    assert pr.best_profit_usd == max(pr.sequential_profit_usd, pr.parallel_profit_usd)
    assert pr.target_met in (True, False)
    d = pr.to_dict()
    assert "disclaimer" in d and d["sequential"]["n_trades"] == len(pr.sequential_trades)
