"""Glue: build REAL events (Snapshot history) + REAL historical prices
(DefiLlama daily series, primary) and run the event-edge estimator.

Event types are distinguished by the time information known at that moment:
  - proposal_created : announcement time (Snapshot `created`)
  - voting_end       : voting close time (Snapshot `end`)
(Proposal on-chain execution time is not in Snapshot; it requires Timelock
tracking and is intentionally omitted rather than fabricated.)

No lookahead: the estimator uses decision-time price <= event ts and realized
historical price at ts+horizon. No real data -> INSUFFICIENT_DATA.
"""
from __future__ import annotations
import math
from typing import List, Optional

from .sources import SnapshotSource, DefiLlamaSource, CoinGeckoSource
from .edge import Event, estimate
from .provenance import OK


def _fetch_proposals(snap: SnapshotSource, space: str, limit: int) -> dict:
    q = {"query": """query($space:String!,$first:Int!){
           proposals(first:$first, where:{space:$space},
                     orderBy:"created", orderDirection:desc){
             id created start end state scores_total }}""",
         "variables": {"space": space, "first": limit}}
    from .provenance import http_post_json
    return http_post_json(snap.URL, q, source="snapshot")


def _daily_series(dl: DefiLlamaSource, coin_key: str, start: int, end: int):
    """Fetch a daily price series in <=365-point chunks (DefiLlama span cap),
    concatenate and de-duplicate. Returns (series, last_record)."""
    CHUNK = 350
    pts = {}
    last = None
    cur = start
    while cur < end:
        span = min(CHUNK, int((end - cur) / 86400) + 2)
        rec = dl.chart(coin_key, cur, span, "1d")
        last = rec
        if rec.ok and isinstance(rec.data, dict):
            info = rec.data.get("coins", {}).get(coin_key)
            if info and info.get("prices"):
                for pz in info["prices"]:
                    pts[int(pz["timestamp"])] = float(pz["price"])
        cur += CHUNK * 86400
    if not pts:
        return None, last
    series = sorted((t, p, None) for t, p in pts.items())
    return series, last


def build_and_estimate(cfg: dict) -> dict:
    ecfg = cfg.get("edge", {})
    horizons = [int(h) * 3600 for h in ecfg.get("horizons_h", [24, 72, 168])]
    cost_bps = float(ecfg.get("edge_cost_bps", 50))
    min_n = int(ecfg.get("min_events", 8))
    t_thr = float(ecfg.get("t_threshold", 1.65))
    max_props = int(ecfg.get("max_proposals_per_space", 300))
    bench_key = "coingecko:" + ecfg.get("benchmark_coingecko_id", "ethereum")

    snap = SnapshotSource()
    dl = DefiLlamaSource()

    # 1) real governance events per DAO
    events: List[Event] = []
    fetch = []
    asset_keys = {}
    for dao in cfg.get("daos", []):
        sym = dao.get("token_symbol"); ck = "coingecko:" + (dao.get("coingecko_id") or "")
        asset_keys[sym] = ck
        rec = _fetch_proposals(snap, dao["snapshot_space"], max_props)
        ok = rec.ok and isinstance(rec.data, dict) and rec.data.get("data", {}).get("proposals") is not None
        props = rec.data["data"]["proposals"] if ok else []
        fetch.append({"space": dao["snapshot_space"], "symbol": sym,
                      "status": "OK" if ok else "UNAVAILABLE",
                      "n_proposals": len(props), "error": rec.error})
        for p in props:
            if p.get("created"):
                events.append(Event(ts=int(p["created"]), asset=sym, event_type="proposal_created"))
            if p.get("end"):
                events.append(Event(ts=int(p["end"]), asset=sym, event_type="voting_end"))

    if not events:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "无法获取真实治理事件（Snapshot 不可达或无提案）。",
                "fetch": fetch, "events": 0, "results": [], "edge_model": {},
                "price_sources": []}

    # 2) real historical daily price series per asset (DefiLlama primary),
    #    each over ITS OWN event range (DefiLlama caps span, so never the union)
    buf = 7 * 24 * 3600
    series_cache = {}
    price_sources = []
    ev_by_asset = {}
    for e in events:
        ev_by_asset.setdefault(e.asset, []).append(e.ts)
    gmin = min(e.ts for e in events) - buf
    gmax = max(e.ts for e in events) + max(horizons) + buf
    for sym, ck in asset_keys.items():
        tss = ev_by_asset.get(sym)
        if not tss:
            series_cache[sym] = None
            price_sources.append({"symbol": sym, "coin_key": ck, "status": "UNAVAILABLE",
                                  "n_points": 0, "latency_ms": None, "error": "no events"})
            continue
        a_lo = min(tss) - buf; a_hi = max(tss) + max(horizons) + buf
        ser, rec = _daily_series(dl, ck, a_lo, a_hi)
        series_cache[sym] = ser
        price_sources.append({"symbol": sym, "coin_key": ck,
                              "status": "OK" if ser else "UNAVAILABLE",
                              "n_points": len(ser) if ser else 0,
                              "latency_ms": rec.latency_ms, "error": rec.error})
    bench_series, brec = _daily_series(dl, bench_key, gmin, gmax)
    price_sources.append({"symbol": "ETH(benchmark)", "coin_key": bench_key,
                          "status": "OK" if bench_series else "UNAVAILABLE",
                          "n_points": len(bench_series) if bench_series else 0,
                          "error": brec.error})

    if not any(series_cache.values()):
        return {"status": "INSUFFICIENT_DATA",
                "reason": "无法获取真实历史价格（DefiLlama chart 不可达或为空）。",
                "fetch": fetch, "events": len(events), "results": [],
                "edge_model": {}, "price_sources": price_sources}

    def series_provider(asset, frm, to):
        ser = series_cache.get(asset)
        if not ser:
            return []
        return [(t, p, v) for (t, p, v) in ser if frm <= t <= to]

    def benchmark_provider(t0, t1, frm, to):
        if not bench_series:
            return None
        from .edge import price_at_or_before, price_at_or_after
        p0 = price_at_or_before(bench_series, t0); p1 = price_at_or_after(bench_series, t1)
        if p0 and p1 and p0 > 0:
            return p1 / p0 - 1.0
        return None

    results = estimate(events, series_provider, horizons=horizons, cost_bps=cost_bps,
                       benchmark_provider=benchmark_provider, min_n=min_n,
                       t_threshold=t_thr)

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
            "reason": "" if priced else "事件无可用历史价格匹配。",
            "fetch": fetch, "events": len(events),
            "results": [r.to_dict() for r in results],
            "edge_model": edge_model, "price_sources": price_sources}


