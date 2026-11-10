"""第二十一阶段正式上线验收：仓位漂移只记录（50/70/85%）与风险算术；实时 / 延迟 / 补记分类；Mac 基线保护；72 小时验收逻辑（加速模拟：无故障通过、有故障按条目失败）；
服务锁与异常记录；30/90/365 天评估不提前判定盈利；审计（截断重放无未来数据、无下单接口、本金一致）；页面；部署脚本语法与拒绝在生产目录运行。"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.data.store import BarStore
from brando_pa.pa21 import SEED_DIR, acceptance21, audit21, dashboard21, forward21, macguard21, monitor21, service21, sim72

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (SEED_DIR / "bars" / "BTCUSDT_1d.parquet").exists(), reason="seed bars missing")
T = lambda s: pd.Timestamp(s, tz="UTC")


def _store(root: Path) -> BarStore:
    st = BarStore(root=root, fallback_root=None); seed = BarStore(root=SEED_DIR, fallback_root=None); rng = np.random.default_rng(5)
    for s in ("BTCUSDT", "ETHUSDT"):
        h = seed.load(s, "1d", allow_fallback=False); days = pd.date_range(h["ts"].max() + pd.Timedelta(days=1), T("2027-02-28"), freq="D"); c = float(h["close"].iloc[-1]) * np.exp(np.cumsum(0.004 + rng.normal(0, 0.01, len(days)))); o = np.r_[float(h["close"].iloc[-1]), c[:-1]]
        st.upsert(pd.concat([h, pd.DataFrame({"ts": days, "open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1e5, "symbol": s, "tf": "1d", "quote_volume": np.nan, "source": "okx_spot", "ingested_at": days + pd.Timedelta(days=1, hours=1)})], ignore_index=True))
    return st


def test_drift_math_and_events_record_only():
    assert monitor21.drop_to_hit(0.25, 0.0, 0.50) is None and monitor21.drop_to_hit(0.50, 0.0, 0.50) == pytest.approx(1.0)
    assert monitor21.drop_to_hit(0.85, 0.0, 0.50) == pytest.approx(0.5 / 0.85) and monitor21.drop_to_hit(0.50, -0.20, 0.50) == pytest.approx(0.75)
    ws = [0.25, 0.55, 0.72, 0.60, 0.86, 0.40, 0.55]
    rows = [{"date": f"2027-01-{i + 1:02d}", "btc_close": 1.0, "accounts": {"FIX25_Y": {"btc_weight": w, "drawdown": 0.0}}} for i, w in enumerate(ws)]
    ev = [(int(e["date"][-2:]) - 1, e["level"]) for e in monitor21.drift_events(rows)]
    assert ev == [(1, 0.5), (2, 0.7), (4, 0.7), (4, 0.85), (6, 0.5)] and all("不交易" in e["action"] for e in monitor21.drift_events(rows))


def test_observation_classification():
    c = T("2026-11-12")
    assert monitor21.classify(c, c + pd.Timedelta(hours=1), "okx_spot", c + pd.Timedelta(hours=1)) == "实时观察"
    assert monitor21.classify(c, c + pd.Timedelta(hours=1), "okx_spot", c + pd.Timedelta(hours=10)) == "延迟记录"
    assert monitor21.classify(c, c + pd.Timedelta(hours=40), "okx_spot", c + pd.Timedelta(hours=40)) == "历史补记"
    assert monitor21.classify(c, c + pd.Timedelta(hours=1), "binance_spot_via_static_klines", c + pd.Timedelta(hours=1)) == "历史补记"


def test_macguard_detects_rewrites_and_allows_appends(tmp_path):
    prod = sim72._fake_prod(tmp_path / "prod"); base = macguard21.snapshot(prod); ev = prod / "reports_out/brando_pa/phase8_forward/events.jsonl"
    ev.write_text(ev.read_text() + '{"n": 1}\n'); (prod / "reports_out/brando_pa/phase8_forward/health.json").write_text('{"x": 1}'); assert macguard21.compare(base, prod)["ok"]
    ev.write_text(ev.read_text().replace('{"n": 0}', '{"n": 9}')); r = macguard21.compare(base, prod); assert not r["ok"] and "被改写" in r["problems"][0]
    ev.write_text('{"n": 0}\n{"n": 1}\n'); (prod / "data_store/brando_pa/phase21_bars").mkdir(parents=True); assert any("第二十一阶段目录" in x for x in macguard21.compare(base, prod)["problems"])
    (prod / "data_store/brando_pa/phase21_bars").rmdir(); (prod / "config/brando_pa.yaml").write_text("usdt:\n  initial_cash_usdt: 20000\n"); assert any("初始本金" in x for x in macguard21.compare(base, prod)["problems"])


def test_accelerated_72h_clean_passes_faults_fail_on_injected_items(tmp_path):
    ok = sim72.run(tmp_path / "clean", dashboard_last=False); assert ok["passed"], {k: v for k, v in ok["criteria"].items() if not v["ok"]}
    bad = sim72.run(tmp_path / "faults", faults=True, dashboard_last=False); f = {k for k, v in bad["criteria"].items() if not v["ok"]}
    assert f == {"coverage", "max_gap", "errors", "daily_records_realtime", "mac_unchanged"} and not bad["passed"]
    assert bad["simulation"]["statuses"] == {"ok": 59, "data_unavailable": 6, "error": 1, "skipped_locked": 1}
    early = acceptance21.accept72("2026-11-12 00:00", tmp_path / "clean" / "out", now=T("2026-11-13 00:00"), baseline=macguard21.snapshot(tmp_path / "clean" / "prod"))
    assert not early["criteria"]["elapsed"]["ok"] and not early["passed"]                                         # 未满 72 小时不能通过


def test_service_lock_and_error_are_recorded_not_traded(tmp_path, monkeypatch):
    st = _store(tmp_path / "bars"); out = tmp_path / "out"
    r = service21.tick(now=T("2026-11-14 01:00"), store=st, out_dir=out, fetch=False, dashboard=False); assert r["status"] == "ok" and r["n_days"] == 3
    monkeypatch.setattr(forward21, "run_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))); r = service21.tick(now=T("2026-11-14 02:00"), store=st, out_dir=out, fetch=False, dashboard=False)
    assert r["status"] == "error" and "boom" in r["error"]; h = json.loads((out / "health.json").read_text()); assert h["consecutive_failures"] == 1 and h["auto_trading"] is False
    runs = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines()]; assert [x["status"] for x in runs] == ["ok", "error"]


def test_evaluation_never_declares_profit_and_waits_for_maturity(tmp_path):
    out = tmp_path / "out"; ev0 = acceptance21.evaluate(out, today=T("2026-11-20")); assert all(w["status"] == "未到期" for w in ev0["windows"])
    st = _store(tmp_path / "bars"); forward21.run_once(now=T("2027-01-20 01:00"), store=st, out_dir=out, fetch=False); monitor21.update(T("2027-01-20 01:00"), st, out)
    ev = acceptance21.evaluate(out, today=T("2027-01-20")); w30 = ev["windows"][0]
    assert w30["status"] == "已到期（描述性）" and "不判定" in w30["profit_verdict"] and ev["windows"][1]["status"] == "未到期" and all(w["status"] == "未到期" for w in ev["windows"][2:])
    assert "验证成功" not in json.dumps([w.get("status") for w in ev["windows"]], ensure_ascii=False) and w30["trade_sample_insufficient"]["B2_original"]
    assert set(w30["risk_criteria"]) == {"mdd_within_50", "dd_vs_btc", "cost", "upside"} and w30["metrics"]["FIX25_Y"]["avg_btc_weight"] < 0.35


def test_audit_no_future_leak_no_order_api_capital_consistent():
    lk = audit21.leak_truncation(start="2025-10-01", cuts=("2025-12-31", "2026-01-01", "2026-02-15")); assert lk["ok"] and all(c["days_compared"] > 0 for c in lk["cuts"])
    assert acceptance21.static_no_order_api()["ok"] and audit21.capital_consistency()["ok"] and audit21.mac_isolation()["ok"]


def test_dashboard_launch_sections(tmp_path):
    st = _store(tmp_path / "bars"); out = tmp_path / "out"; service21.tick(now=T("2026-12-05 01:00"), store=st, out_dir=out, fetch=False, dashboard=False)
    page = dashboard21.build(out_dir=out, out=tmp_path).read_text(encoding="utf-8")
    for s in ("仓位漂移监测（FIX25_Y）", "BTC 参与度与收益差", "两类风险记录", "风险预算，不是止损", "前瞻评估计划", "<th>相对 BH</th>", "<th>平均 BTC 仓位</th>", "研究本金 · 100,000 USDT（虚拟）", "本地服务"):
        assert s in page, s
    assert "<button" not in page and "<form" not in page and "立即买入" not in page


def test_ctl_script_syntax_and_refuses_production_dir():
    sh = ROOT / "scripts" / "brando_pa21_ctl.sh"; assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0
    r = subprocess.run(["bash", str(sh), "status"], env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "BRANDO_PROD_ROOT": str(ROOT)}, capture_output=True, text=True); assert r.returncode == 3 and "REFUSED" in r.stdout
    r = subprocess.run(["bash", str(sh), "nonsense"], env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "BRANDO_PROD_ROOT": "/nonexistent"}, capture_output=True, text=True); assert r.returncode == 2
