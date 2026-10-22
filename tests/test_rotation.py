"""跨市场轮动：因果一致性（前缀不变）、能力约束（无做空、不可交易资产不交易）、资产对齐、空仓允许、
A ≥ B、A/B 使用未来信息的审计（改动未来 K 线 → A/B 变化，而 C 的前缀决策不变）。"""
import json
import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import load_cost_profile
from bgw_quant.data.quality import apply_quality_flags
from bgw_quant.data.simulated import SimulatedSource
from bgw_quant.decision.capability import capability_for
from bgw_quant.decision.engine import load_engine_config
from bgw_quant.rotation.engine import RotParams, run_rotation_day
from bgw_quant.rotation.hindsight import universe_ab, universe_theoretical
from bgw_quant.rotation.strategies import build_specs
from bgw_quant.rotation.universe import GRID_MINUTES, make_asset_day
from tests.conftest import SAMPLE_DAY

CFG = load_engine_config()
ROT = {"post_target_edge_mult": 1.5, "post_target_size_mult": 0.5, "post_target_giveback": 0.25, "expected_hold_bars": 20, "pwin_prior_n": 4}


def _asset(sym, bars):
    prof = load_cost_profile("crypto_spot" if sym in ("BTC", "ETH", "SOL", "HYPE") else "token_dex_optimistic")
    return make_asset_day(sym, bars, prof, capability_for(sym), SAMPLE_DAY)


@pytest.fixture(scope="module")
def assets():
    src = SimulatedSource()
    out = []
    for s in ("NVDA", "TSLA"):
        out.append(_asset(s, apply_quality_flags(src.fetch_minute_bars(s, SAMPLE_DAY, venue="token"))))
    for c in ("SOL", "ETH"):
        out.append(_asset(c, apply_quality_flags(src.fetch_crypto_day(c, SAMPLE_DAY))))
    return out


def test_grid_alignment(assets):
    nvda = next(a for a in assets if a.symbol == "NVDA")
    sol = next(a for a in assets if a.symbol == "SOL")
    assert nvda.bars_per_day == 390 and sol.bars_per_day == 1440
    assert nvda.first_grid == 9 * 60 + 30 and nvda.last_grid == 15 * 60 + 59        # 股票只在 09:30–16:00 有格
    assert sol.first_grid == 0 and sol.last_grid == GRID_MINUTES - 1
    assert (nvda.bar_at_grid[: nvda.first_grid] == -1).all()


def test_causal_prefix_consistency(assets):
    """只截断“未来”K 线：前半天的所有成交必须完全一致（引擎不看未来）。"""
    spec = build_specs(["NVDA", "TSLA"], ["SOL", "ETH"])[-2]          # E 跨市场轮动
    full = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    cut = 14 * 60                                                        # 14:00 NY 之前
    trunc = []
    for a in assets:
        keep = a.bars[a.grid_of_bar < cut].reset_index(drop=True)
        trunc.append(make_asset_day(a.symbol, keep, a.cost.p, a.cap, SAMPLE_DAY))
    part = run_rotation_day(trunc, spec, 650_000, 7_800, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    # 比较开仓时刻 < 截断前 1 小时 的成交（避免截断边界的日终平仓差异）
    def key(t):
        return (t.symbol, t.direction, t.open_ts, round(t.size_usd, 2), round(t.avg_open, 6))
    lim = pd.Timestamp(SAMPLE_DAY).strftime("%Y-%m-%d") + " 12:50:00"
    f_keys = sorted(key(t) for t in full.trades if t.open_ts < lim and t.close_ts < lim)
    p_keys = sorted(key(t) for t in part.trades if t.open_ts < lim and t.close_ts < lim)
    assert f_keys == p_keys and len(f_keys) > 0


def test_no_short_and_untradable_assets_never_trade(assets):
    spec = build_specs(["NVDA", "TSLA"], ["SOL", "ETH"])[-2]
    run = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    assert run.n_short == 0 and all(t.direction == "long" for t in run.trades)
    assert all(a.cap.directions == ["long"] for a in assets)
    # SPCX（未核实，tradable=false）：能力无方向 → 不交易
    src = SimulatedSource()
    spcx = _asset("SPCX", apply_quality_flags(src.fetch_minute_bars("SPCX", SAMPLE_DAY, venue="token")))
    assert spcx.cap.directions == []
    spec1 = build_specs(["SPCX"], [])[0]
    r = run_rotation_day([spcx], spec1, 60_000, 540, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    assert r.n_trades == 0 and r.realized_net_usd == 0


def test_constraints_costs_and_flat_allowed(assets):
    spec = build_specs(["NVDA", "TSLA"], ["SOL", "ETH"])[-1]           # F 组合
    run = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(max_open=4), str(SAMPLE_DAY), ROT)
    assert run.peak_deployed_usd <= 650_000 + 1e-6
    assert run.realized_net_usd == pytest.approx(sum(t.net_pnl_usd for t in run.trades))
    assert all(t.trading_cost_usd > 0 for t in run.trades)              # 每笔都扣了成本
    assert 0.0 <= run.flat_ratio <= 1.0
    # 极高成本门槛 → 全部资产不满足条件 → 空仓（允许，不强行交易）
    r0 = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(edge_min_ratio=1e6), str(SAMPLE_DAY), ROT)
    assert r0.n_trades == 0 and r0.flat_ratio == 1.0 and "成本门槛高于预期波动" in r0.pause_reasons


