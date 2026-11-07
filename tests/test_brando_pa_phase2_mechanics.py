"""第二阶段机制测试：限价成交保守模型、退出开关（固定结构止损/结构退出/ATR 跟踪/分批）、止损不放宽、多周期与环境检查、漏斗阶段归类。"""
import pandas as pd
import pytest

from brando_pa.backtest.execution import Position, PositionManager
from brando_pa.config import cfg, deep_update
from brando_pa.diagnostics.funnel import STAGE_OF, cost_to_risk_audit
from brando_pa.engine.signal_engine import mtf_checks, regime_class4


def _sig(direction="long", ref=100.0, stop=98.0, tp=106.0, limit=99.0, struct=98.5, inv=98.5):
    return {"symbol": "BTCUSDT", "strategy": "A_breakout_retest", "direction": direction, "ref_price": ref, "stop_price": stop, "take_profit": tp, "limit_price": limit, "structure_price": struct,
            "invalidation_price": inv, "atr": 1.0, "max_hold_bars": 50, "trail_after_rr": 1.5, "setup_id": "s1", "zone": {"tfs": ["1d"]}, "reasons": []}


def _pend(**kw):
    return {"signal": _sig(**kw), "instrument": "perp", "signal_ts": "2025-01-01", "placed_idx": 1, "expires_idx": 5}


def test_limit_fill_requires_penetration_and_cancels():
    pm = PositionManager(cfg())
    pend = _pend()
    # 仅触及挂单价（low == 99.0）不算成交；穿越 ≥ 0.05 ATR 才成交
    assert pm.limit_fill_check(pend, 100.0, 100.5, 99.0, 100.2, 2) == ("wait", None)
    assert pm.limit_fill_check(pend, 100.0, 100.5, 98.94, 100.2, 2) == ("fill", 99.0)
    # 跳空低开到挂单价下方 → 按开盘价成交（更优）
    assert pm.limit_fill_check(pend, 98.9, 100.5, 98.8, 100.2, 2) == ("fill", 98.9)
    # 先到止盈 → 撤单（机会损失）
    assert pm.limit_fill_check(pend, 100.0, 106.5, 99.5, 105.0, 2)[0] == "cancel:tp_hit_before_fill"
    # 结构失效（收盘低于失效价）→ 撤单
    assert pm.limit_fill_check(pend, 100.0, 100.2, 99.2, 98.4, 2)[0] == "cancel:structure_invalidated"
    # 超时
    assert pm.limit_fill_check(pend, 100.0, 100.5, 99.5, 100.2, 5)[0] == "cancel:timeout"
    # 限价成交：maker 费、无滑点
    p, dcash, canc = pm.fill_entry(pend, 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 2, 10000.0, 900, fill_price=99.0, maker=True)
    assert p is not None and p.entry_price == 99.0 and p.entry_mode == "limit" and abs(dcash) == pytest.approx(99.0 * p.qty * cfg()["costs"]["perp"]["maker_fee_bps"] / 1e4)


