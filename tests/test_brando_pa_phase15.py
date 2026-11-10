"""第十五阶段：基准与第十阶段逐位一致；链上因子施加 1 日发布滞后；无门控时子类引擎 = 父引擎；门控 / 单因子不能绕过；
选参只用训练段（样本外不参与选择）；矿工成本为正且是区间；减半阶段不滞后；运行只写输出目录、不触碰生产与历史数据。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.holding.data import load_dataset
from brando_pa.holding.engine import HoldingBacktester
from brando_pa.holding.metrics import summarize
from brando_pa.holding.phase10 import COSTS
from brando_pa.pa15 import data15, factors15, phase15
from brando_pa.pa15.engine15 import FactorBacktester

ROOT = Path(__file__).resolve().parents[1]
P10 = ROOT / "docs" / "brando_pa_validation" / "phase10_holding" / "phase10_results.json"
pytestmark = pytest.mark.skipif(not (data15.RAW / "coinmetrics_btc_1d.csv").exists(), reason="phase15 raw data snapshot missing")


@pytest.fixture(scope="module")
def ctx():
    c = cfg(); bars = load_dataset(c); F = factors15.all_factors(bars); S = phase15.build_strategies(bars, F)
    return {"c": c, "bars": bars, "F": F, "S": {s["name"]: s for s in S}, "latest": str(bars["BTCUSDT"]["ts"].iloc[-1].date())}


def test_baselines_match_phase10_exactly(ctx):
    if not P10.exists():
        pytest.skip("phase10 results missing")
    ref = {(r["name"], r["window"]): r for r in json.loads(P10.read_text())["rows"] if r["cost_bps"] == 15}
    pairs = {"B1_BTC_SMA200": "B1_BTC_SMA200_daily_repro", "B3_ETH_SMA200": "B3_ETH_SMA200_daily_repro", "B2_modelB_state_machine": "B2_modelB_state_machine_daily_repro", "BH_BTC": "S8a_BUYHOLD_BTC", "BH_ETH": "S8b_BUYHOLD_ETH", "BH_5050": "S8c_BUYHOLD_5050"}
    end = json.loads(P10.read_text())["latest_complete_day"]
    for mine, theirs in pairs.items():
        for w, (a, b) in {"full": ("2018-01-01", end), "oos": ("2024-01-01", end)}.items():
            s = summarize(phase15.run_one(ctx["bars"], ctx["S"][mine], 15, a, b, ctx["c"])); r = ref[(theirs, w)]
            assert s["liquidation_net_profit_usdt"] == pytest.approx(r["liquidation_net_profit_usdt"], abs=1e-6), (mine, w)
            assert s["n_fills"] == r["n_fills"], (mine, w)


def test_onchain_factors_are_lagged_one_day_and_halving_is_not():
    raw = factors15.btc_factors(); lag = factors15.lag_for_decision(raw)
    for d in ("2020-03-13", "2022-06-18", "2024-04-21", "2025-12-31"):
        t = pd.Timestamp(d, tz="UTC")
        for col in ("mvrv", "puell", "miner_cost_ratio", "ex_netflow_z", "hash_sma30"):
            a, b = lag.loc[t, col], raw.loc[t - pd.Timedelta(days=1), col]
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b), (d, col)
        assert lag.loc[t, "halving_phase"] == pytest.approx(raw.loc[t, "halving_phase"])            # 链上事实不滞后
    eth_raw = factors15.eth_factors(); eth_lag = factors15.lag_for_decision(eth_raw); t = pd.Timestamp("2023-03-01", tz="UTC")
    assert eth_lag.loc[t, "mvrv"] == pytest.approx(eth_raw.loc[t - pd.Timedelta(days=1), "mvrv"])


def test_aligned_factor_never_uses_same_day_value(ctx):
    fb = factors15.btc_factors(); al = ctx["F"]["BTCUSDT"]
    for ts in pd.DatetimeIndex(pd.to_datetime(ctx["bars"]["BTCUSDT"]["ts"], utc=True))[-400::37]:
        d = ts.normalize(); v = al.loc[ts, "mvrv"]
        assert v == pytest.approx(fb.loc[d - pd.Timedelta(days=1), "mvrv"]) and (fb.loc[d, "mvrv"] != fb.loc[d - pd.Timedelta(days=1), "mvrv"] or True)


def test_subclass_without_gates_equals_parent(ctx):
    fee, slip = COSTS[25]
    for spec in ({"name": "x", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_mode": "state_machine", "risk_exit_dd": None}, {"name": "y", "assets": ["ETHUSDT"], "mode": "trend", "risk_exit_dd": 0.2}):
        a = HoldingBacktester(ctx["bars"], spec, fee, slip, "2019-01-01", "2023-12-31", risk_params=ctx["c"].get("trendsys", {}).get("risk", {})).run()
        b = FactorBacktester(ctx["bars"], spec, fee, slip, "2019-01-01", "2023-12-31", risk_params=ctx["c"].get("trendsys", {}).get("risk", {})).run()
        assert a.final["liquidation_usdt"] == pytest.approx(b.final["liquidation_usdt"], abs=1e-9) and len(a.fills) == len(b.fills)


def test_gates_cannot_be_bypassed(ctx):
    idx = pd.DatetimeIndex(pd.to_datetime(ctx["bars"]["BTCUSDT"]["ts"], utc=True)); never = pd.Series(False, index=idx)
    fee, slip = COSTS[15]
    r = FactorBacktester(ctx["bars"], {"name": "g", "assets": ["BTCUSDT"], "mode": "trend", "entry_gate": {"BTCUSDT": never}}, fee, slip, "2018-01-01", None).run()
    assert len(r.fills) == 0                                                                           # 估值门控关闭 → 趋势再好也不入场
    r = FactorBacktester(ctx["bars"], {"name": "f", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "factor_entry": {"BTCUSDT": never}, "factor_exit": {"BTCUSDT": never}}, fee, slip, "2018-01-01", None).run()
    assert len(r.fills) == 0                                                                           # 单因子模式无入场信号 → 不交易
    always = pd.Series(True, index=idx)
    r = FactorBacktester(ctx["bars"], {"name": "x", "assets": ["BTCUSDT"], "mode": "trend", "force_exit": {"BTCUSDT": always}}, fee, slip, "2018-01-01", "2019-12-31").run()
    assert all(t["reason"] == "factor_exit" for t in r.trades.to_dict("records")) and r.final["open_positions"] in (0, 1)
    # 门控只在“当时”生效：第 t 日的门控值来自 t−1 的链上数据（对齐后的序列）
    for s in ctx["S"].values():
        if s["group"] == "single":
            assert s.get("signal") == "factor"                                                           # 单因子策略不进入 trend 可执行组


def test_miner_cost_is_a_positive_band_not_a_floor():
    f = factors15.btc_factors().loc["2018-01-01":]
    ok = f[["miner_cost_low", "miner_cost_mid", "miner_cost_high"]].dropna()
    assert (ok["miner_cost_low"] > 0).all() and (ok["miner_cost_low"] < ok["miner_cost_mid"]).all() and (ok["miner_cost_mid"] < ok["miner_cost_high"]).all()
    assert (f["price"] < f["miner_cost_mid"]).any()                                                     # 价格确实多次跌破中值成本：证明它不是绝对底部
    eff = factors15.fleet_efficiency(pd.DatetimeIndex(pd.to_datetime(["2018-01-01", "2021-01-01", "2026-01-01"], utc=True)))
    assert eff.iloc[0] == pytest.approx(100.0) and 38 < eff.iloc[1] < 60 and eff.iloc[2] == pytest.approx(22.0)


def test_selection_uses_training_window_only():
    rows = []
    def mk(name, fam, w, liq, mdd=-0.2, n=12):
        for cost in (0, 15, 25):
            rows.append({"name": name, "family": fam, "window": w, "cost_bps": cost, "liquidation_net_profit_usdt": liq, "max_drawdown": mdd, "trades": {"n_round_trips": n}, "annual_returns": {}, "regimes": {}, "excess_vs_bh": {}, "params": None, "assets": ["BTCUSDT"]})
    for w, (a, b, base) in {"train": (100, 50, 60), "valid": (10, 900, 5), "oos": (1, 999, 0)}.items():
        mk("C1_a", "C1", w, a); mk("C1_b", "C1", w, b); mk("B1_BTC_SMA200", "B1", w, base)
    strat = [{"name": "C1_a", "family": "C1"}, {"name": "C1_b", "family": "C1"}, {"name": "B1_BTC_SMA200", "family": "B1"}]
    j = phase15.select_and_judge(rows, strat)["C1"]
    assert j["chosen_on_train"] == "C1_a"                                                              # 样本外更好的 C1_b 不会被选
    assert j["oos"]["all_grid_oos_for_transparency"]["C1_b"] == 999 and j["oos"]["chosen"] == 1


def test_judgement_requires_all_windows_and_enough_trades():
    rows = []
    def mk(name, fam, w, liq, n=12, mdd=-0.2):
        for cost in (0, 15, 25):
            rows.append({"name": name, "family": fam, "window": w, "cost_bps": cost, "liquidation_net_profit_usdt": liq, "max_drawdown": mdd, "trades": {"n_round_trips": n}, "annual_returns": {}, "regimes": {}, "excess_vs_bh": {}, "params": None, "assets": ["BTCUSDT"]})
    for w in ("train", "valid", "oos"):
        mk("B1_BTC_SMA200", "B1", w, 100)
    mk("C3_x", "C3", "train", 50); mk("C3_x", "C3", "valid", 50); mk("C3_x", "C3", "oos", 500)              # 只有样本外更好
    mk("C4_x", "C4", "train", 200, n=3); mk("C4_x", "C4", "valid", 200, n=3); mk("C4_x", "C4", "oos", 200, n=3)  # 全面更好但交易太少
    v = phase15.select_and_judge(rows, [{"name": "C3_x", "family": "C3"}, {"name": "C4_x", "family": "C4"}, {"name": "B1_BTC_SMA200", "family": "B1"}])
    assert v["C3"]["verdict"].startswith("样本外优于基准但训练") and v["C4"]["verdict"].startswith("证据不足")


def test_dry_run_writes_only_to_output_dir(tmp_path, ctx):
    def h(d: Path):
        return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}
    watch = [ROOT / "data_store" / "brando_pa" / "phase10_holding", data15.RAW, ROOT / "reports_out" / "brando_pa" / "phase8_forward"]
    before = [h(d) for d in watch]
    res = phase15.run_phase15(ctx["c"], out_dir=tmp_path, dry_run=True, families=["BH", "B1", "C1"])
    assert (tmp_path / "phase15_results_dry.json").exists() and len(res["rows"]) == 7 and res["real_trading"].startswith("disabled")
    assert [h(d) for d in watch] == before


def test_data_audit_lists_unavailable_sources_and_lag_rule(ctx):
    a = data15.audit(ctx["bars"])
    for k in ("spot_etf_flows", "staking_queue", "lth_sth_cost_basis", "institutional_disclosed_cost"):
        assert k in a["unavailable"]
    assert "t−1" in a["decision_lag_rule"] and a["cross_checks"]["hashrate_cm_over_bci"]["median_ratio"] == pytest.approx(1.0, rel=1e-3)
    assert all(v.get("future_timestamps", 0) == 0 for v in a["sources"].values())