def test_rotation_switch_requires_cover_cost(assets):
    spec = build_specs(["NVDA", "TSLA"], ["SOL", "ETH"], max_assets_rotation=1)[-2]
    run = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    for sw in run.switches:
        assert sw.from_symbol != sw.to_symbol and sw.est_cost_usd >= 0 and "切换成本" in sw.reason
    # 切换成本放大到极大 → 不再发生轮动切换
    r = run_rotation_day(assets, spec, 650_000, 7_800, CFG, RotParams(switch_cost_mult=1e9), str(SAMPLE_DAY), ROT)
    assert r.n_switches == 0


def test_ab_use_future_information_but_c_does_not(assets):
    """审计：A/B 是事后量——改变尾盘 K 线会改变 A/B；C 在被改动时刻之前的决策不变。"""
    spec = build_specs(["NVDA", "TSLA"], ["SOL", "ETH"])[-2]
    ab0 = universe_ab(assets, spec, 60_000, 540, str(SAMPLE_DAY), 0.1)
    assert ab0.theoretical_net_usd >= ab0.executable_net_usd - 1e-6      # A ≥ B
    mod = []
    for a in assets:
        b = a.bars.copy()
        tail = a.grid_of_bar >= 15 * 60                                      # 15:00 之后的 K 线整体抬高 3%
        for c in ("open", "high", "low", "close"):
            b.loc[tail, c] = b.loc[tail, c] * 1.03
        mod.append(make_asset_day(a.symbol, b, a.cost.p, a.cap, SAMPLE_DAY))
    ab1 = universe_ab(mod, spec, 60_000, 540, str(SAMPLE_DAY), 0.1)
    assert ab1.theoretical_net_usd > ab0.theoretical_net_usd                # A 看到了“未来”
    c0 = run_rotation_day(assets, spec, 60_000, 540, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    c1 = run_rotation_day(mod, spec, 60_000, 540, CFG, RotParams(), str(SAMPLE_DAY), ROT)
    lim = pd.Timestamp(SAMPLE_DAY).strftime("%Y-%m-%d") + " 14:30:00"
    k0 = sorted((t.symbol, t.open_ts, round(t.size_usd, 2)) for t in c0.trades if t.close_ts < lim)
    k1 = sorted((t.symbol, t.open_ts, round(t.size_usd, 2)) for t in c1.trades if t.close_ts < lim)
    assert k0 == k1                                                          # C 的早盘决策不受尾盘改动影响
    assert "事后" in ab0.disclaimer and "不是保证收益" in c0.disclaimer


def test_universe_theoretical_switches_assets(assets):
    theo, trades = universe_theoretical(assets, 10_000)
    assert theo > 0 and all(t["buy_grid"] < t["sell_grid"] for t in trades)
    # 不相交
    s = sorted(trades, key=lambda t: t["buy_grid"])
    assert all(x["sell_grid"] < y["buy_grid"] for x, y in zip(s, s[1:]))
    # 全知者的跨资产上限 ≥ 任一单资产上限
    for a in assets:
        t1, _ = universe_theoretical([a], 10_000)
        assert theo >= t1 - 1e-6


def test_backtest_walkforward_small(tmp_path, monkeypatch):
    """小规模端到端：参数只由之前日选择、缓存可复用、排名表字段完整。"""
    import os
    from datetime import date as _date

    monkeypatch.setenv("BGWQ_DB_PATH", str(tmp_path / "bt.duckdb"))
    from bgw_quant.config import get_settings
    get_settings.cache_clear()
    from bgw_quant.pipeline import Pipeline
    from bgw_quant.rotation.backtest import RotationBacktest
    pl = Pipeline(probe=False)
    days = [_date(2026, 10, 13), _date(2026, 10, 14), _date(2026, 10, 15), _date(2026, 10, 16)]
    pl.ingest(["NVDA", "TSLA"], days[0], days[-1], with_token=True)
    pl.ingest_crypto(["SOL"], days[0], days[-1])
    specs = [s for s in pl.rotation_specs(["NVDA", "TSLA"], ["SOL"]) if s.key in ("A:NVDA", "E:cross_rotation")]
    bt = RotationBacktest(pl, end_day=days[-1], test_days=2, train_days=2, specs=specs, cache_path=str(tmp_path / "cache.duckdb"), workers=2)
    bt.tiers = [t for t in bt.tiers if t[0] == "T2"]
    bt.grid = bt.grid[:2]
    info = bt.prepare()
    assert info["n_assets_loaded"] == 12 and bt.test_days == days[2:]
    n1 = bt.run_c()
    assert n1 == 2 * 1 * 4 * 2                      # 策略 × 档位 × 日 × 参数
    assert bt.run_c() == 0                            # 第二次全部命中缓存
    oos = bt.oos_table()
    assert set(oos["day"]) == {str(d) for d in days[2:]} and set(oos["strategy"]) >= {"A:NVDA", "E:cross_rotation"}
    # 参数选择只用测试日之前的训练日：选中的参数必须来自网格
    assert all(p in {json.dumps(g.to_dict(), sort_keys=True) for g in bt.grid} for p in oos["params"])
    rk = bt.ranking(oos)
    assert {"attainment", "mean_net_usd", "losing_day_rate", "multi_day_dd_usd", "score", "rank_in_tier"} <= set(rk.columns)
    assert sorted(rk[rk["tier"] == "T2"]["rank_in_tier"]) == list(range(1, len(rk[rk["tier"] == "T2"]) + 1))
    tr = bt.trades_frame(oos)
    assert tr.empty or {"symbol", "open_ts", "close_ts", "size_usd", "net_pnl_usd", "reason_exit"} <= set(tr.columns)
    get_settings.cache_clear()
