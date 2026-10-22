"""历史可行性：时间顺序、逐根成交量共享上限、资金片不超本金、收盘价成交、做空仅在允许时、A ≥ B、达标笔数与路径一致。"""
import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.hindsight.feasibility import feasibility, liquidity_from_bars
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def tok():
    return apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", SAMPLE_DAY, venue="token"))


def test_feasibility_constraints_and_bounds(tok):
    prof = load_cost_profile("token_dex_optimistic")
    r = feasibility(tok, prof, 650_000, 7_800, allow_short=False, participation_rate=0.1, exec_cfg={"capital_cost_annual": 0.05})
    liq, src = liquidity_from_bars(tok, 0.1)
    assert src == "kline_amount" and r.liquidity_source == "kline_amount"
    used = np.zeros(len(tok))
    close = tok["close"].to_numpy(float)
    side = CostModel(prof).proportional_side_cost()
    for t in r.trades:
        assert t.buy_idx < t.sell_idx                                   # 时间顺序
        assert t.size_usd <= 650_000 / r.n_tranches + 1e-6               # 资金片不超本金
        assert t.open_exec >= close[t.buy_idx] * (1 + side) - 1e-9       # 买入成交价 ≥ 收盘价+成本（不用最低价）
        assert t.close_exec <= close[t.sell_idx] * (1 - side) + 1e-9     # 卖出成交价 ≤ 收盘价−成本（不用最高价）
        used[t.buy_idx] += t.size_usd
        used[t.sell_idx] += t.size_usd
    assert (used <= liq + 1e-6).all()                                    # 多片共享流动性，不超每根成交量上限
    # 同一资金片内不重叠
    for k in range(1, r.n_tranches + 1):
        tr = sorted([t for t in r.trades if t.tranche == k], key=lambda t: t.buy_idx)
        assert all(a.sell_idx < b.buy_idx for a, b in zip(tr, tr[1:]))
    deployed = np.zeros(len(tok))
    for t in r.trades:
        deployed[t.buy_idx:t.sell_idx + 1] += t.size_usd
    assert deployed.max() <= 650_000 + 1e-6                              # 任一时刻占用 ≤ 本金
    assert r.theoretical_net_usd >= r.executable_net_usd - 1e-6           # A ≥ B
    assert r.executable_net_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
    if r.executable_met:
        assert r.trades_to_target is not None and r.trades[r.trades_to_target - 1].cum_net_pnl_usd >= 7_800 - 1e-9
    else:
        assert r.limits


def test_short_only_when_allowed_and_a_b_reported_separately():
    n = 150
    close = np.linspace(100, 96, n)
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=n, freq="1min", tz="UTC"), "symbol": "X", "venue": "token",
                         "source": "s", "quality": 0, "close": close, "amount": 1e7})
    bars["open"] = bars["close"]; bars["high"] = bars["close"] * 1.001; bars["low"] = bars["close"] * 0.999
    bars["ts_ny"] = bars["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    prof = load_cost_profile("native_broker")
    no = feasibility(bars, prof, 50_000, 500, allow_short=False)
    yes = feasibility(bars, prof, 50_000, 500, allow_short=True, borrow_annual=0.05)
    assert no.n_short == 0 and no.executable_net_usd == 0 and not no.executable_met and no.theoretical_net_usd == 0
    assert yes.n_short >= 1 and yes.executable_met and yes.theoretical_net_usd >= yes.executable_net_usd
    assert yes.directions == ["long", "short"] and no.directions == ["long"]


def test_unreachable_target_reports_limits(tok):
    prof = load_cost_profile("token_dex")
    r = feasibility(tok, prof, 7_500_000, 150_000, allow_short=False)
    assert not r.executable_met and r.trades_to_target is None
    assert any("执行约束" in x or "理论最大" in x for x in r.limits)
    assert r.peak_deployed_usd <= 7_500_000


def test_transactions_liquidity_preferred(tok):
    tx = np.full(len(tok), 2e5)
    liq, src = liquidity_from_bars(tok, 0.1, tx)
    assert src == "transactions" and np.allclose(liq, 2e4)
    liq2, src2 = liquidity_from_bars(tok.assign(amount=np.nan), 0.1)
    assert src2 == "unknown" and np.isinf(liq2).all()
