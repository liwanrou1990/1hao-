"""阶段二：成本模型、事后最优 DP、当时可执行复盘、反未来函数。"""
import itertools

import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import CostModel, CostProfile, load_cost_profile
from bgw_quant.replay.compare import run_ab
from bgw_quant.replay.executable import ExecutableReplay, prefix_consistency_check
from bgw_quant.replay.hindsight import optimal_trades
from bgw_quant.reports.charts import render_ab_chart_html
from bgw_quant.strategies.rules import all_strategies, make_strategy


def test_cost_profile_scaling_and_capacity_flags():
    p = load_cost_profile("token_dex")
    cm = CostModel(p)
    assert cm.proportional_side_cost() == pytest.approx((p.fee_bps + p.spread_bps + p.slippage_bps) / 1e4)
    assert cm.impact_fraction(1e4) < cm.impact_fraction(1e6)
    assert cm.n_child_orders(p.max_tx_usd * 2.5) == 3
    assert cm.is_unverifiable(p.depth_usd * 0.2) and not cm.is_unverifiable(p.depth_usd * 0.05)
    s = p.scaled(cost_mult=2)
    assert s.fee_bps == 2 * p.fee_bps and s.depth_usd == p.depth_usd


def test_execute_buy_sell_prices_and_costs():
    cm = CostModel(load_cost_profile("token_dex"))
    f = cm.execute("buy", 100.0, 10_000)
    assert f.filled and f.exec_price > 100 and f.total_cost_usd > 0 and f.gas_usd == cm.p.gas_usd
    g = cm.execute("sell", 100.0, 10_000)
    assert g.exec_price < 100
    tiny = cm.execute("buy", 100.0, 0.5)
    assert not tiny.filled
    big = cm.execute("buy", 100.0, cm.p.depth_usd)
    assert big.unverifiable and "不可验证" in big.note and big.gas_usd == cm.p.gas_usd * cm.n_child_orders(cm.p.depth_usd)


def test_stochastic_fills_can_fail_or_partial():
    prof = load_cost_profile("token_dex").scaled(fill_prob_override=0.0)
    cm = CostModel(prof, deterministic=False)
    f = cm.execute("buy", 100.0, 1000)
    assert not f.filled and f.gas_usd > 0  # 失败仍付 Gas


def _brute_force(p, c, gas, cap):
    n = len(p)
    best = cap
    for k in range(0, n // 2 + 1):
        for idx in itertools.combinations(range(n), 2 * k):
            cash = cap
            for b, s in zip(idx[0::2], idx[1::2]):
                q = (cash - gas) / (p[b] * (1 + c))
                cash = q * p[s] * (1 - c) - gas
            best = max(best, cash)
    return best


def test_hindsight_dp_matches_brute_force(nvda_day):
    cm = CostModel(load_cost_profile("native_broker"))
    small = nvda_day.iloc[:12].reset_index(drop=True)
    h = optimal_trades(small, cm, 10_000)
    c = cm.proportional_side_cost() + cm.impact_fraction(10_000)
    bf = _brute_force(small["close"].to_numpy(), c, cm.p.gas_usd, 10_000)
    assert h.final_cash_usd == pytest.approx(bf, rel=1e-9)
    # 交易必须成对、时间递增、日终空仓
    for t in h.trades:
        assert t["buy_idx"] < t["sell_idx"]
    assert all(a["sell_idx"] < b["buy_idx"] for a, b in zip(h.trades, h.trades[1:]))


def test_hindsight_no_trade_when_costs_dominate():
    bars = pd.DataFrame({"ts": pd.date_range("2026-10-16 13:30", periods=5, freq="1min", tz="UTC"),
                         "symbol": "X", "close": [100, 100.1, 100.2, 100.1, 100.0]})
    prof = CostProfile("huge", 500, 0, 0, 0, 0, 1e9, 1e9, 1, 1, 0, 1)
    h = optimal_trades(bars, CostModel(prof), 1000)
    assert h.n_trades == 0 and h.net_return == 0.0


def test_executable_replay_executes_next_bar_open_and_flat_at_close(nvda_day):
    cm = CostModel(load_cost_profile("token_dex"))
    rep = ExecutableReplay(cm, 10_000)
    s = make_strategy("ema_trend")
    r = rep.run(nvda_day, s)
    sig = s.compute(nvda_day).to_numpy()
    for t in r.trades:
        assert t["buy_idx"] == t["signal_idx"] + 1            # 信号后下一根成交
        assert sig[t["signal_idx"]] == 1
        assert t["buy_ref"] == pytest.approx(nvda_day["open"].iloc[t["buy_idx"]])
        assert t["sell_idx"] > t["buy_idx"]
    assert r.equity.iloc[-1] == pytest.approx(r.final_cash_usd)   # 日终空仓
    assert abs(r.net_return) < 1 and r.n_trades >= 1
    # 收盘前 5 根不允许持仓
    assert (sig[-5:] == 0).all()


def test_executable_replay_multi_day_flat_each_day(nvda_multi):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    r = ExecutableReplay(cm, 10_000).run(nvda_multi.iloc[:390 * 3], make_strategy("macd"))
    days = pd.to_datetime(nvda_multi.iloc[:390 * 3]["ts_ny"]).dt.date
    for t in r.trades:
        assert days.iloc[t["buy_idx"]] == days.iloc[t["sell_idx"]]   # 不跨日持仓


@pytest.mark.parametrize("strategy", all_strategies(), ids=lambda s: s.name)
def test_no_lookahead_prefix_consistency(strategy, nvda_day):
    cuts = [40, 97, 150, 233, 300, 385]
    assert prefix_consistency_check(strategy, nvda_day, cuts) == []


def test_lookahead_detector_catches_leaky_strategy(nvda_day):
    class Leaky:
        name = "leaky"
        params = {}

        def compute(self, df):
            return (df["close"].shift(-1) > df["close"]).astype(int)  # 偷看下一根
    assert prefix_consistency_check(Leaky(), nvda_day, [50, 100]) != []


def test_ab_comparison_and_chart(nvda_day, tmp_path):
    cm = CostModel(load_cost_profile("token_dex"))
    ab = run_ab(nvda_day, all_strategies(), cm, 10_000, "token_dex")
    assert ab.hindsight.net_return >= max(r.net_return for r in ab.executable.values()) - 1e-12
    assert ab.gap_explanation and ab.data_is_simulated
    out = tmp_path / "nvda.html"
    html = render_ab_chart_html(nvda_day, ab, out_path=out)
    assert "SIMULATED" in html and "lightweight-charts" in html and out.exists()
    tbl = ab.summary_table()
    assert len(tbl) == 1 + len(ab.executable)
