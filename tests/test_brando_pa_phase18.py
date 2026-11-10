"""第十八阶段：V0 与原 B2 逐位一致；熔断触发条件对所有变体不变；数据故障（技术性停机）不能被任何恢复机制或阶梯绕过；
固定冷静期长度；趋势确认提前结束需熔断后 ≥ 14 天且连续 5 日收盘 > SMA200；阶梯上限；市场上下文无未来数据；ETH 拼接；运行只写输出目录。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.holding.data import load_dataset
from brando_pa.holding.metrics import summarize
from brando_pa.pa18 import phase18
from brando_pa.pa18.variants18 import VARIANTS, RecoveryBacktester, RecoveryBreaker, market_context
from brando_pa.trendsys.risk_layer import COOLDOWN, EMERGENCY_HALT, NORMAL, RECOVERY

ROOT = Path(__file__).resolve().parents[1]
P10 = ROOT / "docs" / "brando_pa_validation" / "phase10_holding" / "phase10_results.json"
SM = cfg().get("trendsys", {}).get("risk", {}).get("state_machine", {})


def _ctx(days=200, above=True):
    idx = pd.date_range("2024-01-01", periods=days, freq="D", tz="UTC")
    df = pd.DataFrame({"close": 100.0, "sma200": 90.0 if above else 110.0, "above_streak": np.arange(1, days + 1) if above else 0, "w_close": np.nan, "w_low": np.nan, "sma20w": np.nan}, index=idx)
    return {"BTCUSDT": df}, idx


def _halt(b, idx):
    b.evaluate(idx[0], 10000.0); d = b.evaluate(idx[1], 7900.0)                                     # 回撤 21% → 紧急停机
    assert d.state == EMERGENCY_HALT and d.force_exit
    b.evaluate(idx[2], 7900.0); assert b.state == COOLDOWN


@pytest.mark.skipif(not P10.exists(), reason="phase10 results missing")
def test_v0_reproduces_original_b2_exactly():
    p10 = json.loads(P10.read_text()); ref = next(r for r in p10["rows"] if r["name"] == "B2_modelB_state_machine_daily_repro" and r["window"] == "full" and r["cost_bps"] == 15)
    r = RecoveryBacktester(load_dataset(cfg()), {"name": "V0", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, "2018-01-01", p10["latest_complete_day"], variant="V0_original", risk_params=cfg()["trendsys"]["risk"]).run()
    assert summarize(r)["liquidation_net_profit_usdt"] == pytest.approx(ref["liquidation_net_profit_usdt"], abs=1e-6) and summarize(r)["n_fills"] == ref["n_fills"]


@pytest.mark.parametrize("name", [v for v in VARIANTS if v != "V0_original"])
def test_halt_triggers_unchanged_for_all_variants(name):
    ctx, idx = _ctx(); b = RecoveryBreaker(SM, VARIANTS[name], ctx); _halt(b, idx)                     # 20% 回撤照常熔断
    b2 = RecoveryBreaker(SM, VARIANTS[name], ctx); b2.evaluate(idx[0], 10000.0); d = b2.evaluate(idx[1], 10000.0, daily_drop=-0.16, exposure=1.0)
    assert d.state == EMERGENCY_HALT                                                                  # 单日跌 15% 照常熔断


@pytest.mark.parametrize("name", [v for v in VARIANTS if v != "V0_original"])
def test_data_fault_cannot_be_bypassed(name):
    ctx, idx = _ctx(); b = RecoveryBreaker(SM, VARIANTS[name], ctx); _halt(b, idx); b.set_data_fault(True, "stale")
    for i in range(3, 200):
        d = b.evaluate(idx[i], 8000.0 + i)
        assert d.max_exposure == 0.0 and d.data_fault                                                 # 市场全面恢复也不能买入


def test_fixed_cooldown_lengths():
    for name, n in (("V1_fixed30", 30), ("V2_fixed60", 60), ("V2b_fixed90", 90)):
        ctx, idx = _ctx(); b = RecoveryBreaker(SM, VARIANTS[name], ctx); b.halt_count = 2; _halt(b, idx)
        assert (b.cooldown_until - idx[2]).days == n


def test_trend_early_exit_needs_14_days_and_streak():
    ctx, idx = _ctx(); b = RecoveryBreaker(SM, VARIANTS["V3_trend_confirm"], ctx); _halt(b, idx)
    for i in range(3, 15):
        b.evaluate(idx[i], 8000.0); assert b.state == COOLDOWN                                        # 熔断后 < 14 天不得提前结束
    b.evaluate(idx[15], 8000.0); assert b.state == RECOVERY and "early_trend" in b.transitions[-1]["why"]
    ctx2, idx2 = _ctx(above=False); c = RecoveryBreaker(SM, VARIANTS["V3_trend_confirm"], ctx2); _halt(c, idx2)
    for i in range(3, 25):
        c.evaluate(idx2[i], 8000.0)
    assert c.state == COOLDOWN                                                                        # 未站上 SMA200 → 按原冷静期


def test_ladder_caps_then_normal():
    ctx, idx = _ctx(300); b = RecoveryBreaker(SM, VARIANTS["V5_ladder"], ctx); _halt(b, idx); caps = {}
    for i in range(3, 300):
        d = b.evaluate(idx[i], 8000.0 + i)                                                              # 净值单调上升，不再熔断
        if b.state == RECOVERY:
            caps[(idx[i] - b.recovery_since).days] = d.max_exposure
    assert caps[0] == 0.25 and caps[25] == 0.50 and caps[45] == 0.75 and b.state == NORMAL


def test_market_context_has_no_future_data():
    bars = phase18.load_bars(); idx = pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True))
    ctx = market_context(bars, ["BTCUSDT"], idx)["BTCUSDT"]; c = bars["BTCUSDT"].set_index(idx)["close"]
    t = idx[1500]; assert ctx.loc[t, "sma200"] == pytest.approx(c.loc[:t].tail(200).mean())
    wk = ctx.dropna(subset=["w_close"]); assert (wk.index.dayofweek == 6).all()


def test_eth_splice_has_no_gaps():
    bars = phase18.load_bars(); ts = pd.DatetimeIndex(pd.to_datetime(bars["ETHUSDT"]["ts"], utc=True))
    assert len(pd.date_range(ts.min(), ts.max(), freq="1D").difference(ts)) == 0 and len(bars["_eth_ffilled_days"]) == 2


def test_run_writes_only_to_output_dir(tmp_path):
    def h(d: Path):
        return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}
    watch = [phase18.RAW18, ROOT / "data_store" / "brando_pa" / "phase10_holding", ROOT / "reports_out" / "brando_pa" / "phase8_forward"]; before = [h(x) for x in watch]
    r = phase18.run_phase18(cfg(), out_dir=tmp_path, windows={"dev_2018_2021": ("2018-01-01", "2021-12-31")}, costs=[15], variants=["V0_original", "V3_trend_confirm"], write=True)
    assert (tmp_path / "phase18_results.json").exists() and r["verdicts"] == {} and r["real_trading"].startswith("disabled")
    assert [h(x) for x in watch] == before
