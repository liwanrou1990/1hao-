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

from .quotes import best_univ3_quote, block_number, gas_price_wei
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
                    gas_units: int, eth_price: float, costs: dict) -> dict:
    """Pure cost model: two-swap gas + optional flash fee + MEV haircut +
    expected failure cost. Returns the breakdown and net USD."""
    gas_usd = 2 * gas_units * gas_price_wei * 1e-18 * eth_price
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


def scan_chain(chain: str, ccfg: dict, pairs, fee_tiers, sizes, costs,
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
                q1 = best_univ3_quote(rpc, v["name"], v["quoter"], qt["address"],
                                      bt["address"], amount_in_q, fee_tiers, blk)
                leg1[v["name"]] = q1
            best = None
            for va in venues:        # buy base on A
                q1 = leg1[va["name"]]
                if not q1.ok:
                    continue
                for vb in venues:    # sell base on B
                    if vb["name"] == va["name"]:
                        continue
                    q2 = best_univ3_quote(rpc, vb["name"], vb["quoter"], bt["address"],
                                          qt["address"], q1.amount_out, fee_tiers, blk)
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
        rows = scan_chain(chain, ccfg, pairs, fee_tiers, sizes, costs, eth_price, min_net)
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
