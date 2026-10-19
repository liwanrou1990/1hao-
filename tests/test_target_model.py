"""固定收益目标·多次交易模型：教学路径约束、无未来信息策略因果性、滚动样本外结构、目标取值规则。"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bgw_quant.calendar_utils import trading_days
from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.target_model.config import load_target_config, target_for_capital
from bgw_quant.target_model.execution import bar_liquidity, batch_fill
from bgw_quant.target_model.strategy import TargetSeekingStrategy, run_strategy_day
from bgw_quant.target_model.teaching import TEACHING_DISCLAIMER, teaching_path
from bgw_quant.target_model.walkforward import walk_forward_target
from tests.conftest import SAMPLE_DAY


@pytest.fixture(scope="module")
def tok():
    return apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", SAMPLE_DAY, venue="token"))


@pytest.fixture(scope="module")
def cfg():
    return load_target_config()


def test_target_for_capital_rules():
    t, tid, note = target_for_capital(60_000)
    assert tid == "T2" and t == pytest.approx(0.009) and "同时命中" in note      # 重叠区间取编号最小
    assert target_for_capital(60_000, mode="mid")[0] == pytest.approx(0.0095)
    assert target_for_capital(100_000)[1] == "T3" and target_for_capital(100_000)[0] == pytest.approx(0.01)
    assert target_for_capital(10_000)[0] is None                                 # 缺档
    assert target_for_capital(5_000_000)[1] == "T7"


def test_batch_fill_respects_per_bar_liquidity(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    liq = bar_liquidity(tok, 0.1)
    f = batch_fill(tok, 50, "buy", 500_000, cm, liq, 5, "close")
    assert 1 <= len(f.fills) <= 5
    for c in f.fills:
        assert c.notional_usd <= liq[c.idx] + 1e-6
    assert f.partial and f.filled_usd < 500_000
    assert f.avg_exec > tok["close"].iloc[50] * 0.99
    s = batch_fill(tok, 60, "sell", f.qty * tok["close"].iloc[60], cm, liq, 10, "close", qty_to_sell=f.qty)
    assert s.qty <= f.qty + 1e-9 and (s.qty == pytest.approx(f.qty, rel=1e-6) or s.partial)
    big = batch_fill(tok, 60, "sell", f.qty * tok["close"].iloc[60], cm, liq, 60, "close", qty_to_sell=f.qty)
    assert big.qty == pytest.approx(f.qty, rel=1e-6)


def test_teaching_path_constraints(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    r = teaching_path(tok, cm, 100_000, 0.009, cfg, cost_profile="x")
    assert r.disclaimer == TEACHING_DISCLAIMER and r.mode == "teaching_hindsight"
    liq = bar_liquidity(tok, cfg.participation_rate)
    for t in r.trades:
        assert t.size_usd <= 100_000 * cfg.position_fraction + 1e-6            # 不加杠杆、不超仓位上限
        assert t.net_ret >= cfg.take_profit_net_pct / 100 - 1e-9              # 每笔净收益 ≥ 单笔目标（扣费后）
        assert t.mae_pct >= -cfg.stop_loss_pct - 1e-9                          # 持有期内不利波动 ≤ 止损
        assert t.hold_bars <= cfg.max_hold_bars + cfg.batch_bars
        for c in t.entry.fills + t.exit.fills:
            assert c.notional_usd <= liq[c.idx] + 1e-6                        # 逐根流动性
        assert t.net_pnl_usd == pytest.approx(t.exit.filled_usd - t.entry.filled_usd - cm.p.gas_usd * (len(t.entry.fills) + len(t.exit.fills)), rel=1e-6)
    for a, b in zip(r.trades, r.trades[1:]):
        assert a.exit.end_idx < b.entry.start_idx                               # 同一时刻只有一个持仓
    assert r.realized_net_usd == pytest.approx(sum(t.net_pnl_usd for t in r.trades))
    assert len(r.cum_curve) == len(tok) and r.cum_curve[-1] == pytest.approx(r.realized_net_usd)
    if r.target_met:
        assert r.target_hit_ts is not None
        # 达标后不再新增交易：最后一笔完成即达标
        assert r.trades[-1].cum_net_pnl_usd >= r.target_usd - 1e-9 and r.trades[-2].cum_net_pnl_usd < r.target_usd if len(r.trades) > 1 else True
    else:
        assert r.reasons_not_met


def test_teaching_unmet_target_is_reported_not_fabricated(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex"))
    r = teaching_path(tok, cm, 10_000_000, 0.02, cfg)
    assert not r.target_met and r.realized_net_usd < r.target_usd and r.reasons_not_met
    assert r.realized_net_return < 0.02


def test_strategy_is_causal_prefix_consistent(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    strat = TargetSeekingStrategy(z_entry=1.0, take_profit_net_pct=0.3, stop_loss_pct=0.8)
    full = run_strategy_day(tok, cm, 100_000, 0.009, cfg, strat)
    assert full.mode == "strategy_no_lookahead"
    # 把未来K线替换成另一天的走势，截断点之前的交易不得改变
    other = apply_quality_flags(SimulatedSource().fetch_minute_bars("NVDA", date(2026, 10, 15), venue="token")).reset_index(drop=True)
    k = 200
    m = min(len(tok), len(other))
    tail = other.iloc[k:m].copy()
    tail["ts_ny"] = tok["ts_ny"].iloc[k:m].to_numpy()
    tail["ts"] = tok["ts"].iloc[k:m].to_numpy()
    mixed = pd.concat([tok.iloc[:k], tail], ignore_index=True)
    alt = run_strategy_day(mixed, cm, 100_000, 0.009, cfg, strat)
    done_full = [t for t in full.trades if t.exit.end_idx < k - cfg.batch_bars]
    done_alt = [t for t in alt.trades if t.exit.end_idx < k - cfg.batch_bars]
    assert [(t.entry.start_idx, t.exit.end_idx, round(t.net_pnl_usd, 6)) for t in done_full] == \
           [(t.entry.start_idx, t.exit.end_idx, round(t.net_pnl_usd, 6)) for t in done_alt]
    for t in full.trades:
        assert t.entry.start_idx >= 1 and t.size_usd <= 100_000 * strat.position_fraction + 1e-6


def test_strategy_daily_risk_controls(tok, cfg):
    cm = CostModel(load_cost_profile("token_dex"))
    c2 = cfg.with_overrides(max_daily_loss_pct=0.5, max_trades_per_day=3)
    r = run_strategy_day(tok, cm, 100_000, 0.009, c2, TargetSeekingStrategy(z_entry=0.5, stop_loss_pct=0.3, take_profit_net_pct=5.0))
    assert len(r.trades) <= 3
    if r.realized_net_usd <= -500:
        assert r.stopped_reason and "亏损" in r.stopped_reason


def test_walk_forward_target_structure(cfg):
    src = SimulatedSource()
    days = trading_days(date(2026, 8, 24), SAMPLE_DAY)
    bars = pd.concat([apply_quality_flags(src.fetch_minute_bars("NVDA", d, venue="token")) for d in days], ignore_index=True)
    cm = CostModel(load_cost_profile("token_dex_optimistic"))
    small = cfg.with_overrides(strategy_grid={"z_entry": [1.5, 2.0], "z_window": [30], "take_profit_net_pct": [0.3], "stop_loss_pct": [0.8], "position_fraction": [0.5]})
    wf = walk_forward_target(bars, cm, 100_000, 0.009, small, train_days=12, test_days=4)
    assert wf.folds and wf.oos_stats.n_days == len(wf.oos_days)
    for f in wf.folds:
        assert f.train[1] < f.test[0]
        assert all(f.train[1] < date.fromisoformat(d.day) for d in f.test_days)
    assert 0 <= wf.oos_stats.attainment_rate <= 1 and 0 <= wf.oos_stats.losing_day_rate <= 1
    assert set(wf.oos_frame().columns) >= {"day", "net_usd", "target_met", "n_trades"}
    with pytest.raises(ValueError):
        walk_forward_target(bars, cm, 100_000, 0.009, small, train_days=2, test_days=1)
