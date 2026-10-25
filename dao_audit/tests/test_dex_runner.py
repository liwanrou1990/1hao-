"""Deterministic tests for the continuous-monitor persistence + acceptance
aggregation (no network). Synthetic rows exercise lifetime/rate/verdict logic."""
import sys, sqlite3, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.dex.runner import _connect, acceptance_from_db  # noqa: E402


def _seed(db, net_sequence):
    con = _connect(db)
    con.execute("INSERT OR REPLACE INTO meta VALUES('run_started','2026-01-01T00:00:00+00:00')")
    con.execute("INSERT OR REPLACE INTO meta VALUES('run_last','2026-01-01T01:00:00+00:00')")
    con.execute("INSERT INTO blocks VALUES('base',1,'t')")
    con.execute("INSERT INTO blocks VALUES('base',2,'t')")
    for i, net in enumerate(net_sequence, 1):
        con.execute("INSERT INTO scans(started_at,wall_ms,blocknum_rpc_ms,chains,n_pairs,"
                    "n_positive_spread,n_positive_net,eth_price) VALUES(?,?,?,?,?,?,?,?)",
                    ("t", 100.0, 20.0, "base", 1, 1 if net > 0 else 0, 1 if net > 0 else 0, 2400))
        sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sid, "base", i, "WETH/USDC", 1000, net + 0.2, 0.2, net, 1 if net+0.2 > 0 else 0, "t"))
    con.commit(); con.close()


def test_acceptance_no_trade_when_all_negative():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "m.db")
        _seed(db, [-0.6, -0.5, -0.7])
        rep = acceptance_from_db(db)
        assert rep["verdict"] == "NO_TRADE"
        assert rep["positive_net_count"] == 0
        assert rep["blocks_observed"] == 2
        assert rep["valid_scans"] == 3
        assert rep["continuous_seconds_observed"] == 3600.0
        assert rep["fork_verified_count"] == 0 and rep["mainnet_realized_count"] == 0


def test_acceptance_lifetime_and_positive_counting():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "m.db")
        _seed(db, [0.5, 0.5, -0.1, 0.5])  # longest positive run = 2
        rep = acceptance_from_db(db)
        assert rep["verdict"] == "HAS_POSITIVE_NET"
        assert rep["positive_net_count"] == 3
        life = rep["opportunity_lifetime"][0]
        assert life["max_consecutive_positive_scans"] == 2
