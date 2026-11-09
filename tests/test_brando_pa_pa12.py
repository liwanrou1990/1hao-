"""第十二阶段：核心复现（A = Phase 10 B2、B = Phase 10 S1 逐项一致）、无未来函数（截断数据决策一致）、暴露 ≤ 100% / 现金 ≥ 0 / 恒等式、
机会加仓只在核心持仓时发生且记录字段完整、买卖双边扣费、拒绝归因计数自洽、dry-run 流程且前瞻账户与 Phase 10/11 结果文件不被触碰。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.pa11.engine11 import MarketContext
from brando_pa.pa12.engine12 import Cfg12, CompositeBacktester
from tests.test_brando_pa_holding import _synth

REAL = Path("reports_out/brando_pa/phase8_forward"); P10 = Path("docs/brando_pa_validation/phase10_holding/phase10_results.json"); P11 = Path("docs/brando_pa_validation/phase11_pa/phase11_results.json")
RP = cfg().get("trendsys", {}).get("risk", {})


@pytest.fixture(scope="module")
def real():
    from brando_pa.holding.data import load_dataset, phase10_data_dir
    if not (phase10_data_dir() / "BTCUSDT_1d.parquet").exists() or not P10.exists():
        pytest.skip("dataset / phase10 results missing")
    bars = load_dataset(); return bars, MarketContext(bars)


def test_core_reproduces_phase10_B2_and_S1_exactly(real):
    bars, ctx = real; rows = pd.DataFrame(json.loads(P10.read_text())["rows"])
    for core, ref in (("B2", "B2_modelB_state_machine_daily_repro"), ("SMA200_btc", "S1_BTC_SMA200")):
        r = CompositeBacktester(bars, Cfg12(core=core, name=core), 20.0, 5.0, "2018-01-01", "2026-11-07", ctx=ctx, risk_params=RP).run()
        x = rows[(rows.name == ref) & (rows.window == "full") & (rows.cost_bps == 25)].iloc[0]
        assert r.final["liquidation_net_profit_usdt"] == pytest.approx(x.liquidation_net_profit_usdt, abs=1e-6) and r.final["n_fills"] == x.n_fills and float(r.nav.drawdown.min()) == pytest.approx(x.max_drawdown, abs=1e-9)


def test_no_lookahead_and_constraints_on_synthetic():
    bars = _synth(n=1500, seed=42, trend=0.002, vol=0.03); end = str(bars["BTCUSDT"]["ts"].iloc[1199].date())
    e = Cfg12(core="B2", core_frac=0.7, pa_add=("A", "B"), exit_mode="structure_reduce", name="t")
    r1 = CompositeBacktester(bars, e, 20.0, 5.0, "2020-08-01", end, risk_params=RP).run()
    cut = {s: b.iloc[:1200].copy() for s, b in bars.items()}; r2 = CompositeBacktester(cut, e, 20.0, 5.0, "2020-08-01", end, risk_params=RP).run()
    pd.testing.assert_frame_equal(r1.nav[["ts", "equity", "cash"]].reset_index(drop=True), r2.nav[["ts", "equity", "cash"]].reset_index(drop=True))
    assert r1.final["identity_ok"] and (r1.nav["cash"] >= -1e-9).all() and (r1.nav["exposure"] <= 1.0 + 1e-9).all() and (r1.nav["core_exposure"] + r1.nav["opp_exposure"] <= r1.nav["exposure"] + 1e-6).all()


def test_opportunity_adds_only_when_core_holding_and_records_complete(real):
    bars, ctx = real; found = False
    for e in (Cfg12(core="B2", core_frac=0.7, pa_add=("A",), name="C"), Cfg12(core="B2", core_frac=0.5, pa_add=("A", "B"), exit_mode="t1_partial", name="F2")):
        r = CompositeBacktester(bars, e, 20.0, 5.0, "2018-01-01", "2026-11-07", ctx=ctx, risk_params=RP).run()
        if len(r.adds):
            found = True
            for k in ("signal_ts", "market_state", "confirmed_support_zone", "expected_entry_zone", "invalidation", "expected_exit", "net_risk_to_invalidation", "position_pct_of_equity", "actual_entry_ts", "actual_entry_price", "trigger"):
                assert k in r.adds.columns, k
            assert (r.adds["position_pct_of_equity"] <= (1 - e.core_frac) + 1e-9).all() and (r.adds["invalidation"] < r.adds["actual_entry_price"]).all()
            nav = r.nav.set_index("ts")
            for _, a in r.adds.iterrows():                               # 加仓当日（信号日）核心已持仓
                assert nav.loc[pd.Timestamp(a["signal_ts"]), "core_exposure"] > 0
        f = r.fills; b = f[f.side == "BUY"]; s = f[f.side == "SELL"]
        assert (b.fee > 0).all() and (s.fee > 0).all() and np.allclose(b.fill_price, b.ref_price * 1.0005) and np.allclose(s.fill_price, s.ref_price * 0.9995)
    assert found


def test_reject_audit_counts_consistent(tmp_path, real):
    bars, ctx = real
    from brando_pa.pa12.reject_audit import reject_audit
    a = reject_audit(tmp_path, ctx=ctx); c = a["candidates"]
    assert c["triggered_total"] == c["accepted_by_phase11_rule"] + c["rejected_no_resistance_above_at_all"] + c["rejected_resistance_exists_but_below_1_5R"] + c["rejected_rr_below_threshold_with_T1"]
    assert c["core_already_holding_share"] == 1.0 and a["counterfactual_accepts"]["drop_T1_structure_requirement(any trigger accepted)"] == c["triggered_total"]
    assert (tmp_path / "phase11_reject_audit.md").exists()


def test_dry_run_and_protected_files_untouched(tmp_path, real):
    from brando_pa.pa12.phase12 import run_phase12
    prot = [p for p in list(REAL.rglob("*")) + [P10, P11] if p.is_file()] if REAL.exists() else [P10, P11]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in prot}
    res = run_phase12(out=tmp_path, dry_run=True)
    rows = pd.DataFrame(res["rows"]); assert {"A_B2", "B_SMA200_btc", "G_BUYHOLD_BTC"} <= set(rows.group) and any(g.startswith("C_B2") for g in rows.group)
    assert (tmp_path / "phase12_dry_run.json").exists() and (tmp_path / "summary_dry.md").exists() and res["cost_note"].startswith("单侧")
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in prot} == before
