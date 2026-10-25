"""Deterministic tests for the continuous-monitor persistence + acceptance
aggregation (no network). Synthetic rows exercise lifetime/rate/verdict logic."""
import sys, sqlite3, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.dex.runner import (  # noqa: E402
    _connect, acceptance_from_db, _last_block, _rpc_list, _per_chain_stats,
)


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


def test_block_idempotency_no_double_count_and_resume():
    # Re-observing the SAME block must not duplicate (INSERT OR IGNORE +
    # PRIMARY KEY), and _last_block must resume from the max -> a restart
    # neither double-counts a scanned block nor re-scans it.
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "m.db")
        con = _connect(db)
        for b in (100, 101, 102):
            con.execute("INSERT OR IGNORE INTO blocks VALUES('base',?,'t')", (b,))
        # simulate restart re-seeing already-recorded blocks
        for b in (101, 102, 102):
            con.execute("INSERT OR IGNORE INTO blocks VALUES('base',?,'t')", (b,))
        con.commit()
        total, distinct = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT block) FROM blocks WHERE chain='base'").fetchone()
        assert total == distinct == 3           # no duplicate rows
        assert _last_block(con, "base") == 102   # resume point = max
        con.close()


def test_rpc_failover_list_dedup_and_env(monkeypatch):
    cfg = {"chains": {"base": {"rpc": "https://primary",
                               "rpc_fallbacks": ["https://fb1", "https://primary"]}}}
    assert _rpc_list(cfg, "base") == ["https://primary", "https://fb1"]  # dedup
    monkeypatch.setenv("BASE_RPC_FALLBACK", "https://env1, https://fb1")
    assert _rpc_list(cfg, "base") == ["https://primary", "https://fb1", "https://env1"]


def test_per_chain_stats_missed_ratio_and_staleness():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "m.db")
        con = _connect(db)
        # window spans blocks 100..110 (span 11) but only 3 were scanned
        for b in (100, 105, 110):
            con.execute("INSERT INTO blocks VALUES('base',?,'t')", (b,))
        con.execute("INSERT INTO scans(started_at,wall_ms,chains,n_pairs,n_positive_spread,"
                    "n_positive_net) VALUES('t',500.0,'base',1,0,0)")
        sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for tb, tip in ((100, 102), (105, 108), (110, 113)):  # staleness 2,3,3
            con.execute("INSERT INTO chain_rounds VALUES(?,?,?,?,?,?,?)",
                        (sid, "base", tb, tip, tip - tb, "https://primary", "t"))
        con.commit()
        st = _per_chain_stats(con)[0]
        con.close()
        assert st["window_block_span"] == 11 and st["scanned_blocks"] == 3
        assert st["missed_blocks"] == 8
        assert abs(st["missed_block_ratio"] - 8 / 11) < 1e-3  # stored rounded to 4dp
        assert st["max_staleness_blocks"] == 3
        assert abs(st["avg_staleness_blocks"] - (2 + 3 + 3) / 3) < 1e-2
