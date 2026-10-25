"""Glue: build real events (Snapshot) + real price series (CoinGecko) and run
the event-edge estimator. Produces a confirmed-edge model keyed by token
symbol, or INSUFFICIENT_DATA when real data is unavailable (never fabricated).
"""
from __future__ import annotations
from typing import List, Optional

from .sources import SnapshotSource, CoinGeckoSource
from .edge import Event, estimate
from .provenance import OK


def _cg_series(cg: CoinGeckoSource, coingecko_id: str):
    def prov(asset, frm, to):
        rec = cg.market_chart_range(coingecko_id, "usd", int(frm), int(to))
        if not rec.ok or not isinstance(rec.data, dict):
            return []
        prices = rec.data.get("prices") or []
        vols = {int(v[0] // 1000): v[1] for v in (rec.data.get("total_volumes") or [])}
        return [(int(ms // 1000), float(px), vols.get(int(ms // 1000)))
                for ms, px in prices]
    return prov


def build_and_estimate(cfg: dict) -> dict:
    ecfg = cfg.get("edge", {})
    horizons = [int(h) * 3600 for h in ecfg.get("horizons_h", [24, 72, 168])]
    cost_bps = float(ecfg.get("edge_cost_bps", 50))
    min_n = int(ecfg.get("min_events", 8))
    t_thr = float(ecfg.get("t_threshold", 1.65))

    snap = SnapshotSource()
    cg = CoinGeckoSource()

    events: List[Event] = []
    fetch = {"snapshot_unreachable": 0, "spaces": []}
    for dao in cfg.get("daos", []):
        sym = dao.get("token_symbol")
        rec = snap.recent_proposals(dao["snapshot_space"], first=50)
        status = "OK" if rec.ok else "UNAVAILABLE"
        fetch["spaces"].append({"space": dao["snapshot_space"], "status": status,
                                "error": rec.error})
        if not rec.ok or not isinstance(rec.data, dict):
            fetch["snapshot_unreachable"] += 1
            continue
        for p_ in (rec.data.get("data", {}).get("proposals") or []):
            if not sym or not p_.get("end"):
                continue
            etype = f"proposal_{p_.get('state','?')}"
            events.append(Event(ts=int(p_["end"]), asset=sym, event_type=etype))

    if not events:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "无法获取真实治理事件（Snapshot 不可达或无提案），不以模拟事件替代。",
                "fetch": fetch, "events": 0, "results": [], "edge_model": {}}

    # per-DAO series providers (benchmark = ETH excess)
    id_by_symbol = {d.get("token_symbol"): d.get("coingecko_id") for d in cfg.get("daos", [])}
    bench_id = ecfg.get("benchmark_coingecko_id", "ethereum")
    bench_series = _cg_series(cg, bench_id)

    def series_provider(asset, frm, to):
        cid = id_by_symbol.get(asset)
        if not cid:
            return []
        return _cg_series(cg, cid)(asset, frm, to)

    def benchmark_provider(t0, t1, frm, to):
        bs = bench_series(bench_id, frm, to)
        if not bs:
            return None
        from .edge import price_at_or_before, price_at_or_after
        p0 = price_at_or_before(bs, t0); p1 = price_at_or_after(bs, t1)
        if p0 and p1 and p0 > 0:
            return p1 / p0 - 1.0
        return None

    results = estimate(events, series_provider, horizons=horizons, cost_bps=cost_bps,
                       benchmark_provider=benchmark_provider, min_n=min_n,
                       t_threshold=t_thr)
    # confirmed-edge model: best confirmed horizon per symbol
    edge_model = {}
    for r in results:
        if r.confirmed and r.edge_bps is not None:
            cur = edge_model.get(r.asset)
            if cur is None or r.edge_bps > cur["edge_bps"]:
                edge_model[r.asset] = {"edge_bps": r.edge_bps, "horizon_s": r.horizon_s,
                                       "event_type": r.event_type,
                                       "in_sample_n": r.in_sample.get("n"),
                                       "oos_n": r.out_of_sample.get("n")}
    priced = sum(1 for r in results if r.status == "OK")
    return {"status": "OK" if priced else "INSUFFICIENT_DATA",
            "reason": "" if priced else "无真实历史价格（CoinGecko 不可达）",
            "fetch": fetch, "events": len(events),
            "results": [r.to_dict() for r in results], "edge_model": edge_model}
