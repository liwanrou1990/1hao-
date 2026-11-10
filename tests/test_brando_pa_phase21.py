"""第二十一阶段：冻结规则校验与篡改检测；四个观察账户统一起点 / 本金 / 日期；只追加哈希链、重跑不重复、已记录历史不改写；
起点之前不产生任何前瞻记录；35/40/45/50% 警报只记录不交易；冻结违规拒绝运行；只写独立目录；压力情景公式；只读页面标注。"""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.data.store import BarStore
from brando_pa.pa20.alloc20 import AllocBacktester
from brando_pa.pa21 import ACCOUNTS, FORWARD_START, SEED_DIR, WARN, dashboard21, freeze21, stress21
from brando_pa.pa21 import forward21 as F
from brando_pa.trendsys.paper import verify_chain

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (SEED_DIR / "bars" / "BTCUSDT_1d.parquet").exists(), reason="seed bars missing")
T = lambda s: pd.Timestamp(s, tz="UTC")


def _store(root: Path, crash: bool = True) -> BarStore:
    """仓库内历史日线 + 合成的“未来”日线（只用于测试，不是行情）。BTC 在 2027 年 1–3 月下跌约 60%。"""
    st = BarStore(root=root, fallback_root=None); seed = BarStore(root=SEED_DIR, fallback_root=None); rng = np.random.default_rng(7)
    for s in ("BTCUSDT", "ETHUSDT"):
        h = seed.load(s, "1d", allow_fallback=False); days = pd.date_range(h["ts"].max() + pd.Timedelta(days=1), T("2027-03-31"), freq="D"); n = len(days)
        drift = np.where(days < T("2027-01-05"), 0.001, -0.011 if crash else 0.001); c = float(h["close"].iloc[-1]) * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n))); o = np.r_[float(h["close"].iloc[-1]), c[:-1]]
        d = pd.DataFrame({"ts": days, "open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995, "close": c, "volume": float(h["volume"].tail(30).mean()), "symbol": s, "tf": "1d", "quote_volume": np.nan, "source": "synthetic_test", "ingested_at": pd.Timestamp.now(tz="UTC")})
        st.upsert(pd.concat([h, d], ignore_index=True))
    return st


def _lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def test_freeze_manifest_matches_and_detects_tampering(tmp_path):
    v = freeze21.verify(); assert v["ok"], v
    m = json.loads(freeze21.MANIFEST.read_text(encoding="utf-8")); assert m["params"]["rules"]["FIX25_Y"]["weight"] == 0.25 and m["params"]["rules"]["FIX25_Y"]["mode"] == "Y" and m["params"]["initial_usdt"] == 100_000.0 and m["params"]["forward_start"] == FORWARD_START
    for f in freeze21.FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True); shutil.copy(ROOT / f, tmp_path / f)
    assert freeze21.verify(root=tmp_path)["ok"]
    p = tmp_path / "src/brando_pa/pa20/alloc20.py"; p.write_text(p.read_text(encoding="utf-8").replace("WEIGHTS = (0.0, 0.25", "WEIGHTS = (0.0, 0.30"), encoding="utf-8")
    assert freeze21.verify(root=tmp_path)["changed_files"] == ["src/brando_pa/pa20/alloc20.py"]


def test_forward_ledger_unified_append_only_and_no_history(tmp_path):
    st = _store(tmp_path / "bars"); out = tmp_path / "out"
    s0 = F.run_once(now=T("2026-11-11 12:00"), store=st, out_dir=out, fetch=False)
    assert s0["status"] == "awaiting_first_close" and not (out / "daily_ledger.jsonl").exists()                     # 起点之前不产生任何前瞻记录
    s1 = F.run_once(now=T("2026-12-20 01:00"), store=st, out_dir=out, fetch=False); L1 = _lines(out / "daily_ledger.jsonl")
    assert s1["status"] == "observing" and L1[0]["date"] == FORWARD_START and all(r["date"] >= FORWARD_START for r in L1) and L1[-1]["date"] == "2026-12-19"
    first = L1[0]["accounts"]; assert all(first[a]["liquidation"] == pytest.approx(100_000.0) and first[a]["n_fills_cum"] == 0 for a in ACCOUNTS)  # 统一起点与本金；起点当日只决策
    d2 = L1[1]["accounts"]; assert d2["FIX25_Y"]["btc_weight"] == pytest.approx(0.25, abs=0.01) and d2["BH_BTC"]["btc_weight"] > 0.99
    assert L1[3]["catch_up"] and not L1[-1]["catch_up"]
    F.run_once(now=T("2026-12-20 02:00"), store=st, out_dir=out, fetch=False); assert _lines(out / "daily_ledger.jsonl") == L1  # 重跑不重复
    s2 = F.run_once(now=T("2027-04-01 01:00"), store=st, out_dir=out, fetch=False); L2 = _lines(out / "daily_ledger.jsonl")
    assert L2[:len(L1)] == L1 and L2[-1]["date"] == "2027-03-31" and verify_chain(out / "daily_ledger.jsonl")["ok"] and s2["chains"]["daily"]["ok"]
    fx = [r["accounts"]["FIX25_Y"]["n_fills_cum"] for r in L2]; changes = [L2[i]["date"] for i in range(1, len(L2)) if fx[i] != fx[i - 1]]
    assert changes == ["2026-11-12", "2027-01-01"]                                                                    # 只在建仓日与 1 月第一个交易日交易


def test_alerts_record_only_and_replay_mismatch_not_rewritten(tmp_path):
    st = _store(tmp_path / "bars"); out = tmp_path / "out"; F.run_once(now=T("2027-04-01 01:00"), store=st, out_dir=out, fetch=False)
    al = _lines(out / "alerts.jsonl"); bh = [e for e in al if e["account"] == "BH_BTC"]
    assert [e["threshold"] for e in bh] == list(WARN) and bh[-1]["kind"] == "drawdown_budget_breach" and all("不触发任何交易" in e["action"] for e in al)
    L = _lines(out / "daily_ledger.jsonl"); fx = [r["accounts"]["FIX25_Y"]["n_fills_cum"] for r in L]; assert max(fx) == 2      # 警报期间 FIX25_Y 没有额外交易
    assert L[-1]["accounts"]["FIX25_Y"]["max_drawdown"] > -0.5 > L[-1]["accounts"]["BH_BTC"]["max_drawdown"]
    b = st.load("BTCUSDT", "1d", allow_fallback=False); b.loc[b["ts"] == T("2027-02-01"), "close"] *= 1.2; st.upsert(b)       # 已记录日期的行情被改写
    F.run_once(now=T("2027-04-01 03:00"), store=st, out_dir=out, fetch=False)
    assert _lines(out / "daily_ledger.jsonl") == L and any(e["kind"] == "replay_mismatch" for e in _lines(out / "events.jsonl"))
    t = out / "daily_ledger.jsonl"; x = t.read_text(encoding="utf-8").replace('"catch_up": false', '"catch_up": true', 1); t.write_text(x, encoding="utf-8"); assert not verify_chain(t)["ok"]


def test_frozen_violation_refuses_and_writes_only_own_dirs(tmp_path, monkeypatch):
    def h(d: Path):
        return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}
    watch = [ROOT / "reports_out" / "brando_pa" / x for x in ("phase8_forward", "phase13_forward", "phase20_capital100k", "phase19_longterm")] + [ROOT / "data_store" / "brando_pa" / "forward_bars", ROOT / "data_store" / "brando_pa" / "phase13_bars", SEED_DIR / "bars"]
    before = [h(x) for x in watch]; st = _store(tmp_path / "bars"); out = tmp_path / "out"
    F.run_once(now=T("2027-01-10 01:00"), store=st, out_dir=out, fetch=False); assert [h(x) for x in watch] == before
    monkeypatch.setattr(freeze21, "verify", lambda **k: {"ok": False, "changed_files": ["x"]}); n = len(_lines(out / "daily_ledger.jsonl"))
    s = F.run_once(now=T("2027-04-01 01:00"), store=st, out_dir=out, fetch=False)
    assert s["status"] == "frozen_rule_violation" and len(_lines(out / "daily_ledger.jsonl")) == n and _lines(out / "events.jsonl")[-1]["kind"] == "frozen_rule_violation"


