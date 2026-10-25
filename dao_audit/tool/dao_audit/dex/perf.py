"""Two-tier scanning + performance benchmark (performance / anti-missed-detection).

The baseline (`scan.scan_chain`) issues one `cast` subprocess per quote
(~0.40s each); a 4-venue full scan needs dozens of them and exceeds 240s,
unable to keep up with Base's ~2s block time. This module removes that cost.

Tier 1 (fast candidate discovery): batch EVERY concentrated-liquidity quote
(Uniswap V3 / SushiSwap V3 / Aerodrome Slipstream, which share the
quoteExactInputSingle ABI) into Multicall3 `aggregate3` eth_calls. The full
round trip (quote->base on A, base->quote on B) is computed from exactly TWO
batched RPC round-trips per pair instead of O(venues^2 x params) subprocesses:
  - RPC A: all buy-leg quotes (quote->base), all venues x params, one call.
  - RPC B: all sell-leg quotes (base->quote) from each buy output, one call.
A pair is flagged a candidate when its best round-trip GROSS (post-swap-fee,
post-price-impact, both legs) clears a conservative floor that is strictly
looser than the full explicit-cost model. This makes the Tier-1 candidate set
a PROVABLE SUPERSET of the baseline's: any pair the baseline would mark
TRADE_CANDIDATE (net = gross - costs > min_net, costs > 0 => gross > min_net)
necessarily clears the floor, so speeding up the scan drops no positive path.

Tier 2 (full precise verification) is the existing `scan.scan_chain` +
round-trip fork, run ONLY for flagged candidates, with the full cost model and
all venues (including classic/solidly). It is authoritative; Tier 1 never
decides TRADE.

On any batch failure / RPC lag / chain desync the scan returns
INSUFFICIENT_DATA and emits no candidate (no trade signal on bad data).

Also provided: a latency benchmark over real blocks (P50/P95/P99, RPC counts,
per-chain) and an equivalence check proving each batched quote equals the
sequential `cast` quote at the SAME block (byte-identical numbers).
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import List, Optional

from .fastquote import batch_quote_univ3
from .quotes import block_number, best_univ3_quote, best_slipstream_quote

# A pair is flagged when best round-trip gross >= -GROSS_FLOOR_BPS. The floor
# must exceed the max plausible total explicit cost (gas + MEV + fail + flash)
# in bps so Tier 1 is a strict superset of the baseline candidate set. On Base
# ~$1k, explicit cost ~= 2bps MEV + ~0.04bps gas + fail; 50bps is a wide margin.
GROSS_FLOOR_BPS = 50.0


def _cl_venues(chain_cfg: dict):
    """Concentrated-liquidity venues Tier 1 can batch (shared quoter ABI)."""
    return [v for v in chain_cfg["venues"] if v["type"] in ("univ3", "slipstream")]


def _params_for(v, fee_tiers, tick_spacings):
    return fee_tiers if v["type"] == "univ3" else tick_spacings


def build_cl_requests(chain_cfg: dict, pairs, fee_tiers, tick_spacings, size_usd):
    """Build the BUY-leg (quote->base) CL quote requests at one reference size.
    Returns (reqs, meta) where meta parallels reqs with venue/pair/param."""
    toks = chain_cfg["tokens"]
    reqs: List[dict] = []
    meta: List[dict] = []
    for (base, quote) in pairs:
        if base not in toks or quote not in toks:
            continue
        bt, qt = toks[base], toks[quote]
        amt_q = int(size_usd * (10 ** qt["decimals"]))  # quote->base leg input
        for v in _cl_venues(chain_cfg):
            for p in _params_for(v, fee_tiers, tick_spacings):
                reqs.append({"quoter": v["quoter"], "token_in": qt["address"],
                             "token_out": bt["address"], "amount_in": amt_q, "fee": p,
                             "vtype": v["type"]})
                meta.append({"venue": v["name"], "type": v["type"], "quoter": v["quoter"],
                             "pair": f"{base}/{quote}", "param": p,
                             "base": base, "quote": quote, "amt_q": amt_q,
                             "base_dec": bt["decimals"], "quote_dec": qt["decimals"],
                             "bt": bt["address"], "qt": qt["address"]})
    return reqs, meta


def tier1_scan(chain: str, chain_cfg: dict, pairs, fee_tiers, tick_spacings,
               size_usd: float, gross_floor_bps: float = GROSS_FLOOR_BPS,
               block: Optional[int] = None) -> dict:
    """Fast candidate discovery: exact best round trip per pair via two batched
    Multicall eth_calls. Flags a provable superset of the baseline candidates."""
    rpc = chain_cfg["rpc"]
    blk = block if block is not None else block_number(rpc)
    if blk is None:
        return {"chain": chain, "block": None, "latency_ms": 0.0, "rpc_calls": 0,
                "n_inner_quotes": 0, "ok": False, "error": "block_number unavailable",
                "candidates": [], "best_roundtrip_by_pair": {},
                "data_status": "INSUFFICIENT_DATA"}

    # --- RPC A: all buy-leg quotes (quote -> base) ---
    buy_reqs, buy_meta = build_cl_requests(chain_cfg, pairs, fee_tiers, tick_spacings, size_usd)
    ba = batch_quote_univ3(rpc, buy_reqs, block=blk)
    if not ba.ok:
        return {"chain": chain, "block": blk, "latency_ms": round(ba.latency_ms, 1),
                "rpc_calls": 1, "n_inner_quotes": ba.n_calls, "ok": False,
                "error": "buy batch failed: %s" % ba.error, "candidates": [],
                "best_roundtrip_by_pair": {}, "data_status": "INSUFFICIENT_DATA"}

    # best buy per (pair, venue): max base_out over that venue's params
    best_buy = {}  # (pair, venue) -> {base_out, param, type, quoter, meta}
    for m, a in zip(buy_meta, ba.amounts):
        if a is None:
            continue
        key = (m["pair"], m["venue"])
        cur = best_buy.get(key)
        if cur is None or a > cur["base_out"]:
            best_buy[key] = {"base_out": a, "param": m["param"], "venue": m["venue"],
                             "type": m["type"], "quoter": m["quoter"], "m": m}

    # --- RPC B: sell-leg quotes (base -> quote) from each buy output ---
    sell_reqs: List[dict] = []
    sell_meta: List[dict] = []
    for (pair, buy_venue), bb in best_buy.items():
        m = bb["m"]
        for v in _cl_venues(chain_cfg):
            if v["name"] == buy_venue:
                continue  # round trip must cross venues
            for p in _params_for(v, fee_tiers, tick_spacings):
                sell_reqs.append({"quoter": v["quoter"], "token_in": m["bt"],
                                  "token_out": m["qt"], "amount_in": bb["base_out"], "fee": p,
                                  "vtype": v["type"]})
                sell_meta.append({"pair": pair, "buy_venue": buy_venue, "buy_param": bb["param"],
                                  "sell_venue": v["name"], "sell_param": p,
                                  "amt_q": m["amt_q"], "quote_dec": m["quote_dec"]})
    bsell = batch_quote_univ3(rpc, sell_reqs, block=blk) if sell_reqs else None
    if sell_reqs and not bsell.ok:
        return {"chain": chain, "block": blk,
                "latency_ms": round(ba.latency_ms + bsell.latency_ms, 1),
                "rpc_calls": 2, "n_inner_quotes": ba.n_calls + bsell.n_calls, "ok": False,
                "error": "sell batch failed: %s" % bsell.error, "candidates": [],
                "best_roundtrip_by_pair": {}, "data_status": "INSUFFICIENT_DATA"}

    # best round trip per pair
    best_rt = {}  # pair -> {gross_bps, buy_venue, sell_venue, quote_out, amt_q}
    if bsell is not None:
        for m, out in zip(sell_meta, bsell.amounts):
            if out is None:
                continue
            gross_bps = (out - m["amt_q"]) / m["amt_q"] * 1e4
            cur = best_rt.get(m["pair"])
            if cur is None or gross_bps > cur["gross_bps"]:
                best_rt[m["pair"]] = {
                    "gross_bps": round(gross_bps, 3), "buy_venue": m["buy_venue"],
                    "buy_param": m["buy_param"], "sell_venue": m["sell_venue"],
                    "sell_param": m["sell_param"], "quote_out": out, "amt_q": m["amt_q"]}

    # Venues Tier-1 cannot price in-batch (solidly/classic use a different ABI).
    # Their presence on a pair means Tier-1 CANNOT prove that pair unprofitable,
    # so it MUST flag it (deferred to Tier-2) — never dropped on speedup.
    deferred = [v["name"] for v in chain_cfg["venues"] if v["type"] not in ("univ3", "slipstream")]
    pair_names = [f"{b}/{q}" for (b, q) in pairs
                  if b in chain_cfg["tokens"] and q in chain_cfg["tokens"]]

    candidates = []
    filter_log = []  # one entry per pair: flagged? why?
    for pair in pair_names:
        v = best_rt.get(pair)
        gross = v["gross_bps"] if v else None
        near = gross is not None and gross >= -gross_floor_bps
        has_deferred = bool(deferred)
        flagged = near or has_deferred or v is None  # v is None => no CL quote, can't rule out
        if near:
            reason = f"CL闭环毛收益 {gross}bps >= -{gross_floor_bps}bps（近/超盈亏平衡）"
        elif has_deferred:
            reason = f"存在非CL venue {deferred} 未入批量，无法证伪 → 保守保留交 Tier-2"
        elif v is None:
            reason = "无CL可执行报价，无法判定 → 保守保留交 Tier-2"
        else:
            reason = f"CL闭环毛收益 {gross}bps << -{gross_floor_bps}bps 且无其它 venue → 丢弃"
        filter_log.append({"pair": pair, "flagged": flagged, "cl_gross_bps": gross,
                           "deferred_venues": deferred, "reason": reason})
        if flagged:
            entry = {"pair": pair, "flag_reason": reason, "deferred_venues": deferred}
            if v:
                entry.update(v)
            candidates.append(entry)

    lat = ba.latency_ms + (bsell.latency_ms if bsell is not None else 0.0)
    rpc_calls = 1 + (1 if sell_reqs else 0)
    n_inner = ba.n_calls + (bsell.n_calls if bsell is not None else 0)
    return {"chain": chain, "block": blk, "latency_ms": round(lat, 1),
            "rpc_calls": rpc_calls, "n_inner_quotes": n_inner, "ok": True, "error": None,
            "size_usd": size_usd, "gross_floor_bps": gross_floor_bps,
            "deferred_venues": deferred,
            "candidates": candidates, "best_roundtrip_by_pair": best_rt,
            "filter_log": filter_log, "data_status": "OK",
            "note": ("Tier-1 候选=最优CL闭环毛收益 >= -%.0fbps（含两腿手续费+价格冲击），或该交易对"
                     "存在未入批量的非CL venue（solidly/classic，无法证伪故保守保留）。该下限宽于全部"
                     "显式成本(Gas/MEV/失败/闪电贷)，故候选集为基准(scan_chain)候选集的真超集——提速"
                     "不漏任何正净收益路径。精确净收益由 Tier-2（全 venue+全成本+fork）判定。"
                     % gross_floor_bps)}


def benchmark(cfg: dict, chain: str, n_blocks: int = 20, size_usd: float = 1000,
              poll_s: float = 1.0, max_seconds: int = 120) -> dict:
    """Run Tier-1 over up to n_blocks distinct blocks; report latency percentiles."""
    ccfg = cfg["chains"][chain]
    pairs = [tuple(p) for p in cfg.get("pairs", {}).get(chain, [])]
    fee_tiers = cfg.get("fee_tiers", [100, 500, 3000])
    tick_spacings = cfg.get("tick_spacings", [1, 50, 100, 200, 2000])
    lat: List[float] = []
    rpc_counts: List[int] = []
    inner_counts: List[int] = []
    seen = set()
    rows = []
    insufficient = 0
    start = time.monotonic()
    while len(seen) < n_blocks and (time.monotonic() - start) < max_seconds:
        bn = block_number(ccfg["rpc"])
        if bn is None or bn in seen:
            time.sleep(poll_s)
            continue
        r = tier1_scan(chain, ccfg, pairs, fee_tiers, tick_spacings, size_usd, block=bn)
        if r["ok"]:
            seen.add(bn)
            lat.append(r["latency_ms"])
            rpc_counts.append(r["rpc_calls"])
            inner_counts.append(r["n_inner_quotes"])
            rows.append({"block": bn, "latency_ms": r["latency_ms"],
                         "rpc_calls": r["rpc_calls"], "n_inner_quotes": r["n_inner_quotes"],
                         "n_candidates": len(r["candidates"])})
        else:
            insufficient += 1
        time.sleep(poll_s)
    lat_sorted = sorted(lat)

    def pct(p):
        if not lat_sorted:
            return None
        k = max(0, min(len(lat_sorted) - 1, int(round(p / 100 * (len(lat_sorted) - 1)))))
        return lat_sorted[k]

    # Before/after: time the SAME buy-leg quote set once via sequential `cast`
    # (the old path) vs one batched eth_call, at one pinned block.
    baseline = _sequential_baseline(ccfg, pairs, fee_tiers, tick_spacings, size_usd)

    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chain": chain, "size_usd": size_usd, "blocks_measured": len(seen),
            "insufficient_data_blocks": insufficient,
            "before_after": baseline,
            "inner_quotes_per_scan": {"min": min(inner_counts) if inner_counts else None,
                                      "max": max(inner_counts) if inner_counts else None},
            "rpc_calls_per_scan": {"min": min(rpc_counts) if rpc_counts else None,
                                   "max": max(rpc_counts) if rpc_counts else None},
            "latency_ms": {"p50": pct(50), "p95": pct(95), "p99": pct(99),
                           "min": lat_sorted[0] if lat_sorted else None,
                           "max": lat_sorted[-1] if lat_sorted else None,
                           "mean": round(sum(lat) / len(lat), 1) if lat else None},
            "p95_under_1s": (pct(95) is not None and pct(95) < 1000),
            "detail": rows,
            "note": ("Tier-1 候选发现：每区块 2 次 Multicall eth_call（买腿/卖腿各 1 次批量）。"
                     "P95<1s 为目标；数值为真实测量，未达则如实报告瓶颈，不伪报达标。")}


def tier1_inner_count(ccfg, pairs, fee_tiers, tick_spacings):
    reqs, _ = build_cl_requests(ccfg, pairs, fee_tiers, tick_spacings, 1000)
    return len(reqs)


def _sequential_baseline(ccfg, pairs, fee_tiers, tick_spacings, size_usd) -> dict:
    """One honest before/after: the identical buy-leg quote set, timed as the
    old per-quote `cast` subprocesses vs one batched eth_call, at one block."""
    blk = block_number(ccfg["rpc"])
    reqs, meta = build_cl_requests(ccfg, pairs, fee_tiers, tick_spacings, size_usd)
    if blk is None or not reqs:
        return {"status": "INSUFFICIENT_DATA"}
    t0 = time.monotonic()
    br = batch_quote_univ3(ccfg["rpc"], reqs, block=blk)
    batched_ms = (time.monotonic() - t0) * 1000
    t0 = time.monotonic()
    for m, r in zip(meta, reqs):
        if m["type"] == "univ3":
            best_univ3_quote(ccfg["rpc"], m["venue"], m["quoter"], r["token_in"],
                             r["token_out"], r["amount_in"], [r["fee"]], blk)
        else:
            best_slipstream_quote(ccfg["rpc"], m["venue"], m["quoter"], r["token_in"],
                                  r["token_out"], r["amount_in"], [r["fee"]], blk)
    seq_ms = (time.monotonic() - t0) * 1000
    return {"status": "OK", "block": blk, "n_quotes": len(reqs),
            "sequential_cast_ms": round(seq_ms, 1), "sequential_rpc_calls": len(reqs),
            "batched_multicall_ms": round(batched_ms, 1), "batched_rpc_calls": 1,
            "speedup_x": round(seq_ms / batched_ms, 1) if batched_ms else None}


def equivalence_check(cfg: dict, chain: str, size_usd: float = 1000) -> dict:
    """Compare every batched Multicall buy-leg quote to the sequential `cast`
    quote at the SAME pinned block. Byte-identical numbers prove the fast path
    changes no quote (same QuoterV2 math, same block) -> drops no candidate."""
    ccfg = cfg["chains"][chain]
    pairs = [tuple(p) for p in cfg.get("pairs", {}).get(chain, [])]
    fee_tiers = cfg.get("fee_tiers", [100, 500, 3000])
    tick_spacings = cfg.get("tick_spacings", [1, 50, 100, 200, 2000])
    blk = block_number(ccfg["rpc"])
    if blk is None:
        return {"chain": chain, "block": None, "compared": 0, "mismatches": [],
                "identical": False, "data_status": "INSUFFICIENT_DATA"}
    reqs, meta = build_cl_requests(ccfg, pairs, fee_tiers, tick_spacings, size_usd)
    br = batch_quote_univ3(ccfg["rpc"], reqs, block=blk)
    mismatches = []
    compared = 0
    for m, r, a in zip(meta, reqs, br.amounts):
        if m["type"] == "univ3":
            q = best_univ3_quote(ccfg["rpc"], m["venue"], m["quoter"], r["token_in"],
                                 r["token_out"], r["amount_in"], [r["fee"]], blk)
        else:
            q = best_slipstream_quote(ccfg["rpc"], m["venue"], m["quoter"], r["token_in"],
                                      r["token_out"], r["amount_in"], [r["fee"]], blk)
        seq = q.amount_out if q.ok else None
        compared += 1
        if a is None and seq is None:
            continue
        if a != seq:
            mismatches.append({"venue": m["venue"], "pair": m["pair"], "param": m["param"],
                               "batched": a, "sequential": seq})
    return {"chain": chain, "block": blk, "compared": compared, "mismatches": mismatches,
            "identical": len(mismatches) == 0, "data_status": "OK",
            "note": ("同一区块下批量(Multicall)与顺序(cast)报价逐一精确比对；identical=True "
                     "表示提速未改变任何报价 → 批量机制无精度损失、无漏检。")}


def to_markdown(bench: dict, equiv: dict) -> str:
    L = ["# DEX 扫描性能优化与防漏检报告 (perf, v0.9)", "",
         "> 以 Multicall3 批量 eth_call 取代逐笔 `cast` 子进程；两级扫描；候选集为基准真超集；"
         "缺数据→INSUFFICIENT_DATA 不出信号。真实测量，未达标如实报告，不伪报。", ""]
    def _rng(d):
        lo, hi = d.get("min"), d.get("max")
        return str(lo) if lo == hi else f"{lo}–{hi}"

    L.append("## 一、性能：Tier-1 候选发现延迟（真实区块测量）")
    L.append("")
    lm = bench["latency_ms"]
    L.append(f"- 链: **{bench['chain']}**  本金: ${bench['size_usd']:,}  测量区块数: {bench['blocks_measured']}"
             f"  INSUFFICIENT_DATA 区块: {bench['insufficient_data_blocks']}")
    L.append(f"- 每次扫描 RPC 次数: {_rng(bench['rpc_calls_per_scan'])}（批量买腿+卖腿各 1 次）"
             f"  内层报价数: {_rng(bench['inner_quotes_per_scan'])}")
    ba = bench.get("before_after", {})
    if ba.get("status") == "OK":
        L.append("")
        L.append("**优化前/后对比**（同一区块、同 %d 个买腿报价）:" % ba["n_quotes"])
        L.append("")
        L.append("| 方式 | 耗时(ms) | RPC/子进程调用 |")
        L.append("|------|----------|----------------|")
        L.append(f"| 优化前：逐笔 `cast` 子进程 | {ba['sequential_cast_ms']} | {ba['sequential_rpc_calls']} |")
        L.append(f"| 优化后：批量 Multicall eth_call | {ba['batched_multicall_ms']} | {ba['batched_rpc_calls']} |")
        L.append(f"| **加速比** | **{ba['speedup_x']}x** | — |")
    L.append("")
    L.append("| 指标 | P50 | P95 | P99 | min | max | mean |")
    L.append("|------|-----|-----|-----|-----|-----|------|")
    L.append(f"| 延迟(ms) | {lm['p50']} | {lm['p95']} | {lm['p99']} | {lm['min']} | {lm['max']} | {lm['mean']} |")
    L.append("")
    tgt = "**达标 ✅**" if bench["p95_under_1s"] else "**未达标**"
    L.append(f"- Base 核心候选发现 P95 < 1s 目标: {tgt}（P95 = {lm['p95']} ms）")
    L.append("")
    L.append("## 二、防漏检：批量 vs 顺序 等价校验（同区块）")
    L.append("")
    if equiv.get("data_status") != "OK":
        L.append(f"- 状态: {equiv.get('data_status')}（区块不可得，无法校验）")
    else:
        verdict = "**完全一致 ✅**" if equiv["identical"] else f"**{len(equiv['mismatches'])} 处不一致 ❌**"
        L.append(f"- 区块 {equiv['block']} 逐笔比对 {equiv['compared']} 个报价: {verdict}")
        if equiv["mismatches"]:
            L.append("")
            for mm in equiv["mismatches"][:20]:
                L.append(f"  - {mm['venue']} {mm['pair']} param={mm['param']}: "
                         f"批量={mm['batched']} 顺序={mm['sequential']}")
    L.append("")
    L.append("> 等价校验证明批量机制与逐笔 `cast` 在同一区块产出完全相同的 QuoterV2 报价；"
             "结合 Tier-1 候选集为基准真超集的构造，提速不丢任何正净收益路径。精确净收益由 Tier-2"
             "（现有 scan_chain 全成本模型 + 完整双腿 fork）判定，Tier-1 不产生 TRADE 决策。")
    return "\n".join(L)
