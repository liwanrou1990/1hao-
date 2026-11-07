"""模拟盘稳定性：实时能力判定（公开缓存永远不算实时）、数据异常检测、熔断、日统计、重启恢复后订单号不重复（见 test_brando_pa_paper）。"""
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.engine.context import MarketContext
from brando_pa.paper.runner import PaperRunner
from brando_pa.risk.limits import RiskState

HAVE = Path("data_store/brando_pa/bars/ETHUSDT_15m.parquet").exists()


def _runner(tmp_path, n=1500):
    c = cfg(); c["paper"]["state_file"] = str(tmp_path / "state.json"); c["paper"]["log_file"] = str(tmp_path / "trades.jsonl"); c["paper"]["lock_file"] = str(tmp_path / ".lock")
    st = BarStore(); bars = {tf: st.load("ETHUSDT", tf) for tf in ("1w", "1d", "4h", "15m")}; bars["15m"] = bars["15m"].iloc[-n:].reset_index(drop=True)
    r = PaperRunner(["ETHUSDT"], "15m", 10000.0, c, refresh_data=False); r.contexts = {"ETHUSDT": MarketContext("ETHUSDT", bars, "15m", c)}
    return r


@pytest.mark.skipif(not HAVE, reason="无本地数据")
def test_realtime_status_false_on_static_cache_and_issue_detection(tmp_path):
    r = _runner(tmp_path)
    last = r.contexts["ETHUSDT"].ts.iloc[-1]
    fresh_now = last + pd.Timedelta(minutes=20)
    rs = r.realtime_status(fresh_now)
    assert rs["realtime_capable"] is False and rs["exchange_realtime_source"] is False and "不具备实时" in rs["label"]   # 公开缓存即使新鲜也不算实时
    assert r.detect_data_issues(fresh_now) == []
    issues = r.detect_data_issues(last + pd.Timedelta(days=2))
    assert any("过期" in x for x in issues)
    # 人为制造缺口与跳变
    mc = r.contexts["ETHUSDT"]; ex = mc.ex.copy()
    ex.loc[ex.index[-50], "close"] = ex["close"].iloc[-51] * 1.5
    ex = ex.drop(ex.index[-100:-97]).reset_index(drop=True)
    mc.ex = ex
    issues = r.detect_data_issues(fresh_now)
    assert any("缺口" in x for x in issues) and any("跳变" in x for x in issues)


@pytest.mark.skipif(not HAVE, reason="无本地数据")
def test_circuit_breakers_and_daily_stats(tmp_path):
    r = _runner(tmp_path)
    st = r.state.data
    rs = RiskState(equity=10000.0); rs.roll_day("2025-01-01")
    assert r.circuit_breakers(rs, 10000.0) == []
    st["peak_equity"] = 10000.0
    cb = r.circuit_breakers(rs, 8900.0)
    assert any("峰值回撤" in x for x in cb)
    st["consecutive_losses"] = 9
    assert any("连续亏损" in x for x in r.circuit_breakers(rs, 9950.0))
    st["data_issues"] = ["ETHUSDT 最近 200 根 15m 有 1 处缺口"]
    assert any("数据异常" in x for x in r.circuit_breakers(rs, 9990.0))
    st["equity"] = 9990.0; st["cash"] = 9990.0
    r.daily_stats("2025-01-01")
    p = r.trade_log.parent / "daily_stats.jsonl"
    assert p.exists() and '"day": "2025-01-01"' in p.read_text()


@pytest.mark.skipif(not HAVE, reason="无本地数据")
def test_run_once_reports_realtime_and_mode(tmp_path):
    r = _runner(tmp_path, n=1200)
    rep = r.run_once(now=r.contexts["ETHUSDT"].ts.iloc[-1] + pd.Timedelta(minutes=20), contexts=r.contexts)
    assert rep["realtime"]["realtime_capable"] is False and r.state.data["mode"] == "paper_delayed_data"