def test_stress_formulas_and_delay_zero_matches_frozen_engine():
    ins = stress21.instant_shocks(0.87, "2017-12-17")
    assert ins["usdt_depeg_needed_alone"]["FIX25_Y（目标 25%）"] == pytest.approx(2 / 3) and ins["fix25_usdt_depeg_needed_with_btc_drop"]["BTC−80%"] == pytest.approx(0.4)
    assert ins["loss_grid"]["FIX25_Y（目标 25%）"]["BTC−80%|USDT−0%"] == pytest.approx(-0.2)
    rc = {(r["rally_x"], r["crash"]): r for r in stress21.rally_then_crash(ks=(1, 13), ds=(0.9,))}; assert not rc[(1, 0.9)]["exceeds_budget"] and rc[(13, 0.9)]["exceeds_budget"]
    cy = {r["years"]: r for r in stress21.consecutive_down_years(ds=(0.9,), ns=(1, 3))}; assert cy[1]["max_drawdown"] > -0.25 and cy[3]["exceeds_budget"]
    dates = pd.date_range(T("2031-01-01"), T("2033-06-30"), freq="D"); c = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0, 0.03, len(dates)))); b = stress21._bars(dates, c)
    a = stress21._run(b); d0 = stress21._run(b, cls=stress21.DelayedAlloc, delay=0)
    assert d0.final["liquidation_usdt"] == pytest.approx(a.final["liquidation_usdt"], rel=1e-12) and len(d0.fills) == len(a.fills)


def test_dashboard_labels_research_capital_and_no_trading(tmp_path):
    st = _store(tmp_path / "bars"); out = tmp_path / "out"; F.run_once(now=T("2026-11-11 12:00"), store=st, out_dir=out, fetch=False)
    page = dashboard21.build(out_dir=out, out=tmp_path).read_text(encoding="utf-8")
    assert "研究本金 · 100,000 USDT（虚拟）" in page and "不是真实资产" in page and "Mac 运行账户 · 10,000 USDT × 3（未改动）" in page and "0 天" in page and "不会用历史数据填充前瞻图表" in page
    assert "立即买入" not in page and "<button" not in page and "<form" not in page
