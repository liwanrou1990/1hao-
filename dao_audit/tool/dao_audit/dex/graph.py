"""Graph search for closed-loop (triangular / multi-hop) DEX arbitrage.

Nodes = tokens; directed edges = the best executable quote across ALL venues
and fee tiers / tick spacings for a given input amount, at one pinned block.
A DFS enumerates simple cycles that start and end at the base (quote) asset,
up to max_hops. Each cycle is evaluated with REAL sized quotes chained leg by
leg (price impact embedded), then full costs are subtracted. Cycles are ranked
by net USD and ROC. When nothing is positive, the best cycle's economics are
decomposed to attribute the shortfall (spread vs fee/impact vs gas vs MEV/fail).

No cost assumption is softened to manufacture profit. Missing quotes drop the
edge. Fork verification of a positive cycle is a separate, explicit step.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional

from .quotes import best_quote, block_number, gas_price_wei
from .scan import net_after_costs, _usd_price_map, _token_usd
from ..market.sources import DefiLlamaSource


def _best_edge(cache, venues, rpc, tin, tout, amount_in, block, fee_tiers, tick_spacings):
    key = (tin, tout, amount_in, block)
    if key in cache:
        return cache[key]
    best = None
    for v in venues:
        q = best_quote(v, rpc, tin, tout, amount_in, block, fee_tiers, tick_spacings)
        if q.ok and (best is None or q.amount_out > best.amount_out):
            best = q
    cache[key] = best
    return best


def search_chain(chain: str, ccfg: dict, base_sym: str, intermediates, sizes,
                 fee_tiers, tick_spacings, costs, eth_price: float,
                 max_hops: int = 3) -> dict:
    rpc = ccfg["rpc"]; toks = ccfg["tokens"]; venues = ccfg["venues"]
    gas_units = int(ccfg.get("gas_units_per_swap", 200000))
    blk = block_number(rpc); gp = gas_price_wei(rpc)
    out = {"chain": chain, "base": base_sym, "block": blk, "gas_price_wei": gp,
           "venues": [v["name"] for v in venues], "cycles": []}
    if blk is None or gp is None or base_sym not in toks:
        out["error"] = "缺区块/gas/base token"
        return out
    base = toks[base_sym]
    cache = {}

    for size in sizes:
        start_amt = int(size * (10 ** base["decimals"]))

        # DFS over token path: list of (symbol, amount). Return to base closes a cycle.
        def dfs(path):
            cur_sym, cur_amt = path[-1]
            hops = len(path) - 1
            # try closing the cycle back to base (if we have moved at least 1 intermediate)
            if hops >= 1 and cur_sym != base_sym:
                e = _best_edge(cache, venues, rpc, toks[cur_sym]["address"],
                               base["address"], cur_amt, blk, fee_tiers, tick_spacings)
                if e and e.ok:
                    final = e.amount_out
                    gross_usd = (final - start_amt) / (10 ** base["decimals"]) * _token_usd(base_sym, eth_price)
                    legs = hops + 1
                    cb = net_after_costs(gross_usd, size * _token_usd(base_sym, eth_price),
                                         gp, gas_units, eth_price, costs, n_legs=legs)
                    route = base_sym + "->" + "->".join(s for s, _ in path[1:]) + "->" + base_sym
                    out["cycles"].append({
                        "size_usd": size, "route": route, "legs": legs,
                        "gross_usd": round(gross_usd, 4), **cb,
                        "roc_pct": round(cb["net_usd"] / (size * _token_usd(base_sym, eth_price)) * 100, 4),
                    })
            if hops >= max_hops - 0:  # path already has max_hops intermediates? limit extension
                pass
            if len(path) - 1 >= max_hops - 1:
                return
            for sym in intermediates:
                if sym in (s for s, _ in path):
                    continue
                e = _best_edge(cache, venues, rpc, toks[cur_sym]["address"],
                               toks[sym]["address"], cur_amt, blk, fee_tiers, tick_spacings)
                if e and e.ok:
                    dfs(path + [(sym, e.amount_out)])

        dfs([(base_sym, start_amt)])

    out["quote_calls_cached"] = len(cache)
    return out


def run(cfg: dict, max_hops: int = 3) -> dict:
    dl = DefiLlamaSource()
    eth_price, _ = _usd_price_map(dl)
    fee_tiers = cfg.get("fee_tiers", [100, 500, 3000])
    tick_spacings = cfg.get("tick_spacings", [1, 100, 2000])
    costs = cfg.get("costs", {})
    gcfg = cfg.get("graph", {})
    sizes = gcfg.get("sizes", [1000, 100000])
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "eth_price_usd": eth_price, "max_hops": max_hops, "chains": []}
    if eth_price is None:
        result["status"] = "NO_TRADE"; result["reason"] = "ETH 计价不可得"; return result
    for chain, ccfg in cfg.get("chains", {}).items():
        gc = gcfg.get(chain)
        if not gc:
            continue
        r = search_chain(chain, ccfg, gc["base"], gc["intermediates"], sizes,
                         fee_tiers, tick_spacings, costs, eth_price, max_hops)
        r["cycles"].sort(key=lambda c: c["net_usd"], reverse=True)
        result["chains"].append(r)
    all_cycles = [c for ch in result["chains"] for c in ch["cycles"]]
    positives = [c for c in all_cycles if c["net_usd"] > float(cfg.get("min_net_usd", 1))]
    result["n_cycles"] = len(all_cycles)
    result["n_positive"] = len(positives)
    result["status"] = "OK"
    result["verdict"] = "CANDIDATES" if positives else "NO_TRADE"
    result["root_cause"] = _root_cause(all_cycles) if not positives else None
    return result


def _root_cause(cycles) -> dict:
    """Attribute why the best cycle is not profitable."""
    if not cycles:
        return {"summary": "无可报价闭环路径（流动性/池缺失）"}
    best = max(cycles, key=lambda c: c["net_usd"])
    n_usd = best["size_usd"]
    gross_bps = best["gross_usd"] / n_usd * 1e4
    gas_bps = best["gas_usd"] / n_usd * 1e4
    mev_bps = best["mev_usd"] / n_usd * 1e4
    fail_bps = best["fail_usd"] / n_usd * 1e4
    if gross_bps <= 0:
        primary = ("价差本身为负：闭环毛收益 <= 0，说明池间定价已被充分套利/竞争充分，"
                   "或大额下价格冲击吞没价差（流动性深度不足以承载该本金）")
    elif gross_bps <= gas_bps + mev_bps + fail_bps:
        primary = ("存在微小正价差，但被 Gas+MEV/失败成本吞没（价差 %.2fbps <= 成本 %.2fbps）"
                   % (gross_bps, gas_bps + mev_bps + fail_bps))
    else:
        primary = "毛价差超过显式成本，但仍未过阈值或受手续费/冲击侵蚀"
    return {"best_route": best["route"], "size_usd": n_usd,
            "gross_bps": round(gross_bps, 3), "gas_bps": round(gas_bps, 4),
            "mev_bps_est": round(mev_bps, 3), "fail_bps_est": round(fail_bps, 4),
            "primary_cause": primary,
            "note": ("费用与价格冲击已内含于可执行报价（无法再分离到 bps 级）；"
                     "MEV/失败为估计。价差为负主要归因于 DEX 市场高效与同资产池间竞争。")}


def to_markdown(rep: dict) -> str:
    L = ["# 图搜索闭环套利（三角/多跳）报告", "",
         "> 节点=代币，边=跨 venue/费率的可执行报价（同区块、含价格冲击）；DFS 枚举 USDC 起止闭环；"
         "扣全部成本后按净收益排序。禁止通过降低成本假设制造盈利。", ""]
    if rep.get("status") != "OK":
        L.append(f"状态: {rep.get('status')} — {rep.get('reason','')}"); return "\n".join(L)
    L.append(f"- 生成: {rep['generated_at']}  ETH=${rep['eth_price_usd']}  最大跳数: {rep['max_hops']}")
    L.append(f"- 闭环路径评估数: {rep['n_cycles']}  正净收益: {rep['n_positive']}  判定: **{rep['verdict']}**")
    L.append("")
    for ch in rep["chains"]:
        L.append(f"## {ch['chain']} (block {ch['block']}, venues {', '.join(ch['venues'])})")
        L.append("")
        L.append("| 路径 | 本金USD | 跳数 | 毛利USD | 总成本USD | 净USD | ROC% |")
        L.append("|------|---------|------|---------|-----------|-------|------|")
        for c in ch["cycles"][:12]:
            L.append(f"| {c['route']} | {c['size_usd']:,} | {c['legs']} | {c['gross_usd']} | "
                     f"{c['total_cost_usd']} | {c['net_usd']} | {c['roc_pct']} |")
        L.append("")
    if rep.get("root_cause"):
        rc = rep["root_cause"]
        L.append("## 根因分析（为何无正净收益）")
        L.append("")
        L.append(f"- 最优路径: `{rc.get('best_route')}`  本金 ${rc.get('size_usd'):,}")
        L.append(f"- 毛价差: {rc.get('gross_bps')} bps；Gas: {rc.get('gas_bps')} bps；"
                 f"MEV(估): {rc.get('mev_bps_est')} bps；失败(估): {rc.get('fail_bps_est')} bps")
        L.append(f"- **主因**: {rc.get('primary_cause')}")
        L.append(f"- {rc.get('note')}")
        L.append("")
        L.append("结论：**NO_TRADE**。真实数据下未发现正净收益闭环，不制造盈利结论。")
    return "\n".join(L)
