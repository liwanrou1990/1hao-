"""Per-endpoint RPC verification: chain id match, block height, timestamp,
latency, and consecutive-request success rate. Also exercises failover/backoff
via MultiRpc. Alchemy endpoints are included only if their env URL is set
(never fabricated)."""
from __future__ import annotations
import os
from datetime import datetime, timezone

from .rpc import EvmRpc, MultiRpc


def _endpoints(chain_cfg: dict):
    urls = list(chain_cfg.get("rpcs") or ([chain_cfg["rpc"]] if chain_cfg.get("rpc") else []))
    env = chain_cfg.get("alchemy_env")
    alchemy = os.environ.get(env) if env else None
    note = None
    if alchemy:
        urls.append(alchemy)
    elif env:
        note = f"Alchemy 备用未激活：环境变量 {env} 未设置（且需放行 *.g.alchemy.com）"
    return urls, note


def run(cfg: dict, samples: int = 5) -> dict:
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "chains": []}
    for chain, c in cfg.get("chains", {}).items():
        expected_cid = c.get("chain_id")
        urls, note = _endpoints(c)
        eps = []
        for url in urls:
            rpc = EvmRpc(chain, url, max_staleness_s=cfg.get("max_staleness_s", 120))
            ok = 0; lat = []; cid = None; block = None; ts = None; errs = []
            for _ in range(samples):
                r = rpc.block_number()
                if r.ok:
                    ok += 1
                    if r.latency_ms:
                        lat.append(r.latency_ms)
                    block = r.block
                else:
                    errs.append(r.error or f"HTTP {r.http_code}")
            cr = rpc.chain_id()
            if cr.ok:
                cid = cr.data
            br = rpc.get_block("latest")
            if br.ok:
                ts = br.block_timestamp
            eps.append({
                "url": url, "success_rate": ok / samples,
                "chain_id": cid, "chain_id_match": (cid == expected_cid) if (cid and expected_cid) else None,
                "block": block, "block_timestamp": ts,
                "data_latency_s": br.data_latency_s if br.ok else None,
                "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
                "errors": errs[:3],
            })
        # failover health via MultiRpc
        mr = MultiRpc(chain, urls, max_staleness_s=cfg.get("max_staleness_s", 120))
        hrec, served, attempts = mr.health()
        healthy = [e["url"] for e in eps if e["success_rate"] >= 0.6 and e["chain_id_match"] in (True, None)]
        out["chains"].append({
            "chain": chain, "expected_chain_id": expected_cid,
            "endpoints": eps, "alchemy_note": note,
            "failover_served_by": served, "failover_status": hrec.status,
            "failover_attempts": attempts,
            "healthy_primary": healthy[0] if healthy else None,
        })
    return out


def to_markdown(r: dict) -> str:
    L = ["# RPC 多节点验证报告", "", f"- 生成时间: {r['generated_at']}", ""]
    for ch in r["chains"]:
        L.append(f"## {ch['chain']} (expected chainId={ch['expected_chain_id']})")
        L.append("")
        L.append("| 端点 | 成功率 | chainId | 匹配 | 区块 | 数据延迟s | 平均延迟ms | 错误 |")
        L.append("|------|--------|---------|------|------|-----------|------------|------|")
        for e in ch["endpoints"]:
            L.append("| {u} | {sr:.0%} | {cid} | {m} | {b} | {d} | {l} | {err} |".format(
                u=e["url"], sr=e["success_rate"], cid=e["chain_id"],
                m={True: "✓", False: "✗", None: "?"}[e["chain_id_match"]],
                b=e["block"] or "-", d=e["data_latency_s"] if e["data_latency_s"] is not None else "-",
                l=e["avg_latency_ms"] or "-", err=(e["errors"][0][:40] if e["errors"] else "")))
        L.append("")
        L.append(f"- 失败切换实际由: `{ch['failover_served_by'] or '全部失败'}` 提供 (status={ch['failover_status']})")
        L.append(f"- 建议主用端点: `{ch['healthy_primary'] or '无健康端点'}`")
        if ch["alchemy_note"]:
            L.append(f"- {ch['alchemy_note']}")
        L.append("")
    return "\n".join(L)
