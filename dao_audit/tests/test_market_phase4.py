"""Deterministic tests for paper-trading recorder and acceptance gate.
Synthetic inputs test engine logic only; never used as real market data."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.market.paper import PaperBook  # noqa: E402
from dao_audit.market import accept as ACCEPT  # noqa: E402


def test_paper_fill_deviation_and_metrics():
    bk = PaperBook()
    t = bk.open_trade("AAVE", "long", quote_price=100.0, edge_bps=80,
                      slippage_bps=10, cost_bps=50)
    assert round(t.fill_price, 4) == 100.1      # adverse slippage applied
    assert round(t.exec_deviation_bps, 2) == 10.0
    bk.close_trade(t, exit_price=102.0)
    assert t.realized_bps is not None and t.net_bps is not None
    assert t.net_bps < t.realized_bps           # costs+deviation subtracted
    m = bk.metrics()
    assert m["n_closed"] == 1 and m["win_rate"] == 1.0


def test_paper_drawdown_tracks_losses():
    bk = PaperBook()
    t1 = bk.open_trade("X", "long", 100, 50, 0, 0); bk.close_trade(t1, 101)   # +100bps
    t2 = bk.open_trade("X", "long", 100, 50, 0, 0); bk.close_trade(t2, 97)    # -300bps
    m = bk.metrics()
    assert m["max_drawdown_bps"] < 0 and m["win_rate"] == 0.5


def test_accept_all_blocked_when_no_reports(tmp_path):
    r = ACCEPT.evaluate(tmp_path)
    assert r["overall"] == "BLOCKED"
    assert r["first_blocking_phase"].startswith("1")
    assert all(p["status"] == "BLOCKED" for p in r["phases"])


def test_accept_all_pass_with_real_evidence(tmp_path):
    (tmp_path / "connection_report.json").write_text(json.dumps(
        {"overall": "OK", "ok": 8, "total": 8, "denied_hosts": []}), encoding="utf-8")
    (tmp_path / "edge_report.json").write_text(json.dumps(
        {"status": "OK", "events": 40, "edge_model": {"AAVE": {"edge_bps": 60}}}), encoding="utf-8")
    (tmp_path / "market_scan.json").write_text(json.dumps(
        {"summary": {"trade_signals": 1}}), encoding="utf-8")
    (tmp_path / "paper_book.json").write_text(json.dumps(
        {"trades": [{"symbol": "AAVE"}]}), encoding="utf-8")
    r = ACCEPT.evaluate(tmp_path)
    assert r["overall"] == "PASS", r
    assert all(p["status"] == "PASS" for p in r["phases"])
