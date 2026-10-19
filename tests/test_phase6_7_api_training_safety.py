"""阶段六/七：API 工作台、训练模式、执行安全。"""
import os

import pytest
from fastapi.testclient import TestClient

from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.execution.guard import ExecutionForbidden, ExecutionGuard
from bgw_quant.training.session import TrainingSession


def test_execution_guard_blocks_everything_in_v1(monkeypatch):
    g = ExecutionGuard.from_env()
    assert g.mode == "research_only" and g.kill_switch
    with pytest.raises(ExecutionForbidden):
        g.check_live_order(100)
    st = g.status()
    assert st["live_trading_possible"] is False and st["wallet_connected"] is False and st["private_key_requested"] is False
    monkeypatch.setenv("BGWQ_EXECUTION_MODE", "live")
    from bgw_quant import config
    config.get_settings.cache_clear()
    g2 = ExecutionGuard.from_env()
    assert g2.mode == "research_only" and any("强制回退" in a["event"] for a in g2.audit)
    config.get_settings.cache_clear()
    monkeypatch.setenv("MY_WALLET_PRIVATE_KEY", "x")
    assert "MY_WALLET_PRIVATE_KEY" in g2.assert_no_private_material_in_env()


def test_training_session_hides_future_and_scores(nvda_day):
    cm = CostModel(load_cost_profile("token_dex"))
    s = TrainingSession.start(nvda_day, cm, "tester", 10_000, "token_dex", warmup=30)
    assert len(s.visible_bars()) == 31                      # 只看到热身K线
    s.decide("buy")
    v = s.advance()
    assert v["cursor"] == 31 and s.state.position == 1
    assert s.state.decisions[-1]["exec_price"] == pytest.approx(nvda_day["open"].iloc[31])  # 下根开盘成交
    for _ in range(20):
        s.advance()
    assert len(s.visible_bars()) == 52
    s.decide("sell")
    s.advance()
    assert s.state.position == 0
    score = s.finish()
    assert s.state.finished and "grade" in score and score["comparison"]["trader"]["n_trades"] == 1
    assert score["comparison"]["hindsight"]["net_return"] >= score["comparison"]["trader"]["net_return"]
    with pytest.raises(RuntimeError):
        s.decide("buy")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "t.duckdb"
    os.environ["BGWQ_DB_PATH"] = str(db)
    os.environ["BGWQ_REPORT_DIR"] = str(tmp_path_factory.mktemp("rep"))
    from bgw_quant import config
    config.get_settings.cache_clear()
    from bgw_quant.api import main as m
    from bgw_quant.pipeline import Pipeline
    from datetime import date
    p = Pipeline(probe=False)
    p.ingest(["NVDA"], date(2026, 9, 1), date(2026, 10, 16))
    m._pipeline = p
    yield TestClient(m.app)
    config.get_settings.cache_clear()


def test_api_status_symbols_rankings(client):
    s = client.get("/api/status").json()
    assert s["data_mode"] == "simulated" and s["execution"]["live_trading_possible"] is False
    assert client.get("/api/symbols").json()["tradable"].count("SPCX") == 0
    r = client.get("/api/rankings?day=2026-10-16").json()
    assert r["simulated"] and r["rows"][0]["symbol"] == "NVDA"


def test_api_replay_trades_tiers_capacity(client):
    r = client.get("/api/replay/NVDA?day=2026-10-16").json()
    assert r["candles"] and "markers_a" in r and r["data_is_simulated"]
    assert len(r["equity_b"]) == len(r["candles"])
    t = client.get("/api/trades/NVDA?day=2026-10-16").json()
    assert any(x["mode"] == "A" for x in t["trades"])
    tiers = client.get("/api/tiers").json()
    assert len(tiers["tiers"]) == 7 and tiers["issues"]
    cap = client.get("/api/capacity?symbol=NVDA").json()
    assert len(cap["rows"]) == 21
    assert client.get("/api/execution/status").json()["mode"] == "research_only"
    assert client.get("/api/bars/NVDA?day=2026-10-17").status_code == 404


def test_api_training_flow(client):
    s = client.post("/api/training/start", json={"symbol": "NVDA", "day": "2026-10-16", "trader": "t"}).json()
    sid = s["session_id"]
    assert len(s["candles"]) == 31
    client.post(f"/api/training/{sid}/decide", json={"action": "buy"})
    v = client.post(f"/api/training/{sid}/advance").json()
    assert v["position"] == 1 and len(v["candles"]) == 32
    f = client.post(f"/api/training/{sid}/finish").json()
    assert f["score"]["grade"]
    lb = client.get("/api/training/leaderboard/all").json()
    assert lb["rows"] and lb["rows"][0]["trader"] == "t"


def test_api_backtest_and_report_fast(client):
    bt = client.get("/api/backtest/NVDA?train_days=12&test_days=5").json()
    assert bt["strategies"] and bt["benchmarks"]["no_trade"]
    rep = client.post("/api/report?day=2026-10-16&symbols=NVDA&with_ml=false&with_backtest=false").json()
    assert rep["html_url"].endswith(".html") and rep["csv_url"].endswith(".csv")
    assert client.get(rep["html_url"]).status_code == 200
    assert client.get("/api/reports").json()["items"]