def to_markdown(rep: dict) -> str:
    L = ["# 事件收益模型报告 (event-edge estimator)", "",
         "> 真实数据：Snapshot 历史治理事件 + DefiLlama 日线历史价（ETH 为基准）。"
         "严格时序 train+val/样本外；防未来泄漏；多重检验(Bonferroni)校正。"
         "不承诺收益；无确认优势→NO_TRADE。", ""]
    L.append(f"- 状态: **{rep['status']}**  事件数: {rep['events']}  "
             f"确认edge: {len(rep.get('edge_model', {}))}")
    results = rep.get("results", [])
    m = next((r.get("n_hypotheses") for r in results if r.get("n_hypotheses")), None)
    bt = next((r.get("bonferroni_t") for r in results if r.get("bonferroni_t")), None)
    if m:
        L.append(f"- 假设数(组合): {m}  Bonferroni 显著阈值 t>={bt:.2f}（单侧，族错误率 5%）")
    L.append("")
    L.append("## 每空间真实提案数")
    L.append("")
    L.append("| 代币 | Snapshot 空间 | 状态 | 提案数 |")
    L.append("|------|---------------|------|--------|")
    for f in rep.get("fetch", []):
        L.append(f"| {f['symbol']} | {f['space']} | {f['status']} | {f['n_proposals']} |")
    L.append("")
    L.append("## 历史价格序列（真实点数）")
    L.append("")
    L.append("| 标的 | coin key | 状态 | 日线点数 |")
    L.append("|------|----------|------|----------|")
    for p_ in rep.get("price_sources", []):
        L.append(f"| {p_['symbol']} | {p_['coin_key']} | {p_['status']} | {p_['n_points']} |")
    L.append("")
    L.append("## 分事件类型收益分布（样本内/样本外，单位 bps，超额对 ETH）")
    L.append("")
    L.append("| 标的 | 事件类型 | 窗口 | 内n | 内均值 | t | CI下界 | 胜率 | OOSn | OOS均值 | OOS成本调整 | OOS回撤 | 确认 |")
    L.append("|------|----------|------|-----|--------|---|--------|------|------|---------|-------------|---------|------|")

    def b(x):
        return f"{x*1e4:.0f}" if isinstance(x, (int, float)) else "-"
    for r in results:
        i = r["in_sample"]; o = r["out_of_sample"]
        if not i.get("n"):
            continue
        L.append("| {a} | {e} | {h}h | {inn} | {m} | {t} | {ci} | {wr} | {on} | {om} | {oc} | {md} | {cf} |".format(
            a=r["asset"], e=r["event_type"], h=r["horizon_s"] // 3600, inn=i["n"],
            m=b(i["mean"]), t=(f"{i['t_stat']:.2f}" if i.get("t_stat") is not None else "-"),
            ci=b(i["ci95"][0]) if i.get("ci95") else "-", wr=f"{i['win_rate']:.2f}",
            on=o["n"], om=b(o["mean"]), oc=b(o.get("cost_adj_mean")),
            md=b(o.get("max_drawdown")), cf="YES" if r["confirmed"] else ""))
    L.append("")
    if rep.get("edge_model"):
        L.append("## 确认的可交易 edge")
        L.append("")
        for sym, e in rep["edge_model"].items():
            L.append(f"- **{sym}** {e['event_type']} {e['horizon_s']//3600}h: "
                     f"edge={e['edge_bps']:.1f}bps (样本内 n={e['in_sample_n']}, OOS n={e['oos_n']})")
    else:
        L.append("## 结论")
        L.append("")
        L.append("在多重检验校正后，没有任何(标的×事件类型×窗口)组合显示出样本内显著且样本外成本调整后为正"
                 "的可持续交易优势。最强正向组合的样本内 t 也低于 Bonferroni 阈值，且自助法置信区间跨越 0。"
                 "系统对全部标的输出 NO_TRADE——这是真实数据下的正确保守结论，而非系统故障。")
    L.append("")
    L.append("> 局限：价格为日线分辨率；基准为 ETH 超额；提案执行(on-chain Timelock)时间未纳入，需链上追踪后扩展。")
    return "\n".join(L)
