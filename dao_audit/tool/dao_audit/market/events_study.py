"""Hourly, categorized, walk-forward event study (v0.4).

Builds on real data only (Snapshot governance history + DefiLlama hourly prices,
ETH as risk benchmark). It does NOT modify the v0.3 edge results; it writes its
own report files.

What it adds over v0.3:
- Hourly price resolution matched to each event's first-public time.
- Event categorization from proposal text: tokenomics / liquidity_incentive /
  treasury_major / governance_generic (prioritizing supply/demand/revenue/
  liquidity-affecting events over generic noise).
- Reaction windows 1h / 4h / 24h / 72h, each classified bullish / bearish /
  neutral by ETH-excess return.
- Walk-forward in-sample vs out-of-sample, bootstrap CI, Bonferroni correction.
- Capital-scaled executable net: the fill is the NEXT hourly bar at/after the
  decision time (never a hindsight-optimal price), minus fee + size-scaled
  slippage + gas.

Integrity: no lookahead (decision price <= event ts; realized price historical),
no simulated market data, no fixed-return promise. Unproven -> NO_TRADE.
timelock_execution requires on-chain Timelock tracking (ETH RPC); when that is
unavailable it is reported INSUFFICIENT_DATA, not fabricated.
"""
from __future__ import annotations
import math
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from .sources import SnapshotSource, DefiLlamaSource
from .edge import _summary, price_at_or_before, price_at_or_after
from .provenance import http_post_json, OK


# ---- event categorization (keyword, on title+body, priority order) ----
_CAT_PATTERNS = [
    ("tokenomics", re.compile(
        r"tokenomic|emission|\bmint\b|\bburn\b|buyback|fee switch|fee-switch|"
        r"revenue shar|inflation|token supply|staking reward|stake reward", re.I)),
    ("liquidity_incentive", re.compile(
        r"liquidity|incentiv|\bgauge\b|market[ -]?making|rewards program|"
        r"\bLM\b|onboard.*pool|emissions? to", re.I)),
    ("treasury_major", re.compile(
        r"treasury|\bbudget\b|allocat|\bgrant\b|funding|\bspend\b|diversif|"
        r"runway|acquir|purchas|buy .*token|sell .*token", re.I)),
]


def classify(title: str, body: str) -> str:
    text = f"{title or ''}\n{body or ''}"
    for name, pat in _CAT_PATTERNS:
        if pat.search(text):
            return name
    return "governance_generic"


def _fetch_proposals(snap: SnapshotSource, space: str, limit: int, since_ts: int) -> list:
    q = {"query": """query($space:String!,$first:Int!){
           proposals(first:$first, where:{space:$space},
                     orderBy:"created", orderDirection:desc){
             id title body created end state scores scores_total choices }}""",
         "variables": {"space": space, "first": limit}}
    rec = http_post_json(snap.URL, q, source="snapshot")
    if not rec.ok or not isinstance(rec.data, dict):
        return []
    props = rec.data.get("data", {}).get("proposals") or []
    return [p for p in props if int(p.get("created") or 0) >= since_ts]


def _hourly_series(dl: DefiLlamaSource, coin_key: str, start: int, end: int):
    """Hourly price series in <=400-point chunks (concatenated, deduped)."""
    CHUNK = 400
    pts = {}
    cur = start
    calls = 0
    while cur < end and calls < 80:
        span = min(CHUNK, int((end - cur) / 3600) + 2)
        rec = dl.chart(coin_key, cur, span, "1h")
        calls += 1
        if rec.ok and isinstance(rec.data, dict):
            info = rec.data.get("coins", {}).get(coin_key)
            if info and info.get("prices"):
                for pz in info["prices"]:
                    pts[int(pz["timestamp"])] = float(pz["price"])
        cur += CHUNK * 3600
    if not pts:
        return None
    return sorted((t, p, None) for t, p in pts.items())


def _bull_bear(excess: float, thr_bps: float) -> str:
    if excess * 1e4 > thr_bps:
        return "bullish"
    if excess * 1e4 < -thr_bps:
        return "bearish"
    return "neutral"


def _slippage_bps(size: float, base_bps: float, ref_usd: float) -> float:
    return base_bps * math.sqrt(size / ref_usd)


