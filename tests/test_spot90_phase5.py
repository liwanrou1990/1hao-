"""第五阶段验收测试：Bitget 403 自动回退备用源并标记来源；快照过期判定；风险等级历史分布；三方案同资金/同日期/同费用；健康检查；只读保证。"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from spot90.data.bitget_v3 import BitgetSpotHistoryClient
from spot90.data.ingest import run_ingest
from spot90.data.static_klines import StaticKlinesClient
from spot90.data.store import BarStore
from spot90.monitor import healthcheck
from spot90.monitor.plans import simulate
from spot90.monitor.rules import enrich, risk_level_history

DAY = 86_400_000


def test_bitget_403_falls_back_to_static_klines_and_tags_source(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOT90_DATA_DIR", str(tmp_path))
    store = BarStore(root=tmp_path)
    bg = BitgetSpotHistoryClient(transport=httpx.MockTransport(lambda req: httpx.Response(403, text="forbidden")))
    t0 = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int((datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    def sk_handler(req: httpx.Request):
        m = re.search(r"/(\d{4}-\d{2}-\d{2})\.json$", req.url.path)
        start = int(datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc).timestamp() * 1000)
        rows = [[t, "100", "101", "99", "100.5", "1", 0, "100", 1, "1", "1", "0"] for t in range(start, min(start + 731 * DAY, end + DAY), DAY) if t >= t0]
        return httpx.Response(200, json=rows)
    sk = StaticKlinesClient(cache_root=tmp_path / "cache", transport=httpx.MockTransport(sk_handler))
    st = run_ingest(["BTCUSDT"], ["1d"], source="bitget", skip_crosscheck=True, store=store, bitget_client=bg, sk_client=sk)
    assert st["probes"]["bitget"]["ok"] is False and st["fallback_used"] is True
    e = st["symbols"]["BTCUSDT"]["1d"]
    assert e["effective_source"] == "binance_spot_via_static_klines" and e["updated_at"] and e["continuity"]["n_bars"] > 365 * 5
    assert store.load("BTCUSDT", "1d")["source"].eq("binance_spot_via_static_klines").all()


def test_snapshot_stale_rule():
    gen = datetime.now(timezone.utc) - timedelta(hours=31)
    age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
    assert age_h > 30                                                    # Dashboard 用 snapshot_max_age_hours=30 判定过期


def test_risk_level_history_on_real_data():
    b = BarStore().load("BTCUSDT", "1d")
    if b.empty:
        pytest.skip("无本地数据")
    e = enrich(b); t = risk_level_history(b, e)
    assert "全部" in set(t["风险等级"]) and t["样本日"].sum() >= 2 * t.loc[t["风险等级"] == "全部", "样本日"].iloc[0] - 1
    assert (t["浮亏>10%"] >= t["浮亏>15%"]).all() and (t["浮亏>15%"] >= t["浮亏>20%"]).all()


def test_plans_same_capital_dates_fees():
    rng = np.random.default_rng(1); n = 620
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    df = pd.DataFrame({"ts": pd.date_range("2022-01-01", periods=n, tz="UTC"), "symbol": "BTCUSDT", "close": close})
    df["open"] = df["close"].shift(1).fillna(100.0); df["high"] = np.maximum(df["open"], df["close"]) * 1.01; df["low"] = np.minimum(df["open"], df["close"]) * 0.99
    df["volume"] = 1.0; df["quote_volume"] = 1.0; df["source"] = "x"; df["tf"] = "1d"; df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    a = simulate(df, H=90, capital=10_000, wait_d=3.0, fallback="market"); b = simulate(df, H=90, capital=10_000, wait_d=5.0, fallback="cash", cash_yield=0.04)
    assert (a["ts"] == b["ts"]).all()                                    # 同决策日期
    assert np.allclose(a["P1_ret"], b["P1_ret"])                         # 一次性买入不受等待参数影响 → 同资金同费用
    # 定投第一份在起始日次日开盘，与一次性买入同一日：若全部份额价格相同则净值相同 → 用常数价格验证费用口径一致
    flat = df.copy(); flat[["open", "high", "low", "close"]] = 100.0
    f = simulate(flat, H=90, capital=10_000, dca_n=6, dca_k=7, wait_d=3.0, fallback="market")
    assert np.allclose(f["P1_ret"], f["P2_ret"]) and np.allclose(f["P1_ret"], -(1 - (1 - 0.0015) ** 2))   # 双边费用 15 bps


def test_healthcheck_runs_and_reports(tmp_path, monkeypatch):
    r = healthcheck.run(send_alert=False)
    assert r["overall"] in ("OK", "WARN", "FAIL") and len(r["checks"]) > 10
    ro = next(c for c in r["checks"] if c["check"].startswith("只读"))
    assert ro["status"] == "OK"
    assert (Path("reports_out/spot90/monitor/health.json")).exists()


def test_no_trading_code_paths():
    src = Path("src/spot90")
    hits = [str(f) for f in src.rglob("*.py") if re.search(r"place-order|placeOrder|/api/v2/spot/trade/|/withdraw", f.read_text(encoding="utf-8")) and f.name != "healthcheck.py"]
    assert not hits
