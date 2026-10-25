"""Live scan: record real DAO governance, treasury and market state, then run
the value + decision pipeline. Produces only real data (or UNAVAILABLE)."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import yaml

from .provenance import Record, OK
from .rpc import EvmRpc
from .sources import DefiLlamaSource, SnapshotSource, DexQuoteSource
from .costs import CostInputs, compute
from .decision import decide


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def scan(cfg: dict, edge_model: dict | None = None) -> dict:
    max_stale = cfg.get("max_staleness_s", 120)
    costs_cfg = cfg.get("costs", {})
    thr = cfg.get("thresholds", {})
    dl = DefiLlamaSource()
    snap = SnapshotSource()
    dex = DexQuoteSource(dl)

    out = {"generated_at": _now_iso(), "integrity":
           "real-data-only; unreachable sources -> UNAVAILABLE -> NO TRADE; no simulated quotes",
           "chains": {}, "daos": [], "tokens": [], "opportunities": []}

    # 1) chain health (reachability + freshness)
    rpcs = {}
    for chain, c in cfg.get("chains", {}).items():
        rpc = EvmRpc(chain, c["rpc"], max_staleness_s=max_stale)
        rpcs[chain] = rpc
        h = rpc.health()
        out["chains"][chain] = h.to_dict()

    # gas price (ethereum) for cost model — real or None
    eth_rpc = rpcs.get("ethereum")
    gas_price_gwei = None
    if eth_rpc:
        gp = eth_rpc._call("eth_gasPrice", [])
        if gp.ok:
            try:
                gas_price_gwei = int(gp.data, 16) / 1e9
            except (TypeError, ValueError):
                gas_price_gwei = None

    # 2) DAO governance + treasury
    for dao in cfg.get("daos", []):
        entry = {"name": dao["name"], "snapshot_space": dao.get("snapshot_space"),
                 "chain": dao.get("chain"), "treasury": dao.get("treasury")}
        prop = snap.recent_proposals(dao["snapshot_space"], first=5)
        entry["governance"] = prop.to_dict()
        chain = dao.get("chain")
        if chain in rpcs and dao.get("treasury"):
            bal = rpcs[chain].get_balance(dao["treasury"])
            entry["treasury_balance_wei"] = bal.to_dict()
        out["daos"].append(entry)

    # 3) token prices + DEX quote divergence
    eth_price_usd = None
    for tok in cfg.get("tokens", []):
        key = tok["key"]
        cmp = dex.compare(key)
        price_rec = dl.price_current([key])
        px = None
        if price_rec.ok and isinstance(price_rec.data, dict):
            info = price_rec.data.get("coins", {}).get(key)
            if info and "price" in info:
                px = float(info["price"])
                if tok.get("symbol") == "ETH":
                    eth_price_usd = px
        out["tokens"].append({"symbol": tok.get("symbol"), "key": key,
                              "price_usd": px, "quote_compare": cmp,
                              "price_record": price_rec.to_dict()})

    # 4) opportunities: edge (confirmed only) -> value -> TRADE/NO TRADE
    edge_model = edge_model or {}
    for tok in out["tokens"]:
        evidence = 0
        if tok["price_usd"] is not None:
            evidence += 1
        if tok["quote_compare"]["status"] == OK:
            evidence += 1
        chain_ok = any(c.get("status") == OK for c in out["chains"].values())
        data_status = OK if (tok["price_usd"] is not None and chain_ok) else "UNAVAILABLE"

        # Expected gross edge comes ONLY from a statistically-confirmed,
        # out-of-sample-positive event-edge estimate. No invented/forced edge.
        edge_entry = edge_model.get(tok["symbol"])
        edge_bps = edge_entry.get("edge_bps") if isinstance(edge_entry, dict) else None

        if edge_bps is None:
            out["opportunities"].append({
                "symbol": tok["symbol"], "data_status": data_status,
                "evidence_sources": evidence, "edge_bps": None, "cost": None,
                "decision": {"decision": "NO_TRADE",
                             "reason": "无样本外确认的正 event-edge（未建立或数据不足），不交易",
                             "net_bps": None, "evidence_sources": evidence,
                             "data_status": data_status,
                             "disclaimer": "估计值，非收益承诺；未经人工确认不执行真实交易。"}})
            continue

        ci = CostInputs(
            notional_usd=float(costs_cfg.get("trade_notional_usd", 10000)),
            expected_gross_bps=float(edge_bps),
            dex_fee_bps=float(costs_cfg.get("dex_fee_bps", 30)),
            slippage_bps=float(costs_cfg.get("taker_slippage_bps", 10)),
            gas_limit=int(costs_cfg.get("gas_limit", 180000)),
            gas_price_gwei=gas_price_gwei,
            eth_price_usd=eth_price_usd,
            pool_liquidity_usd=None,  # real DEX pool data (egress-gated) -> INSUFFICIENT_DATA
        )
        cost = compute(ci)
        dec = decide(cost, evidence_sources=evidence, data_status=data_status,
                     min_net_bps=float(thr.get("min_net_bps", 15)),
                     min_evidence_sources=int(thr.get("min_evidence_sources", 2)))
        out["opportunities"].append({
            "symbol": tok["symbol"], "data_status": data_status,
            "evidence_sources": evidence, "edge_bps": edge_bps,
            "cost": cost.to_dict(), "decision": dec.to_dict()})

    # overall integrity rollup
    any_ok = any(c.get("status") == OK for c in out["chains"].values()) or \
        any(t["price_usd"] is not None for t in out["tokens"])
    out["summary"] = {
        "any_real_data": any_ok,
        "trade_signals": sum(1 for o in out["opportunities"] if o["decision"]["decision"] == "TRADE"),
        "no_trade": sum(1 for o in out["opportunities"] if o["decision"]["decision"] == "NO_TRADE"),
        "note": ("本次扫描所有外部数据源是否可达见各记录 status；无真实数据时不产生交易信号。"),
    }
    return out