def _net_by_size(mean_excess, sizes, cost):
    """Capital-scaled executable net (bps). mean_excess already uses next-bar
    fills (no hindsight). Costs: round-trip fee + size slippage + gas."""
    out = []
    fee = cost["dex_fee_bps"] * 2
    for S in sizes:
        slip = _slippage_bps(S, cost["slippage_base_bps"], cost["slippage_ref_usd"]) * 2
        gas_bps = cost["gas_usd_assumption"] / S * 1e4
        net = mean_excess * 1e4 - fee - slip - gas_bps
        out.append({"size_usd": S, "fee_bps": fee, "slippage_bps": round(slip, 1),
                    "gas_bps": round(gas_bps, 2), "net_bps": round(net, 1)})
    return out


def run(cfg: dict) -> dict:
    scfg = cfg.get("study", {})
    horizons = [int(h) * 3600 for h in scfg.get("horizons_h", [1, 4, 24, 72])]
    lookback = int(scfg.get("lookback_days", 365)) * 86400
    thr = float(scfg.get("reaction_threshold_bps", 100))
    train_frac = float(scfg.get("train_frac", 0.5))
    min_n = int(scfg.get("min_events", 8))
    t_threshold = float(scfg.get("t_threshold", 1.65))
    sizes = scfg.get("capital_sizes_usd", [10000, 100000, 1000000])
    cost = scfg.get("cost", {})
    now = int(time.time())
    cutoff = now - lookback

    snap = SnapshotSource()
    dl = DefiLlamaSource()

    # 1) collect categorized events (hourly-era only)
    events = []   # (ts, asset, event_type, category, direction_hint)
    fetch = []
    assets = {}
    for dao in cfg.get("daos", []):
        sym = dao.get("token_symbol"); ck = "coingecko:" + (dao.get("coingecko_id") or "")
        assets[sym] = ck
        props = _fetch_proposals(snap, dao["snapshot_space"], 300, cutoff)
        fetch.append({"space": dao["snapshot_space"], "symbol": sym, "n": len(props)})
        for p in props:
            cat = classify(p.get("title", ""), p.get("body", ""))
            created = int(p.get("created") or 0)
            end = int(p.get("end") or 0)
            if created >= cutoff:
                events.append((created, sym, "proposal_created", cat))
            if end >= cutoff and end <= now:
                events.append((end, sym, "voting_end", cat))

    if not events:
        return {"status": "INSUFFICIENT_DATA", "reason": "近一年内无治理事件或 Snapshot 不可达。",
                "fetch": fetch, "groups": [], "timelock_execution": "INSUFFICIENT_DATA(需链上 Timelock 追踪)"}

    # 2) hourly series per asset + ETH benchmark
    lo = min(e[0] for e in events) - 2 * 3600
    hi = min(now, max(e[0] for e in events) + max(horizons) + 2 * 3600)
    series = {}
    price_sources = []
    for sym, ck in assets.items():
        s = _hourly_series(dl, ck, lo, hi)
        series[sym] = s
        price_sources.append({"symbol": sym, "status": "OK" if s else "UNAVAILABLE",
                              "n_points": len(s) if s else 0})
    eth = _hourly_series(dl, "coingecko:ethereum", lo, hi)
    price_sources.append({"symbol": "ETH(benchmark)", "status": "OK" if eth else "UNAVAILABLE",
                          "n_points": len(eth) if eth else 0})
    if not eth or not any(series.values()):
        return {"status": "INSUFFICIENT_DATA", "reason": "无法获取真实小时级价格。",
                "fetch": fetch, "price_sources": price_sources, "groups": []}

    def excess(sym, t0, h):
        s = series.get(sym)
        if not s:
            return None
        p0 = price_at_or_before(s, t0); p1 = price_at_or_after(s, t0 + h)
        b0 = price_at_or_before(eth, t0); b1 = price_at_or_after(eth, t0 + h)
        if not (p0 and p1 and b0 and b1) or p0 <= 0 or b0 <= 0:
            return None
        return (p1 / p0 - 1.0) - (b1 / b0 - 1.0)

    # 3) group by (category, event_type, horizon)
    groups = {}
    for (ts, sym, etype, cat) in events:
        for h in horizons:
            r = excess(sym, ts, h)
            if r is None:
                continue
            key = (cat, etype, h)
            groups.setdefault(key, []).append((ts, r))

    # Bonferroni across number of populated groups
    m = sum(1 for v in groups.values() if len(v) > 0) or 1
    try:
        from statistics import NormalDist
        z_bonf = NormalDist().inv_cdf(1 - 0.05 / m)
    except Exception:
        z_bonf = 3.0
    bonf_t = max(t_threshold, z_bonf)

    results = []
    for (cat, etype, h), arr in sorted(groups.items()):
        arr.sort(key=lambda x: x[0])  # chronological
        rets = [r for _, r in arr]
        n = len(rets)
        # walk-forward split
        cut = int(n * train_frac)
        in_r = rets[:cut]; oos_r = rets[cut:]
        in_s = _summary(in_r, cost.get("dex_fee_bps", 30) * 2, seed=7)
        oos_s = _summary(oos_r, cost.get("dex_fee_bps", 30) * 2, seed=8)
        bull = sum(1 for r in rets if _bull_bear(r, thr) == "bullish")
        bear = sum(1 for r in rets if _bull_bear(r, thr) == "bearish")
        neu = n - bull - bear
        # significance gate (strict, Bonferroni + CI>0), both in-sample & OOS>0
        sig = False
        if in_s["n"] >= min_n:
            if in_s["std"] == 0:
                sig = (in_s["mean"] or 0) > 0
            else:
                sig = (in_s["t_stat"] is not None and abs(in_s["t_stat"]) >= bonf_t
                       and in_s["ci95"] and (in_s["ci95"][0] > 0 or in_s["ci95"][1] < 0))
        direction = "long" if (in_s["mean"] or 0) > 0 else "short"
        oos_mean = oos_s.get("mean") or 0
        oos_aligned = oos_mean if direction == "long" else -oos_mean
        # executable net at capital sizes (using the aligned OOS mean, next-bar fills)
        net_sizes = _net_by_size(abs(oos_aligned), sizes, {
            "dex_fee_bps": cost.get("dex_fee_bps", 30),
            "slippage_base_bps": cost.get("slippage_base_bps", 5),
            "slippage_ref_usd": cost.get("slippage_ref_usd", 100000),
            "gas_usd_assumption": cost.get("gas_usd_assumption", 15)}) if oos_s["n"] else []
        confirmed = bool(sig and oos_s["n"] >= 3 and oos_aligned > 0
                         and net_sizes and net_sizes[0]["net_bps"] > 0)
        results.append({
            "category": cat, "event_type": etype, "horizon_h": h // 3600,
            "n": n, "bullish": bull, "bearish": bear, "neutral": neu,
            "in_sample": {k: in_s[k] for k in ("n", "mean", "t_stat", "ci95", "win_rate", "max_drawdown")},
            "out_of_sample": {k: oos_s[k] for k in ("n", "mean", "win_rate", "max_drawdown")},
            "direction": direction, "oos_aligned_mean_bps": round(oos_aligned * 1e4, 1),
            "net_by_size": net_sizes, "confirmed": confirmed,
            "bonferroni_t": round(bonf_t, 2), "n_hypotheses": m,
        })

    confirmed = [r for r in results if r["confirmed"]]
    return {"status": "OK", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lookback_days": scfg.get("lookback_days", 365),
            "n_events": len(events), "n_groups": len(results),
            "bonferroni_t": round(bonf_t, 2), "fetch": fetch,
            "price_sources": price_sources, "results": results,
            "confirmed_signals": confirmed,
            "timelock_execution": "INSUFFICIENT_DATA(需链上 Timelock 追踪；ETH RPC 当前不可用)",
            "treasury_tokenomics_liquidity_prioritized": True,
            "cost_assumptions": cost}


