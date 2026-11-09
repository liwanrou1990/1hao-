"""第十阶段：日线持仓回测引擎正确性（无未来函数、买卖双边扣费、固定/最短持有、风险退出、买入持有一致性、盈亏恒等式）、数据验证、流程 dry-run、只读保护。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.holding.data import validate_daily
from brando_pa.holding.engine import HoldingBacktester
from brando_pa.holding.metrics import drawdown_stats, summarize
from brando_pa.holding.phase10 import COSTS, STRATEGIES

REAL = Path("reports_out/brando_pa/phase8_forward")


def _synth(n=900, seed=7, trend=0.0015, vol=0.03, start="2020-01-01"):
    rng = np.random.default_rng(seed); ts = pd.date_range(start, periods=n, freq="D", tz="UTC")
    out = {}
    for k, s in enumerate(["BTCUSDT", "ETHUSDT"]):
        r = rng.normal(trend, vol, n); c = 100.0 * (1 + k) * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
        out[s] = pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1.0, "source": "synth"})
    return out


def _run(bars, spec, cost=25, start="2021-01-01", end=None):
    fee, slip = COSTS[cost]
    return HoldingBacktester(bars, spec, fee, slip, start, end or str(bars["BTCUSDT"]["ts"].iloc[-1].date()), sma=200).run()


def test_no_lookahead_future_prices_do_not_change_past_decisions():
    bars = _synth(); spec = {"name": "t", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": 0.2}
    r1 = _run(bars, spec, end="2021-12-31")
    b2 = {s: d.copy() for s, d in bars.items()}; m = b2["BTCUSDT"]["ts"] > pd.Timestamp("2021-12-31", tz="UTC")
    for col in ("open", "high", "low", "close"):
        b2["BTCUSDT"].loc[m, col] *= 3.0                                     # 篡改 2022 年以后的价格
    r2 = _run(b2, spec, end="2021-12-31")
    pd.testing.assert_frame_equal(r1.nav[["ts", "equity", "cash"]], r2.nav[["ts", "equity", "cash"]])
    assert r1.fills.equals(r2.fills)
    # 执行价 = 决策日的下一交易日开盘
    for _, f in r1.fills.iterrows():
        i = int(np.searchsorted(pd.DatetimeIndex(bars["BTCUSDT"]["ts"]), f["ts"])); assert bars["BTCUSDT"]["open"].iloc[i] == pytest.approx(f["ref_price"])


def test_costs_charged_on_both_sides_and_identity():
    bars = _synth(); spec = {"name": "t", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": 0.2}
    r0 = _run(bars, spec, cost=0); r25 = _run(bars, spec, cost=25)
    assert r0.final["fees_usdt"] == 0 and r0.final["slippage_usdt"] == 0 and r25.final["fees_usdt"] > 0 and r25.final["slippage_usdt"] > 0
    f = r25.fills; buys = f[f["side"] == "BUY"]; sells = f[f["side"] == "SELL"]
    assert len(sells) > 0 and (buys["fee"] > 0).all() and (sells["fee"] > 0).all()
    assert np.allclose(buys["fill_price"], buys["ref_price"] * 1.0005) and np.allclose(sells["fill_price"], sells["ref_price"] * 0.9995)
    assert np.allclose(buys["fee"], buys["fill_price"] * buys["qty"] * 0.002) and np.allclose(sells["fee"], sells["fill_price"] * sells["qty"] * 0.002)
    for r in (r0, r25):
        assert r.final["identity_ok"]                                           # 权益 − 初始 = 已实现净额 + 未实现
        assert r.final["liquidation_usdt"] <= r.final["equity"] + 1e-9
    assert r25.final["liquidation_net_profit_usdt"] < r0.final["liquidation_net_profit_usdt"]      # 成本只会降低结果


def test_fixed_hold_exits_at_n_days_and_minhold_ignores_trend_off():
    bars = _synth(); fixed = _run(bars, {"name": "f", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "fixed", "min_hold": 30, "max_hold": 30, "risk_exit_dd": 0.2})
    t = fixed.trades[~fixed.trades["partial"]]
    assert len(t) > 0 and (t[t["reason"] == "max_hold"]["hold_days"] == 30).all() and set(t["reason"]) <= {"max_hold", "risk_exit", "trend_off", "rebalance_down"}
    mh = _run(bars, {"name": "m", "assets": ["BTCUSDT"], "mode": "minhold", "min_hold": 90, "max_hold": None, "risk_exit_dd": None})
    t2 = mh.trades[~mh.trades["partial"]]
    assert (t2[t2["reason"] == "trend_off"]["hold_days"] >= 90).all()        # 90 日内不因 trend_off 退出
    plain = _run(bars, {"name": "p", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None})
    assert len(plain.fills) >= len(mh.fills)                                   # 最短持有不会增加交易次数


def test_risk_exit_triggers_on_20pct_loss():
    bars = _synth(seed=3, trend=-0.004, vol=0.04)
    r = _run(bars, {"name": "r", "assets": ["BTCUSDT"], "mode": "minhold", "min_hold": 10_000, "max_hold": None, "risk_exit_dd": 0.2})
    t = r.trades[~r.trades["partial"]]
    if len(t):
        assert set(t["reason"]) <= {"risk_exit"}
        for _, x in t.iterrows():                                              # 触发日收盘 ≤ 入场价 × 0.8（次日开盘退出）
            d = bars["BTCUSDT"].set_index("ts"); prev = d.loc[:x["exit_ts"]].iloc[-2]["close"]; assert prev <= x["entry_price"] * 0.8 + 1e-9


def test_buy_and_hold_matches_direct_computation():
    bars = _synth(); r = _run(bars, {"name": "bh", "assets": ["BTCUSDT"], "mode": "buyhold"}, cost=25, start="2021-01-01")
    assert len(r.fills) == 1 and r.fills.iloc[0]["side"] == "BUY"
    d = bars["BTCUSDT"].set_index("ts"); i0 = d.index.get_loc(pd.Timestamp("2021-01-01", tz="UTC"))
    open1 = d["open"].iloc[i0 + 1]; q = (10000 * 0.999) / (open1 * 1.0005 * 1.002); last = d["close"].iloc[-1]
    assert r.final["holdings"]["BTCUSDT"] == pytest.approx(q) and r.final["equity"] == pytest.approx(10.0 + q * last, rel=1e-9)
    assert r.final["liquidation_usdt"] == pytest.approx(10.0 + q * last * 0.9995 * 0.998, rel=1e-9)


def test_dynamic_allocation_rebalances_to_equal_weight_on_set_change():
    bars = _synth(seed=11); r = _run(bars, {"name": "dyn", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, cost=10)
    nav = r.nav
    both = nav[(nav["holdings"].apply(lambda h: h["BTCUSDT"] > 0 and h["ETHUSDT"] > 0))]
    if len(both):
        first = both.iloc[0]; v = {s: first["holdings"][s] * first["closes"][s] for s in ("BTCUSDT", "ETHUSDT")}
        assert abs(v["BTCUSDT"] - v["ETHUSDT"]) / first["equity"] < 0.08       # 同时持有时接近等权（允许当日价格变动）
    assert (nav["exposure"] <= 1.0 + 1e-9).all() and (nav["cash"] >= -1e-9).all()


def test_metrics_and_validation():
    eq = pd.Series([100, 110, 99, 105, 120, 90, 95, 130.0], index=pd.date_range("2024-01-01", periods=8, freq="D", tz="UTC"))
    dd = drawdown_stats(eq); assert dd["max_drawdown"] == pytest.approx(90 / 120 - 1) and dd["longest_drawdown_days"] == 2
    bars = _synth(n=300); df = bars["BTCUSDT"]; v = validate_daily(df); assert v["ok"] and v["n_gaps"] == 0
    bad = pd.concat([df.iloc[:100], df.iloc[103:]]); assert validate_daily(bad)["n_gaps"] == 1
    r = _run(bars, {"name": "t", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": 0.2}, start="2020-08-01")
    sm = summarize(r, bars["BTCUSDT"].set_index("ts")["close"]); assert set(sm["regimes"]) == {"bull", "bear", "sideways"} and "annual_returns" in sm and sm["one_side_cost_bps"] == 25


def test_strategy_set_is_preregistered():
    names = [s["name"] for s in STRATEGIES]
    for n in ("S1_BTC_SMA200", "S2_ETH_SMA200", "S3_DYN_BTC_ETH", "S4_HOLD30", "S5_HOLD60", "S6_HOLD90", "S7_HOLD90_EXTEND", "S8a_BUYHOLD_BTC", "S8b_BUYHOLD_ETH", "S8c_BUYHOLD_5050", "B1_BTC_SMA200_daily_repro", "B2_modelB_state_machine_daily_repro", "B3_ETH_SMA200_daily_repro"):
        assert n in names
    assert COSTS == {0: (0.0, 0.0), 10: (5.0, 5.0), 15: (10.0, 5.0), 25: (20.0, 5.0)}
    for s in STRATEGIES:
        if s["mode"] in ("fixed", "minhold"):
            assert s.get("risk_exit_dd") == 0.2


def test_dry_run_pipeline_writes_outputs_and_does_not_touch_forward_accounts(tmp_path):
    import hashlib
    from brando_pa.holding.data import phase10_data_dir
    if not (phase10_data_dir() / "BTCUSDT_1d.parquet").exists():
        pytest.skip("phase10 dataset not built")
    from brando_pa.holding.phase10 import run_phase10
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for a in REAL.iterdir() if a.is_dir() for p in a.iterdir() if p.is_file()} if REAL.exists() else {}
    res = run_phase10(out_dir=tmp_path, dry_run=True)
    assert res["dry_run"] and (tmp_path / "phase10_dry_run.json").exists() and (tmp_path / "summary_dry.md").exists() and len(res["rows"]) >= 4
    for r in res["rows"]:
        assert r["liquidation_usdt"] <= r["final_equity"] + 1e-9 and abs(r["equity_profit_usdt"] - (r["realized_net_usdt"] + r["unrealized_usdt"])) < 1e-6 * max(1.0, abs(r["final_equity"]))
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for a in REAL.iterdir() if a.is_dir() for p in a.iterdir() if p.is_file()} if REAL.exists() else {}
    assert before == after
