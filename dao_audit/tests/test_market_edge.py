"""Deterministic tests for the event-edge estimator (engine arithmetic only).

Synthetic price paths test the statistics and the no-lookahead / confirmation
logic. They are never used as real market data in any report; live estimation
uses real historical series or returns INSUFFICIENT_DATA.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.market.edge import (  # noqa: E402
    Event, estimate, price_at_or_before, price_at_or_after, realized_vol)

H = 3600  # 1h horizon


def _provider(step_up: float):
    """Series: price is 100 before each event.ts, 100*(1+step_up) at ts+H."""
    def prov(asset, frm, to):
        pts = []
        t = frm
        while t <= to:
            # default 100; bump to the stepped price once we pass an event+H point
            pts.append((t, 100.0, 1000.0))
            t += 600  # 10-min candles
        return pts
    return prov


def _event_provider(returns_by_event):
    """Global series with a localized bump per event that returns to baseline
    before the next event (gap > 2H), so each event's forward return is exactly
    r_i and independent of neighbors: (ts,100) (ts+H,100*(1+r)) (ts+2H,100)."""
    anchors = []
    for ev_ts, r in sorted(returns_by_event.items()):
        anchors.append((ev_ts, 100.0, 1000.0))
        anchors.append((ev_ts + H, 100.0 * (1.0 + r), 1000.0))
        anchors.append((ev_ts + 2 * H, 100.0, 1000.0))
    anchors.sort()
    def prov(asset, frm, to):
        return [(t, p, v) for (t, p, v) in anchors if frm <= t <= to]
    return prov


def _events(n, etype="PASS", asset="UP", start=1_000_000, gap=10_000):
    return [Event(ts=start + i * gap, asset=asset, event_type=etype) for i in range(n)]


def test_no_data_is_insufficient():
    evs = _events(10)
    res = estimate(evs, lambda a, f, t: [], horizons=[H], cost_bps=20)
    assert len(res) == 1 and res[0].status == "INSUFFICIENT_DATA"
    assert res[0].edge_bps is None


def test_positive_edge_confirmed_out_of_sample():
    evs = _events(24)
    rmap = {e.ts: 0.01 for e in evs}  # +1.00% each event
    res = estimate(evs, _event_provider(rmap), horizons=[H], cost_bps=20,
                   min_n=8, t_threshold=1.65)[0]
    assert res.status == "OK"
    assert res.confirmed is True, res.reason
    # 1.00% gross - 0.20% cost = 0.80% = 80 bps
    assert res.edge_bps is not None and 70 <= res.edge_bps <= 90


def test_subcost_edge_not_confirmed():
    evs = _events(24)
    rmap = {e.ts: 0.001 for e in evs}  # +0.10% < 0.20% cost
    res = estimate(evs, _event_provider(rmap), horizons=[H], cost_bps=20)[0]
    assert res.confirmed is False
    assert res.edge_bps is None


def test_zero_mean_edge_not_confirmed():
    evs = _events(24)
    rmap = {}
    for i, e in enumerate(evs):
        rmap[e.ts] = 0.01 if i % 2 == 0 else -0.01  # alternating +1%/-1%
    res = estimate(evs, _event_provider(rmap), horizons=[H], cost_bps=20)[0]
    assert res.confirmed is False


def test_no_lookahead_price_selection():
    # decision price must ignore a future spike at ts+2H
    series = [(100, 100.0, 1), (200, 100.0, 1), (300, 500.0, 1)]
    assert price_at_or_before(series, 250) == 100.0   # not the 500 spike at 300
    assert price_at_or_after(series, 250) == 500.0    # first obs >= 250
    assert realized_vol([(0,100.0,1),(60,101.0,1),(120,100.5,1),(180,101.5,1)],
                        180, 1000) is not None


def test_chronological_split_oos_is_later():
    evs = _events(20)
    rmap = {e.ts: 0.01 for e in evs}
    res = estimate(evs, _event_provider(rmap), horizons=[H], cost_bps=20)[0]
    # in-sample + oos counts add up to total priced events
    assert res.in_sample["n"] + res.out_of_sample["n"] == 20
