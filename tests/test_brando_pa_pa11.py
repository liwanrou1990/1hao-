"""第十一阶段：结构确认滞后（无未来函数）、区域只用过去、引擎在截断数据上决策一致、买卖双边扣费与恒等式、T1/失效价入场即定且不变、风险/配置上限与非负现金、
宏观过滤阻止新开仓、买入持有基准、前瞻账户文件不被触碰、Phase 10 复核只读。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.pa11.engine11 import Config, MarketContext, PriceActionBacktester
from brando_pa.pa11.macro import MacroEventFilter, build_events
from brando_pa.pa11.structure import DailyStructureDetector, WeeklyTrendDetector, swings, structure_state
from brando_pa.pa11.zones import SupportResistanceZones
from tests.test_brando_pa_holding import _synth

REAL = Path("reports_out/brando_pa/phase8_forward")


def _ctx_run(bars, cfg, start, end, cost=(20.0, 5.0), ctx=None, events=None):
    return PriceActionBacktester(bars, cfg, cost[0], cost[1], start, end, events=events, ctx=ctx).run()


def test_swings_are_confirmed_with_lag_and_structure_uses_only_confirmed():
    bars = _synth(n=400, seed=5); d = bars["BTCUSDT"]; sw = swings(d, 3)
    assert len(sw) and (sw["confirmed_at"] == sw["idx"] + 3).all()
    for i in (50, 120, 300):
        st = structure_state(sw, i); vis = sw[sw.confirmed_at <= i]
        assert (st["last_high_idx"] is None) or st["last_high_idx"] <= i - 3        # 第 i 日看不到最近 3 根内形成的摆动点
        assert (st["last_low_idx"] is None) or st["last_low_idx"] in set(vis.idx)
    # 篡改未来价格不改变第 i 日之前的结构与区域
    b2 = d.copy(); b2.loc[b2.index >= 200, ["open", "high", "low", "close"]] *= 2.0
    sw2 = swings(b2, 3); i = 190
    pd.testing.assert_frame_equal(sw[sw.confirmed_at <= i].reset_index(drop=True), sw2[sw2.confirmed_at <= i].reset_index(drop=True))
    dd1 = DailyStructureDetector(d); dd2 = DailyStructureDetector(b2)
    z1 = SupportResistanceZones(dd1.sw, None, None, dd1.atr).zones_at(i, dd1.close[i]); z2 = SupportResistanceZones(dd2.sw, None, None, dd2.atr).zones_at(i, dd2.close[i])
    assert [(z.low, z.high, z.strength) for z in z1] == [(z.low, z.high, z.strength) for z in z2]
    w1 = WeeklyTrendDetector(d).at(i); w2 = WeeklyTrendDetector(b2).at(i); assert w1["trend"] == w2["trend"]


def test_engine_no_lookahead_on_truncated_data_and_identity():
    bars = _synth(n=1200, seed=21, trend=0.002, vol=0.03); end = str(bars["BTCUSDT"]["ts"].iloc[899].date())
    cfg = Config(strategies=("A", "B"), exit_mode="B", name="t")
    r1 = _ctx_run(bars, cfg, "2020-08-01", end)
    cut = {s: b.iloc[:900].copy() for s, b in bars.items()}; r2 = _ctx_run(cut, cfg, "2020-08-01", end)
    pd.testing.assert_frame_equal(r1.nav[["ts", "equity", "cash"]].reset_index(drop=True), r2.nav[["ts", "equity", "cash"]].reset_index(drop=True))
    assert r1.final["identity_ok"] and r1.final["liquidation_usdt"] <= r1.final["equity"] + 1e-9
    assert (r1.nav["cash"] >= -1e-9).all() and (r1.nav["exposure"] <= 1.0 + 1e-9).all()


def test_plans_fixed_at_entry_costs_both_sides_and_caps():
    bars = _synth(n=1500, seed=42, trend=0.002, vol=0.03); end = str(bars["BTCUSDT"]["ts"].iloc[-1].date())
    ctx = MarketContext(bars); got = False
    for seed_cfg in (Config(strategies=("A", "B"), exit_mode="B", max_alloc=0.5, risk_pct=0.01, name="t"), Config(strategies=("A", "B", "C"), exit_mode="A", max_alloc=0.7, risk_pct=0.005, name="t2")):
        r = _ctx_run(bars, seed_cfg, "2020-08-01", end, ctx=ctx)
        if len(r.trades):
            got = True; t = r.trades
            assert (t["planned_t1"] > t["entry_price"]).all() and (t["planned_invalidation"] < t["entry_price"]).all()        # 入场时 T1 在上、失效在下
            assert (t["planned_rr"] >= 1.5 - 1e-9).all()
            f = r.fills; buys = f[f.side == "BUY"]; sells = f[f.side == "SELL"]
            assert (buys["fee"] > 0).all() and (sells["fee"] > 0).all() and np.allclose(buys["fill_price"], buys["ref_price"] * 1.0005) and np.allclose(sells["fill_price"], sells["ref_price"] * 0.9995)
            assert (r.nav["exposure"] <= seed_cfg.max_alloc + 0.05).all()                                                         # 配置上限（允许日内涨幅）
            acc = r.signals[r.signals.accepted]; assert (acc["position_size_pct"] <= seed_cfg.max_alloc + 1e-9).all() and (acc["planned_risk_usdt"] <= seed_cfg.risk_pct * 1.5 * acc["position_size_usdt"] / acc["position_size_pct"] + 1e-6).all()
    assert got, "合成数据未产生交易，无法验证（调整 seed）"
    r0 = _ctx_run(bars, Config(strategies=("A", "B"), exit_mode="B", name="t0"), "2020-08-01", end, cost=(0.0, 0.0), ctx=ctx)
    r25 = _ctx_run(bars, Config(strategies=("A", "B"), exit_mode="B", name="t25"), "2020-08-01", end, cost=(20.0, 5.0), ctx=ctx)
    assert r0.final["fees_usdt"] == 0 and r25.final["total_cost_usdt" if "total_cost_usdt" in r25.final else "fees_usdt"] >= 0


def test_macro_filter_blocks_new_entries_on_event_days():
    ev = build_events("2020-01-01", "2020-12-31"); f = MacroEventFilter(ev, "pre_event"); g = MacroEventFilter(ev, "post_event"); n = MacroEventFilter(ev, "none")
    d = pd.Timestamp("2020-06-10", tz="UTC")                                                        # FOMC
    assert not f.allows_new_entry(d)[0] and not f.allows_new_entry(d - pd.Timedelta(days=1))[0] and f.allows_new_entry(d + pd.Timedelta(days=1))[0]
    assert not g.allows_new_entry(d)[0] and g.allows_new_entry(d - pd.Timedelta(days=1))[0] and n.allows_new_entry(d)[0]
    assert set(ev["event"]) == {"FOMC", "CPI", "NFP"} and ev[ev.event == "FOMC"]["approx"].eq(False).all() and ev[ev.event != "FOMC"]["approx"].eq(True).all()
    bars = _synth(n=1500, seed=42, trend=0.002, vol=0.03); end = str(bars["BTCUSDT"]["ts"].iloc[-1].date()); ctx = MarketContext(bars); evs = build_events("2020-01-01", "2024-12-31")
    # 把全部日期设为事件 → 任何新开仓都被阻止
    all_days = pd.DataFrame({"date": pd.date_range("2020-01-01", "2024-12-31", tz="UTC"), "event": "FOMC", "approx": False})
    r = _ctx_run(bars, Config(strategies=("A", "B"), exit_mode="B", macro_mode="post_event", name="m"), "2020-08-01", end, ctx=ctx, events=all_days)
    assert len(r.fills) == 0 and (r.decisions["action"] != "BUY").all()


def test_dry_run_pipeline_and_forward_accounts_untouched(tmp_path):
    from brando_pa.holding.data import phase10_data_dir
    if not (phase10_data_dir() / "BTCUSDT_1d.parquet").exists():
        pytest.skip("dataset not built")
    from brando_pa.pa11.competition import run_phase11
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for a in REAL.iterdir() if a.is_dir() for p in a.iterdir() if p.is_file()} if REAL.exists() else {}
    res = run_phase11(out=tmp_path, dry_run=True)
    assert res["dry_run"] and (tmp_path / "phase11_dry_run.json").exists() and (tmp_path / "summary_dry.md").exists()
    rows = pd.DataFrame(res["rows"]); assert {"V0_BUYHOLD_BTC", "V2_trend_pullback", "V3_trend_breakout_retest", "V4_pa_combined"} <= set(rows.group)
    for _, r in rows.iterrows():
        assert r["liquidation_usdt"] <= r["final_equity"] + 1e-9
    assert res["cost_note"].startswith("全部为单侧")
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for a in REAL.iterdir() if a.is_dir() for p in a.iterdir() if p.is_file()} if REAL.exists() else {}
    assert before == after


def test_phase10_audit_is_read_only_and_recomputes(tmp_path):
    p10 = Path("docs/brando_pa_validation/phase10_holding")
    if not (p10 / "phase10_results.json").exists():
        pytest.skip("no phase10 results")
    from brando_pa.pa11.audit_phase10 import audit_phase10
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in p10.iterdir() if p.is_file()}
    a = audit_phase10(tmp_path)
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in p10.iterdir() if p.is_file()} == before
    assert a["data"]["BTCUSDT"]["bars_equal_span"] and a["cost_convention"]["verified_from_fills"] and a["lookahead_check"]["identical"]
    assert a["strategies"]["S1_BTC_SMA200"]["top2_share_of_closed_pnl"] > 0.75
