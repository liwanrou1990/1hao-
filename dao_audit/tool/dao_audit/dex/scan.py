"""Same-chain cross-DEX arbitrage scanner (real, executable, round-trip).

For each chain/pair/size it:
  1. pins the current block,
  2. gets executable quotes on each venue for both legs
     (quote->base on venue A, base->quote on venue B) at the best fee tier,
  3. computes the round-trip gross in USD,
  4. subtracts ALL costs: gas (real gas price x units x ETH price), flash-loan
     fee (if used), MEV haircut, and expected failure cost,
  5. reports net USD per capital size and a TRADE/NO_TRADE decision.

A size-based quote already embeds price impact. Missing any quote or the ETH
price -> NO_TRADE (never fabricated). Cross-chain is handled separately and is
NOT treated as atomic/risk-free.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional

from .quotes import best_quote, block_number, gas_price_wei
from ..market.sources import DefiLlamaSource


def _usd_price_map(dl: DefiLlamaSource):
    rec = dl.price_current(["coingecko:ethereum"])
    eth = None
    if rec.ok and isinstance(rec.data, dict):
        info = rec.data.get("coins", {}).get("coingecko:ethereum")
        if info and "price" in info:
            eth = float(info["price"])
    return eth, rec


def net_after_costs(gross_usd: float, n_usd: float, gas_price_wei: int,
                    gas_units: int, eth_price: float, costs: dict,
                    n_legs: int = 2) -> dict:
    """Pure cost model: per-leg gas x n_legs + optional flash fee + MEV haircut
    + expected failure cost. Returns the breakdown and net USD."""
    gas_usd = n_legs * gas_units * gas_price_wei * 1e-18 * eth_price
    flash_usd = n_usd * costs.get("flashloan_bps", 0) / 1e4 if costs.get("use_flashloan") else 0.0
    mev_usd = n_usd * costs.get("mev_haircut_bps", 0) / 1e4
    fail_usd = costs.get("fail_probability", 0) * gas_usd
    total = gas_usd + flash_usd + mev_usd + fail_usd
    return {"gas_usd": round(gas_usd, 4), "flash_usd": round(flash_usd, 2),
            "mev_usd": round(mev_usd, 2), "fail_usd": round(fail_usd, 4),
            "total_cost_usd": round(total, 2), "net_usd": round(gross_usd - total, 2)}


def _token_usd(symbol: str, eth_price: Optional[float]) -> Optional[float]:
    if symbol in ("USDC", "USDT", "DAI"):
        return 1.0
    if symbol in ("WETH", "ETH"):
        return eth_price
    return None


def scan_chain(chain: str, ccfg: dict, pairs, fee_tiers, tick_spacings, sizes, costs,
               eth_price: float, min_net_usd: float) -> list:
    rpc = ccfg["rpc"]
    toks = ccfg["tokens"]
    venues = ccfg["venues"]
    gas_units = int(ccfg.get("gas_units_per_swap", 200000))
    blk = block_number(rpc)
    gp = gas_price_wei(rpc)
    out = []
    for (base, quote) in pairs:
        if base not in toks or quote not in toks:
            continue
        bt, qt = toks[base], toks[quote]
        q_usd = _token_usd(quote, eth_price)
        row = {"chain": chain, "pair": f"{base}/{quote}", "block": blk,
               "gas_price_wei": gp, "venues": [v["name"] for v in venues],
               "sizes": [], "decision": "NO_TRADE", "reason": "", "provenance": []}
        if gp is None or q_usd is None or blk is None:
            row["reason"] = "缺区块/gas/计价（真实数据不足）→ NO_TRADE"
            out.append(row); continue

        best_any = None
        for size in sizes:
            amount_in_q = int(size * (10 ** qt["decimals"]))
            # leg1: quote->base on each venue; leg2: base->quote on each venue
            leg1 = {}  # venue -> base_out
            leg2_in = {}
            for v in venues:
                q1 = best_quote(v, rpc, qt["address"], bt["address"],
                                amount_in_q, blk, fee_tiers, tick_spacings)
                leg1[v["name"]] = q1
            best = None
            for va in venues:        # buy base on A
                q1 = leg1[va["name"]]
                if not q1.ok:
                    continue
                for vb in venues:    # sell base on B
                    if vb["name"] == va["name"]:
                        continue
                    q2 = best_quote(vb, rpc, bt["address"], qt["address"],
                                    q1.amount_out, blk, fee_tiers, tick_spacings)
                    if not q2.ok:
                        continue
                    quote_out = q2.amount_out
                    gross_usd = ((quote_out - amount_in_q) / (10 ** qt["decimals"])) * q_usd
                    cb = net_after_costs(gross_usd, size * q_usd, gp, gas_units,
                                         eth_price, costs)
                    cand = {"buy_on": va["name"], "sell_on": vb["name"],
                            "buy_fee": q1.fee, "sell_fee": q2.fee,
                            "gross_usd": round(gross_usd, 2), **cb}
                    net_usd = cb["net_usd"]
                    if best is None or net_usd > best["net_usd"]:
                        best = cand
            if best is None:
                row["sizes"].append({"size_usd": size, "status": "NO_QUOTE"})
                continue
            best["size_usd"] = size
            row["sizes"].append(best)
            if best_any is None or best["net_usd"] > best_any["net_usd"]:
                best_any = best

        if best_any and best_any["net_usd"] > min_net_usd:
            row["decision"] = "TRADE_CANDIDATE"
            row["reason"] = (f"扣全部成本后净收益 ${best_any['net_usd']} > ${min_net_usd} "
                             f"(买@{best_any['buy_on']} 卖@{best_any['sell_on']} size=${best_any['size_usd']:,})")
        else:
            bn = best_any["net_usd"] if best_any else None
            row["reason"] = (f"最佳 round-trip 净收益 ${bn}（<= ${min_net_usd}）→ NO_TRADE"
                             if bn is not None else "无可执行报价 → NO_TRADE")
        row["best"] = best_any
        out.append(row)
    return out


def scan(cfg: dict) -> dict:
    dl = DefiLlamaSource()
    eth_price, erec = _usd_price_map(dl)
    fee_tiers = cfg.get("fee_tiers", [100, 500, 3000])
    tick_spacings = cfg.get("tick_spacings", [1, 50, 100, 200, 2000])
    sizes = cfg.get("capital_sizes", [1000, 10000, 100000])
    costs = cfg.get("costs", {})
    min_net = float(cfg.get("min_net_usd", 1))
    pairs_cfg = cfg.get("pairs", {})
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "eth_price_usd": eth_price, "eth_price_source": "defillama",
              "integrity": "executable on-chain quotes; missing data -> NO_TRADE; no real execution",
              "same_chain": [], "cross_chain_note": (
                  "跨链不同价不可视为原子无风险套利：需跨链桥/中继，存在桥接延迟、桥风险、"
                  "两端各自滑点与价格漂移；本模块仅做同链原子 round-trip，跨链单列为高风险、非原子，未计入机会。")}
    if eth_price is None:
        result["status"] = "NO_TRADE"
        result["reason"] = "ETH 计价不可得（DefiLlama）→ 全部 NO_TRADE"
        return result
    for chain, ccfg in cfg.get("chains", {}).items():
        pairs = [tuple(p) for p in pairs_cfg.get(chain, [])]
        rows = scan_chain(chain, ccfg, pairs, fee_tiers, tick_spacings, sizes, costs, eth_price, min_net)
        result["same_chain"].extend(rows)
    result["status"] = "OK"
    result["n_candidates"] = sum(1 for r in result["same_chain"] if r["decision"] == "TRADE_CANDIDATE")
    return result


def to_markdown(rep: dict) -> str:
    L = ["# 真实 DEX 同链跨市场套利扫描", "",
         "> 可执行链上报价（QuoterV2，按金额含价格冲击）；同区块两腿 round-trip；"
         "扣除 Gas/费/滑点/闪电贷费/MEV/失败成本；真实数据不足→NO_TRADE；不签名、不执行真实交易。", ""]
    L.append(f"- 生成: {rep.get('generated_at')}  ETH计价: ${rep.get('eth_price_usd')} (DefiLlama)")
    if rep.get("status") != "OK":
        L.append(f"- 状态: {rep.get('status')} — {rep.get('reason','')}")
        return "\n".join(L)
    L.append(f"- 同链候选机会(净>0且过阈值): **{rep.get('n_candidates', 0)}**")
    L.append("")
    for r in rep["same_chain"]:
        L.append(f"## {r['chain']} · {r['pair']}  (block {r['block']}, venues {', '.join(r['venues'])})")
        L.append("")
        L.append(f"- 决策: **{r['decision']}** — {r['reason']}")
        if r.get("sizes"):
            L.append("")
            L.append("| 本金USD | 买@ | 卖@ | 毛利USD | Gas | MEV | 失败成本 | 总成本 | 净USD |")
            L.append("|---------|-----|-----|---------|-----|-----|----------|--------|-------|")
            for s in r["sizes"]:
                if s.get("status") == "NO_QUOTE":
                    L.append(f"| {s['size_usd']:,} | - | - | - | - | - | - | - | NO_QUOTE |")
                else:
                    L.append("| {sz:,} | {b} | {se} | {g} | {ga} | {m} | {f} | {tc} | {n} |".format(
                        sz=s["size_usd"], b=s["buy_on"], se=s["sell_on"], g=s["gross_usd"],
                        ga=s["gas_usd"], m=s["mev_usd"], f=s["fail_usd"],
                        tc=s["total_cost_usd"], n=s["net_usd"]))
        L.append("")
    L.append("## 跨链")
    L.append("")
    L.append(rep["cross_chain_note"])
    L.append("")
    if rep.get("n_candidates", 0) == 0:
        L.append("## 结论")
        L.append("")
        L.append("本次快照中，所有同链跨 DEX round-trip 在扣除全部真实成本后净收益非正 → 全部 NO_TRADE。"
                 "这与高效的 L2 DEX 市场一致：显示价差多被手续费、价格冲击与 Gas 吞没。"
                 "不制造盈利结论。")
    return "\n".join(L)


def monitor(cfg: dict, snapshots: int = 3, interval_s: int = 20) -> dict:
    """Repeated real snapshots to measure opportunity frequency / persistence /
    simulated-fill success. Bounded; no real execution."""
    import time as _t
    from datetime import datetime, timezone
    snaps = []
    pair_hits = {}
    for i in range(max(1, snapshots)):
        rep = scan(cfg)
        ts = rep.get("generated_at")
        n_cand = rep.get("n_candidates", 0)
        per = []
        for r in rep.get("same_chain", []):
            key = f"{r['chain']}/{r['pair']}"
            best = r.get("best")
            is_cand = r["decision"] == "TRADE_CANDIDATE"
            if is_cand:
                pair_hits[key] = pair_hits.get(key, 0) + 1
            per.append({"pair": key, "candidate": is_cand,
                        "best_net_usd": best["net_usd"] if best else None})
        snaps.append({"i": i, "ts": ts, "n_candidates": n_cand, "pairs": per})
        if i < snapshots - 1:
            _t.sleep(min(interval_s, 60))
    total = len(snaps)
    with_cand = sum(1 for s in snaps if s["n_candidates"] > 0)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "snapshots": total, "interval_s": interval_s,
            "snapshots_with_candidate": with_cand,
            "candidate_frequency": round(with_cand / total, 3) if total else 0.0,
            "persistence_by_pair": pair_hits,
            "detail": snaps,
            "note": ("机会频率=出现正净收益候选的快照占比；本窗口为真实快照统计。"
                     "无候选时频率为 0，不代表未来，也不构成收益承诺。")}


def monitor_resumable(cfg: dict, state_path, max_new: int = 5, interval_s: int = 30,
                      target_hours: float = 24.0) -> dict:
    """Resumable continuous monitor. Appends one JSONL line per snapshot to
    state_path; on re-invocation it reads prior lines and continues, so 24h+
    observation accumulates across runs (rate-limit friendly: bounded per run).
    Aggregates opportunity frequency, persistence and simulated tradability."""
    import json, os, time as _t
    from datetime import datetime, timezone
    prior = []
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        prior.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    # elapsed span of accumulated observation
    def _span_hours(rows):
        ts = [r.get("epoch") for r in rows if r.get("epoch")]
        return (max(ts) - min(ts)) / 3600 if len(ts) >= 2 else 0.0

    added = 0
    with open(state_path, "a", encoding="utf-8") as fh:
        for i in range(max(1, max_new)):
            if _span_hours(prior) >= target_hours:
                break
            rep = scan(cfg)
            now = int(_t.time())
            snap = {"epoch": now, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "n_candidates": rep.get("n_candidates", 0), "pairs": []}
            for r in rep.get("same_chain", []):
                best = r.get("best")
                snap["pairs"].append({"pair": f"{r['chain']}/{r['pair']}",
                                      "block": r.get("block"),
                                      "candidate": r["decision"] == "TRADE_CANDIDATE",
                                      "best_net_usd": best["net_usd"] if best else None})
            fh.write(json.dumps(snap, ensure_ascii=False) + "\n"); fh.flush()
            prior.append(snap); added += 1
            if i < max_new - 1 and _span_hours(prior) < target_hours:
                _t.sleep(min(interval_s, 120))

    total = len(prior)
    with_cand = sum(1 for s in prior if s.get("n_candidates", 0) > 0)
    pair_hits = {}
    pair_best = {}
    pair_series = {}   # ordered best_net per pair for persistence/competition
    for s in prior:
        for p in s.get("pairs", []):
            k = p["pair"]
            if p.get("candidate"):
                pair_hits[k] = pair_hits.get(k, 0) + 1
            if p.get("best_net_usd") is not None:
                pair_best[k] = max(pair_best.get(k, -1e18), p["best_net_usd"])
                pair_series.setdefault(k, []).append(p["best_net_usd"])
    # competition / persistence proxies: how close to breakeven, and how often
    # the best net sign flips across consecutive snapshots (churn = competition).
    competition = {}
    for k, ser in pair_series.items():
        flips = sum(1 for i in range(1, len(ser))
                    if (ser[i] > 0) != (ser[i - 1] > 0))
        competition[k] = {"n": len(ser), "best_net_usd": round(max(ser), 2),
                          "avg_net_usd": round(sum(ser) / len(ser), 2),
                          "sign_flips": flips,
                          "max_consecutive_positive": _max_run_positive(ser)}
    return {"state_file": str(state_path), "snapshots_total": total, "added_this_run": added,
            "observation_hours": round(_span_hours(prior), 3), "target_hours": target_hours,
            "snapshots_with_candidate": with_cand,
            "candidate_frequency": round(with_cand / total, 4) if total else 0.0,
            "persistence_by_pair": pair_hits,
            "best_net_seen_by_pair": {k: round(v, 2) for k, v in pair_best.items()},
            "competition_by_pair": competition,
            "note": ("可恢复监控：每次调用追加若干真实快照到 JSONL，多次运行累积至 target_hours。"
                     "机会频率=出现正净收益候选的快照占比；sign_flips 近似竞争/价差翻转频率；"
                     "max_consecutive_positive 近似机会持续时间。无候选→频率 0，不构成收益承诺。")}


def _max_run_positive(series) -> int:
    best = cur = 0
    for x in series:
        if x > 0:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def rank_report(scan_rep: dict, roundtrip_fork_verified: bool = False) -> dict:
    """Rank same-chain opportunities by best net USD. An opportunity is
    'simulated_feasible' ONLY if net>0 AND a full two-leg fork round trip was
    verified. Realized profit is never claimed."""
    rows = []
    for r in scan_rep.get("same_chain", []):
        best = r.get("best")
        net = best["net_usd"] if best else None
        feasible = bool(net is not None and net > 0 and roundtrip_fork_verified)
        rows.append({"chain": r["chain"], "pair": r["pair"], "block": r.get("block"),
                     "best_net_usd": net, "decision": r["decision"],
                     "buy_on": best["buy_on"] if best else None,
                     "sell_on": best["sell_on"] if best else None,
                     "size_usd": best.get("size_usd") if best else None,
                     "simulated_feasible": feasible})
    rows.sort(key=lambda x: (x["best_net_usd"] is not None, x["best_net_usd"] or -1e18), reverse=True)
    n_feasible = sum(1 for x in rows if x["simulated_feasible"])
    return {"ranked": rows, "n_feasible": n_feasible,
            "verdict": "NO_TRADE" if n_feasible == 0 else "CANDIDATES",
            "note": ("按扣全部成本后净收益排序。simulated_feasible 需 净>0 且完整双腿 fork 验证通过；"
                     "真实执行成功率未经验证前，不标记为已实现利润。")}