def test_exit_switches_fixed_structure_structure_exit_atr_trail_partial():
    c = cfg()
    # 固定结构止损：止损 = structure_price（无缓冲），且不会比策略止损更宽
    pm = PositionManager(deep_update(c, {"exits": {"stop_mode": "fixed_structure"}}))
    p, _, _ = pm.fill_entry(_pend(), 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    assert p.stop == 98.5 and p.init_stop == 98.5
    # 结构退出：收盘跌破失效价 → structure_exit
    pm = PositionManager(deep_update(cfg(), {"exits": {"structure_exit": True}}))
    p, _, _ = pm.fill_entry(_pend(), 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    tr = []
    closed, _ = pm.manage(p, 100.0, 100.3, 98.6, 98.4, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 2, tr)
    assert closed and tr[-1]["exit_reason"] == "structure_exit"
    # ATR 跟踪：浮盈 ≥ 2R 后止损 = 收盘 − 2 ATR，且只向上移动（never widen）
    pm = PositionManager(deep_update(cfg(), {"exits": {"breakeven_after_rr": None, "atr_trail": {"after_rr": 2.0, "atr_mult": 2.0}}}))
    p, _, _ = pm.fill_entry(_pend(tp=120.0), 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    tr = []
    pm.manage(p, 100.0, 105.0, 99.9, 104.8, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 2, tr)
    assert p.trailed and p.stop == pytest.approx(102.8)
    pm.manage(p, 104.8, 104.9, 103.5, 103.6, pd.Timestamp("2025-01-01 02:00", tz="UTC"), 3, tr)
    assert p.stop == pytest.approx(102.8)      # 收盘回落时不放宽止损
    # 分批止盈：达 1R 平一半、余仓保本
    pm = PositionManager(deep_update(cfg(), {"exits": {"partial_tp": {"at_rr": 1.0, "fraction": 0.5}, "breakeven_after_rr": None}}))
    p, _, _ = pm.fill_entry(_pend(tp=120.0), 100.0, pd.Timestamp("2025-01-01", tz="UTC"), 1, 10000.0, 900)
    q0 = p.qty; tr = []
    closed, cash = pm.manage(p, 100.0, 102.5, 99.9, 102.3, pd.Timestamp("2025-01-01 01:00", tz="UTC"), 2, tr)
    assert not closed and p.partial_done and p.qty == pytest.approx(q0 * 0.5) and cash > 0 and p.stop > 100.0
    closed, _ = pm.manage(p, 102.3, 102.4, 99.0, 99.5, pd.Timestamp("2025-01-01 02:00", tz="UTC"), 3, tr)
    assert closed and tr[-1]["qty"] == pytest.approx(q0) and tr[-1]["partial_gross"] > 0 and tr[-1]["net_pnl"] == pytest.approx(tr[-1]["gross_pnl"] - tr[-1]["fees"] - tr[-1]["funding"])


def test_mtf_and_regime_checks():
    from brando_pa.strategies.base import BarContext, Signal
    ctx = BarContext("BTCUSDT", 5, pd.Timestamp("2025-01-01", tz="UTC"), 100, 101, 99, 100.5, 100, 10, 1.0, 1.0, 0.01, [], {}, "down", {"trend": "down", "highvol": False}, [])
    sig = Signal("BTCUSDT", "A_breakout_retest", "long", 100.5, 98.0, 106.0, {"tfs": ["4h"]}, [], "", "x", 5, ctx.ts, 1.0)
    assert mtf_checks(sig, ctx, {"mode": "single"}) == []
    names = {c["name"]: c["status"] for c in mtf_checks(sig, ctx, {"mode": "zone_limit", "aligned_require_4h": True, "aligned_require_1d": True, "zone_limit_require_htf_zone": True})}
    assert names == {"mtf_4h_trend": "FAIL", "mtf_1d_regime": "FAIL", "mtf_zone_htf": "FAIL"}
    assert regime_class4({"trend": "strong_up", "highvol": False}) == "上涨趋势" and regime_class4({"trend": "range", "highvol": True}) == "高波动异常"
    assert STAGE_OF["mtf_4h_trend"] == "trend" and STAGE_OF["cost_to_risk"] == "cost" and STAGE_OF["net_rr"] == "rr"


def test_cost_to_risk_semantics_use_initial_stop():
    a = cost_to_risk_audit(cfg())
    ex = a["example"]
    assert ex["ratio"] == pytest.approx(ex["cost_per_unit"] / ex["stop_distance"])
    assert ex["components"]["entry_cost"] + ex["components"]["exit_cost_at_stop"] + ex["components"]["funding_est"] == pytest.approx(ex["cost_per_unit"])
    assert "初始" in a["denominator"]


def test_oos_metrics_and_evaluator():
    import numpy as np
    from brando_pa.backtest.oos_metrics import block_bootstrap_ci, cost_sensitivity, full_oos_report
    from brando_pa.evaluation.evaluator import evaluate_one
    rng = np.random.default_rng(0)
    n = 80
    tr = pd.DataFrame({"net_pnl": rng.normal(5, 40, n), "risk_usd": 50.0, "gross_pnl": 0.0, "fees": 3.0, "funding": 0.5, "slippage_cost": 1.0,
                       "exit_ts": pd.date_range("2025-01-01", periods=n, freq="12h", tz="UTC"), "entry_ts": pd.date_range("2025-01-01", periods=n, freq="12h", tz="UTC"),
                       "direction": ["long", "short"] * (n // 2), "symbol": "BTCUSDT", "regime4": "横盘震荡", "exit_reason": "stop", "bars_held": 3})
    tr["gross_pnl"] = tr["net_pnl"] + tr["fees"] + tr["funding"]; tr["r_multiple"] = tr["net_pnl"] / tr["risk_usd"]; tr["year"] = 2025
    rep = full_oos_report(tr, 10000.0, [{"n_trades": 40, "expectancy_r": 0.1}, {"n_trades": 40, "expectancy_r": -0.1}])
    ci = rep["bootstrap_mean_r_95ci"]
    assert ci["lower"] < rep["expectancy_r"] < ci["upper"] and rep["fold_nonneg_share"] == 0.5
    cs = cost_sensitivity(tr)
    assert cs["fees_x2"]["net_pnl"] < cs["base"]["net_pnl"] < cs["slip_x0"]["net_pnl"]
    assert abs(cs["base"]["net_pnl"] - tr["net_pnl"].sum()) < 1e-6
    ev = cfg()["evaluator"]
    r = evaluate_one(rep, ev, "1h", "x")
    assert r["status"] in ("qualified_paper", "research_only", "unqualified") and "checks" in r
    # 4h 周期政策：即使门槛全过也不能进模拟盘
    good = {"n_trades": 100, "expectancy_r": 0.3, "profit_factor": 1.8, "max_drawdown": -0.05, "bootstrap_mean_r_95ci": {"lower": 0.1, "upper": 0.5}, "fold_nonneg_share": 0.9}
    assert evaluate_one(good, ev, "4h", "x")["status"] == "research_only" and evaluate_one(good, ev, "1h", "x")["status"] == "qualified_paper"


def test_walkforward_windows_have_embargo_and_order():
    from brando_pa.backtest.walkforward import month_windows, override_from_params, set_path, get_path
    wins = month_windows(pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2025-12-31", tz="UTC"), 12, 6, 6, embargo_days=7)
    assert wins and all(tr[1] < va[0] and va[1] < te[0] and (va[0] - tr[1]).days == 7 for tr, va, te in wins)
    o = override_from_params({"strategies.A_breakout_retest.sl_buffer_atr": 0.3, "exits.breakeven_after_rr": None})
    assert o["strategies"]["A_breakout_retest"]["sl_buffer_atr"] == 0.3 and o["exits"]["breakeven_after_rr"] is None
    d = {}; set_path(d, "a.b.c", 1); assert get_path(d, "a.b.c") == 1 and get_path(d, "a.x", "dflt") == "dflt"