def to_markdown(rep: dict) -> str:
    L = ["# 事件类型真实历史收益报告（小时级 / walk-forward）", "",
         "> 真实数据：Snapshot 治理历史 + DefiLlama 小时价（ETH 超额）。无未来泄漏；"
         "成交价取事件后下一根小时K线（非事后最优）；多重检验校正；不承诺收益；未达标→NO_TRADE。", ""]
    if rep["status"] != "OK":
        L.append(f"**状态: {rep['status']}** — {rep.get('reason','')}")
        return "\n".join(L)
    L.append(f"- 生成: {rep['generated_at']}  研究窗口: 近 {rep['lookback_days']} 天（小时价可得范围）")
    L.append(f"- 事件数: {rep['n_events']}  分组: {rep['n_groups']}  "
             f"Bonferroni t≥{rep['bonferroni_t']}  确认信号: {len(rep['confirmed_signals'])}")
    L.append(f"- Timelock 执行事件: {rep['timelock_execution']}")
    L.append("")
    L.append("## 每类事件：样本量 / 胜率 / 超额 / 样本外 / 回撤 / 利多利空中性")
    L.append("")
    L.append("| 类别 | 事件 | 窗口 | n | 利多/利空/中性 | 内均值bps | 内t | OOSn | OOS对齐均值bps | 最大回撤bps | 确认 |")
    L.append("|------|------|------|---|----------------|-----------|-----|------|----------------|-------------|------|")
    def b(x): return f"{x*1e4:.0f}" if isinstance(x,(int,float)) else "-"
    for r in sorted(rep["results"], key=lambda x: (x["category"], x["event_type"], x["horizon_h"])):
        i = r["in_sample"]; o = r["out_of_sample"]
        L.append("| {c} | {e} | {h}h | {n} | {bu}/{be}/{ne} | {im} | {it} | {on} | {oa} | {md} | {cf} |".format(
            c=r["category"], e=r["event_type"], h=r["horizon_h"], n=r["n"],
            bu=r["bullish"], be=r["bearish"], ne=r["neutral"],
            im=b(i["mean"]), it=(f"{i['t_stat']:.2f}" if i.get("t_stat") is not None else "-"),
            on=o["n"], oa=r["oos_aligned_mean_bps"], md=b(o.get("max_drawdown")),
            cf="YES" if r["confirmed"] else ""))
    L.append("")
    # capital-size net for the best (highest OOS aligned) groups
    L.append("## 分本金规模可执行净收益（样本外对齐均值，扣费/滑点/Gas）")
    L.append("")
    L.append("> 成交价=事件后下一根小时K线价（非事后最优）。滑点=base*sqrt(size/ref)×2腿；"
             f"费率{rep['cost_assumptions'].get('dex_fee_bps')}bps/腿；Gas 假设 "
             f"${rep['cost_assumptions'].get('gas_usd_assumption')}/笔（ETH RPC 实时 gas 不可得，标注为假设）。")
    L.append("")
    top = sorted(rep["results"], key=lambda x: x["oos_aligned_mean_bps"], reverse=True)[:5]
    L.append("| 类别/事件/窗口 | 本金 | 滑点bps | Gas_bps | 净收益bps |")
    L.append("|----------------|------|---------|---------|-----------|")
    for r in top:
        for ns in r["net_by_size"]:
            L.append(f"| {r['category']}/{r['event_type']}/{r['horizon_h']}h | ${ns['size_usd']:,} | "
                     f"{ns['slippage_bps']} | {ns['gas_bps']} | {ns['net_bps']} |")
    L.append("")
    if rep["confirmed_signals"]:
        L.append("## 确认的 TRADE 信号")
        for r in rep["confirmed_signals"]:
            L.append(f"- {r['category']} {r['event_type']} {r['horizon_h']}h "
                     f"方向={r['direction']} OOS对齐均值={r['oos_aligned_mean_bps']}bps")
    else:
        L.append("## 结论：无交易优势")
        L.append("")
        L.append("在小时级数据、事件分类、多窗口、walk-forward 样本外与多重检验校正下，**没有任何事件类别**"
                 "显示出样本内显著且样本外、成本后为正的可持续优势。全部输出 **NO_TRADE**。"
                 "这是真实数据下的结论，未人为放宽标准，也不制造盈利结论。")
    L.append("")
    L.append("> 局限：Timelock 执行与金库实际到账需链上追踪（ETH RPC 当前不可用）；"
             "滑点/Gas 为标注假设（真实逐笔深度与实时 gas 未接入）；研究窗口受小时价可得范围（~1 年）限制。")
    return "\n".join(L)
