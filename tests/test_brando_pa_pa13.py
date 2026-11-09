"""第十三阶段：冻结规格（A = Mac B 规则、B = Mac A 规则、买入持有永不卖出）、F 波动跟踪状态、影子运行器与生产引擎同机制且目录隔离（生产目录哈希不变、锁不同）、
审计不接受未来日期且窗口未到期不填数、报告模板只用真实记录、冻结清单含提交与比对、bash 3.2 规则扫描覆盖新脚本。"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.forward.accounts import account_specs
from brando_pa.pa13.shadow import ShadowRunner, shadow_specs
from tests.test_brando_pa_causality import _synth_oc, T
from tests.test_brando_pa_forward import _store

ROOT = Path(__file__).resolve().parents[1]; PROD = ROOT / "reports_out" / "brando_pa" / "phase8_forward"


def _hashes(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


def _drive(tmp: Path, days=("2024-08-02", "2024-08-03", "2024-08-04", "2024-08-05")):
    bars = _synth_oc(); store = _store(tmp, bars); r = ShadowRunner(cfg(), log_dir=tmp / "p13", store=store)
    r.run_once(now=T("2024-08-01 05:00"))
    for d in days:
        r.run_once(now=T(f"{d} 00:30")); r.run_once(now=T(f"{d} 01:10"))
    return r, bars


def test_frozen_specs_match_mac_rules():
    c = cfg(); s = shadow_specs(c); m = account_specs(c)
    assert s["A_B2_state_machine"]["weights"] == m["B_modelB_state_machine"]["weights"] and s["A_B2_state_machine"]["risk_mode"] == m["B_modelB_state_machine"]["risk_mode"]
    assert s["B_BTC_SMA200"]["weights"] == m["A_BTC_SMA200"]["weights"] and s["B_BTC_SMA200"]["risk_mode"] == "none"
    for n in ("C_BUYHOLD_BTC", "D_BUYHOLD_ETH", "E_BUYHOLD_5050"):
        w = s[n]["weights"]; assert w["none"] == w["both"] == w["btc_only"] == w["eth_only"] and s[n]["alloc_params"]["drift_band"] >= 1.0
    assert s["F_B2_vol_trail"]["vol_trail"] == {"atr_mult": 3.0, "atr_n": 14, "swing_k": 3, "reduce_to": 0.5}


def test_shadow_runner_runs_all_six_and_buyhold_never_sells(tmp_path):
    r, bars = _drive(tmp_path)
    assert set(r.accounts) == {"A_B2_state_machine", "B_BTC_SMA200", "C_BUYHOLD_BTC", "D_BUYHOLD_ETH", "E_BUYHOLD_5050", "F_B2_vol_trail"}
    assert all(v["ok"] for v in r.verify_all().values())
    for n in ("C_BUYHOLD_BTC", "D_BUYHOLD_ETH", "E_BUYHOLD_5050"):
        ev = pd.read_json(tmp_path / "p13" / n / "events.jsonl", lines=True); f = ev[ev["kind"] == "fill"]
        assert len(f) > 0 and (f["side"] == "BUY").all()
    stA = r.accounts["A_B2_state_machine"].state; assert stA["forward_start_utc"] is not None and len(stA["processed_orders"]) > 0
    # 决策只在已收盘日线之后：所有 signal_ts ≤ decision_time
    ev = pd.read_json(tmp_path / "p13" / "A_B2_state_machine" / "events.jsonl", lines=True); sig = ev[ev["kind"] == "signal"]
    assert (pd.to_datetime(sig["signal_ts"], utc=True) <= pd.to_datetime(sig["decision_time"], utc=True)).all()


def test_vol_trail_state_reduces_and_restores(tmp_path):
    bars = _synth_oc()
    # 构造：上涨后急跌 → F 应进入 reduced；之后再创新高 → 恢复
    d = bars["BTCUSDT"]["1d"].copy(); n = len(d)
    import numpy as np
    base = np.linspace(100, 200, n); base[-12:-6] = np.linspace(200, 150, 6); base[-6:] = np.linspace(150, 230, 6)
    for col in ("open", "high", "low", "close"):
        d[col] = base * (1.01 if col == "high" else 0.99 if col == "low" else 1.0)
    bars["BTCUSDT"]["1d"] = d
    store = _store(tmp_path, bars); r = ShadowRunner(cfg(), log_dir=tmp_path / "p13", store=store, accounts=["F_B2_vol_trail"])
    acc = r.accounts["F_B2_vol_trail"]; acc.bars_ref = r.load_bars()
    ts = pd.to_datetime(d["ts"], utc=True)
    acc.update_vol_trail(ts.iloc[-13] + pd.Timedelta(days=1), {"BTCUSDT": True, "ETHUSDT": False}); assert acc.state["vol_trail"]["BTCUSDT"]["peak_close"] is not None
    notes = acc.update_vol_trail(ts.iloc[-7] + pd.Timedelta(days=1), {"BTCUSDT": True, "ETHUSDT": False}); assert acc.state["vol_trail"]["BTCUSDT"]["reduced"] and acc.allocs["cost"].mult["BTCUSDT"] == 0.5, notes
    acc.update_vol_trail(ts.iloc[-1] + pd.Timedelta(days=1), {"BTCUSDT": True, "ETHUSDT": False}); assert not acc.state["vol_trail"]["BTCUSDT"]["reduced"] and acc.allocs["cost"].mult["BTCUSDT"] == 1.0
    acc.update_vol_trail(ts.iloc[-1] + pd.Timedelta(days=1), {"BTCUSDT": False, "ETHUSDT": False}); assert acc.state["vol_trail"]["BTCUSDT"]["peak_close"] is None


def test_isolation_production_dir_untouched_and_locks_distinct(tmp_path):
    before = _hashes(PROD)
    r, _ = _drive(tmp_path)
    assert _hashes(PROD) == before
    from brando_pa.pa13.isolation import isolation_check
    iso = isolation_check(tmp_path / "p13"); assert iso["paths_disjoint"] and iso["locks_distinct"] and not iso["live_trading_allowed"] and iso["production_hash_unchanged"] and iso["launchd_labels"]["distinct"]
    from brando_pa.pa13.shadow import shadow_dirs
    d = shadow_dirs(); assert d["log_dir"] != d["production_dir"] and d["bars_root"].name == "phase13_bars"


def test_audit_rejects_future_dates_and_marks_unexpired_horizons(tmp_path):
    r, _ = _drive(tmp_path)
    from brando_pa.pa13.audit13 import shadow_audit
    a = shadow_audit(tmp_path / "p13", now=T("2024-08-05 02:00"), out_dir=tmp_path / "audit")
    assert set(a["accounts"]) == set(r.accounts) and not a["future_dates_found"]
    for x in a["accounts"].values():
        assert all(x["horizons"][f"{h}d"]["status"] == "未到期" for h in (30, 90, 180, 365)) and x["elapsed_days"] < 5
    a2 = shadow_audit(tmp_path / "p13", now=T("2024-07-01 00:00"), out_dir=tmp_path / "audit2", write=False)       # 审计时钟早于记录 → 视为未来日期
    assert a2["future_dates_found"] and not a2["ok"]
    a3 = shadow_audit(tmp_path / "empty", now=T("2024-08-05 02:00"), out_dir=tmp_path / "audit3"); assert "尚未开始" in a3["status"]


def test_reports_and_dashboard_from_real_records_only(tmp_path):
    r, _ = _drive(tmp_path)
    from brando_pa.pa13.report13 import daily_report13, weekly_report13
    p = daily_report13(tmp_path / "p13", now=T("2024-08-05 02:00")); w = weekly_report13(tmp_path / "p13", now=T("2024-08-05 02:00"))
    md = Path(p).read_text(encoding="utf-8") + Path(w).read_text(encoding="utf-8")
    assert "前瞻模拟" in md and "A_B2_state_machine" in md and "未到期" in md and "2024-08-0" in md and "2025" not in md
    from brando_pa.pa13.dashboard13 import build_dashboard13
    out = build_dashboard13(tmp_path / "p13", tmp_path / "dash.html", embed=False, now=T("2024-08-05 02:00")); h = Path(out).read_text(encoding="utf-8")
    assert "前瞻监控" in h and "A_B2_state_machine" in h and "真实账户" in h


def test_freeze_manifest_and_mac_scripts():
    from brando_pa.pa13.freeze import freeze_manifest
    with tempfile.TemporaryDirectory() as td:
        m = freeze_manifest(Path(td)); assert m["git_commit"] and len(m["strategies"]) == 6 and m["costs"]["cost_ledger_one_side_bps"]["total"] == 15 and any("不同" in r["same"] for r in m["comparison_historical_B2_vs_mac_B_vs_shadow_A"])
    for f in ("scripts/brando_pa_shadow13_ctl.sh", "deploy/install_mac_brando_pa_phase13.sh"):
        txt = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]", txt) and "com.brando-pa.shadow13" in txt and "phase8_forward" not in txt.replace("never touches reports_out/brando_pa/phase8_forward", "").replace("It never touches", "")
    assert "BRANDO_SHADOW13_CONFIRM" in (ROOT / "deploy/install_mac_brando_pa_phase13.sh").read_text(encoding="utf-8")
