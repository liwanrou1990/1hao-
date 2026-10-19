"""阶段四/五：本金分层、容量、严格回测、基准与压力测试。"""
import pandas as pd
import pytest

from bgw_quant.backtest.benchmarks import benchmark_daily
from bgw_quant.backtest.engine import daily_frame, run_daily
from bgw_quant.backtest.metrics import performance_metrics
from bgw_quant.backtest.stress import SCENARIOS, run_stress
from bgw_quant.backtest.walkforward import walk_forward_rule
from bgw_quant.capital.capacity import capacity_table, target_attainment
from bgw_quant.capital.tiers import Tier, load_tiers, validate_tiers
from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.strategies.filters import CostAwareStrategy
from bgw_quant.strategies.rules import make_strategy


def test_tiers_loaded_and_issues_flagged_not_fixed():
    cfg = load_tiers()
    assert len(cfg.tiers) == 7
    joined = " ".join(cfg.issues)
    assert "缺档" in joined and "5,000-19,999" in joined
    assert "重叠" in joined and "50,000-99,999" in joined
    assert "计息口径未知" in joined
    assert len(cfg.find(60_000)) == 2          # 重叠区间命中两档
    assert cfg.find(10_000) == []              # 缺档
    assert cfg.tiers[6].target_daily_pct == (2.0, 2.2) and cfg.tiers[6].period_days == 30


def test_validate_tiers_clean_config_has_only_basis_issues():
    tiers = [Tier("A", 0, 999, (1, 2), 1), Tier("B", 1000, 1999, (1, 2), 1)]
    assert validate_tiers(tiers, "simple", "trading_days") == []


def test_capacity_table_marks_unverifiable_and_feasibility():
    cfg = load_tiers()
    ct = capacity_table(cfg, load_cost_profile("token_dex"), adv_usd=3.6e10)
    assert len(ct) == 21
    big = ct[ct["capital_usd"] >= 1_000_000]
    assert big["unverifiable"].all()
    assert (ct["child_orders"] >= 1).all()
    assert ct["notes"].apply(len).sum() > 0
    assert ct.loc[ct["capital_usd"] == 1000.0, "round_trip_pct"].iloc[0] < ct.loc[ct["capital_usd"] == 5_000_000.0, "round_trip_pct"].iloc[0]


def test_target_attainment_reports_unmet():
    t = Tier("T1", 1000, 4999, (0.8, 0.9), 1)
    dr = pd.Series([0.001, -0.002, 0.009, 0.0], index=pd.to_datetime(["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]).date)
    r = target_attainment(dr, t)
    assert r["attained_days"] == 1 and r["status"] == "未达标" and not r["met"] and "口径未知" in r["note"]
    r2 = target_attainment(pd.Series([0.01, 0.012]), t)
    assert r2["status"] == "达标"


def test_performance_metrics_fields():
    r = pd.Series([0.01, -0.005, 0.002, 0.0, 0.004, -0.001])
    trades = [{"net_pnl_usd": 10}, {"net_pnl_usd": -4}, {"net_pnl_usd": 6}]
    m = performance_metrics(r, trades, capital_usd=10_000, total_cost_usd=50, hindsight_daily=r * 3)
    for k in ["total_net_return", "annualized_return", "sharpe", "sortino", "max_drawdown", "daily_win_rate", "n_trades",
              "trade_win_rate", "payoff_ratio", "profit_factor", "total_cost_usd", "gap_to_hindsight_daily"]:
        assert k in m
    assert m["profit_factor"] == pytest.approx(16 / 4)
    assert m["max_drawdown"] <= 0


def test_run_daily_and_benchmarks(nvda_multi):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    days = sorted(pd.to_datetime(nvda_multi["ts_ny"]).dt.date.unique())[:5]
    sub = nvda_multi[pd.to_datetime(nvda_multi["ts_ny"]).dt.date.isin(days)]
    df = daily_frame(run_daily(sub, make_strategy("rsi"), cm, 10_000))
    assert len(df) == 5 and (df["hindsight_net"] >= df["net_return"] - 1e-12).all()
    b = benchmark_daily(sub, cm, 10_000)
    assert set(b.columns) == {"buy_hold", "no_trade", "simple_rule_ema", "hindsight_optimal"}
    assert (b["no_trade"] == 0).all()


def test_walk_forward_rule_oos_strictly_after_train(nvda_multi):
    cm = CostModel(load_cost_profile("token_dex"))
    wf = walk_forward_rule(nvda_multi, "breakout", cm, 10_000, train_days=15, test_days=5, min_train_days=10)
    assert wf.folds
    for f in wf.folds:
        assert f.train[1] < f.test[0]
        assert set(f.oos_daily.index) <= set(pd.date_range(f.test[0], f.test[1]).date)
    assert len(wf.oos_daily) == sum(len(f.oos_daily) for f in wf.folds)
    vs = wf.in_sample_vs_oos
    assert "in_sample_mean_daily" in vs and "oos_mean_daily" in vs
    with pytest.raises(ValueError):
        walk_forward_rule(nvda_multi, "breakout", cm, 10_000, train_days=1, test_days=1)


def test_cost_aware_filter_reduces_trades_and_is_causal(nvda_day):
    cm = CostModel(load_cost_profile("token_dex"))
    inner = make_strategy("ema_trend")
    wrapped = CostAwareStrategy(inner, cm, 10_000, cooldown_bars=15)
    raw = inner.compute(nvda_day)
    gated = wrapped.compute(nvda_day)
    assert gated.sum() <= raw.sum()
    assert (gated.diff().clip(lower=0).sum()) <= (raw.diff().clip(lower=0).sum())
    from bgw_quant.replay.executable import prefix_consistency_check
    assert prefix_consistency_check(wrapped, nvda_day, [60, 150, 300]) == []


def test_stress_scenarios_run(nvda_multi):
    prof = load_cost_profile("token_dex")
    sub = nvda_multi.iloc[:390 * 3]
    st = run_stress(sub, make_strategy("vwap"), prof, 10_000)
    assert len(st) == len(SCENARIOS)
    base = st.set_index("scenario").loc["基准", "mean_daily_net"]
    assert st.set_index("scenario").loc["成本×2", "mean_daily_net"] <= base + 1e-12
