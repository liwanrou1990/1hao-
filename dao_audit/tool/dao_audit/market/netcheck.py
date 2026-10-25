"""Phase-1 real-data connection verification.

Probes every configured data source and classifies the outcome precisely:
OK / NETWORK_POLICY_DENIED (egress proxy CONNECT 403) / PROXY_AUTH (407) /
API_AUTH (401/403 from the API) / RATE_LIMITED (429) / UPSTREAM_FAIL (5xx) /
TIMEOUT / CONNECT_FAIL. For RPCs it also reports block height, timestamp and
freshness; for data APIs it checks current and historical availability. It
never fabricates values; a denied host yields the real diagnosis plus the exact
cloud-permission change required.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from .provenance import Record, OK, UNAVAILABLE, STALE, ERROR
from .rpc import EvmRpc
from .sources import DefiLlamaSource, SnapshotSource, CoinGeckoSource


def _classify(rec: Record) -> str:
    if rec.status == OK:
        return "OK"
    err = (rec.error or "")
    if "response 403" in err or (rec.http_code is None and "403" in err):
        return "NETWORK_POLICY_DENIED"
    if "response 407" in err or rec.http_code == 407:
        return "PROXY_AUTH"
    if rec.http_code in (401, 403):
        return "API_AUTH"
    if rec.http_code == 429:
        return "RATE_LIMITED"
    if rec.http_code and 500 <= rec.http_code < 600:
        return "UPSTREAM_FAIL"
    if "timed out" in err.lower() or "timeout" in err.lower():
        return "TIMEOUT"
    if rec.status == STALE:
        return "STALE"
    if rec.status == ERROR:
        return "API_ERROR"
    return "CONNECT_FAIL"


def run(cfg: dict) -> dict:
    checks = []

    # EVM RPCs: reachability + block height + timestamp + freshness
    for chain, c in cfg.get("chains", {}).items():
        rpc = EvmRpc(chain, c["rpc"], max_staleness_s=cfg.get("max_staleness_s", 120))
        rec = rpc.health()
        checks.append({
            "name": f"RPC {chain}", "url": c["rpc"], "category": _classify(rec),
            "http_code": rec.http_code, "latency_ms": rec.latency_ms,
            "block": rec.block, "block_timestamp": rec.block_timestamp,
            "data_latency_s": rec.data_latency_s, "error": rec.error,
        })

    dl = DefiLlamaSource()
    cur = dl.price_current(["coingecko:ethereum"])
    checks.append({"name": "DefiLlama price (current)", "url": dl.COINS,
                   "category": _classify(cur), "http_code": cur.http_code,
                   "latency_ms": cur.latency_ms, "error": cur.error})
    hist_ts = int(datetime.now(timezone.utc).timestamp()) - 7 * 24 * 3600
    hist = dl.price_at("coingecko:ethereum", hist_ts)
    checks.append({"name": "DefiLlama price (historical)", "url": dl.COINS,
                   "category": _classify(hist), "http_code": hist.http_code,
                   "latency_ms": hist.latency_ms, "error": hist.error,
                   "historical_available": hist.status == OK})

    snap = SnapshotSource()
    sp = snap.recent_proposals(next(iter(
        [d["snapshot_space"] for d in cfg.get("daos", [])] or ["aave.eth"])), first=1)
    checks.append({"name": "Snapshot governance", "url": snap.URL,
                   "category": _classify(sp), "http_code": sp.http_code,
                   "latency_ms": sp.latency_ms, "error": sp.error})

    cg = CoinGeckoSource()
    pg = cg.ping()
    checks.append({"name": "CoinGecko ping", "url": cg.BASE,
                   "category": _classify(pg), "http_code": pg.http_code,
                   "latency_ms": pg.latency_ms, "error": pg.error})
    now = int(datetime.now(timezone.utc).timestamp())
    mc = cg.market_chart_range("ethereum", "usd", now - 7 * 24 * 3600, now)
    checks.append({"name": "CoinGecko market_chart (hist price+vol)", "url": cg.BASE,
                   "category": _classify(mc), "http_code": mc.http_code,
                   "latency_ms": mc.latency_ms, "error": mc.error,
                   "historical_available": mc.status == OK})

    cats = [c["category"] for c in checks]
    n_ok = sum(1 for x in cats if x == "OK")
    denied = sorted({checks[i]["url"].split("/")[2]
                     for i, x in enumerate(cats) if x == "NETWORK_POLICY_DENIED"})
    overall = "OK" if n_ok == len(checks) else (
        "NETWORK_POLICY_DENIED" if "NETWORK_POLICY_DENIED" in cats else "DEGRADED")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": overall, "ok": n_ok, "total": len(checks),
        "denied_hosts": denied, "checks": checks,
        "remediation": (
            "云环境出口策略拒绝了这些主机（代理 CONNECT 403）。在会话标题栏的云环境菜单→Edit→"
            "Network access 中放宽级别，或将被拒主机加入 Allowed domains（保留 Allow package "
            "managers 勾选）。策略在容器启动时固定，改动后需开新会话生效。"
            "文档 https://code.claude.com/docs/en/cloud-environments#network-access") if denied else "",
    }


def to_markdown(r: dict) -> str:
    L = ["# 真实数据连接报告 (Phase 1)", "",
         f"- 生成时间: {r['generated_at']}",
         f"- 总体: **{r['overall']}**  ({r['ok']}/{r['total']} OK)", ""]
    L += ["| 检查项 | 分类 | HTTP | 延迟ms | 区块 | 数据延迟s | 错误 |",
          "|--------|------|------|--------|------|-----------|------|"]
    for c in r["checks"]:
        L.append("| {n} | {cat} | {h} | {l} | {b} | {d} | {e} |".format(
            n=c["name"], cat=c["category"], h=c.get("http_code") or "-",
            l=round(c["latency_ms"]) if c.get("latency_ms") else "-",
            b=c.get("block") or "-", d=c.get("data_latency_s") if c.get("data_latency_s") is not None else "-",
            e=(c.get("error") or "")[:60]))
    if r["denied_hosts"]:
        L += ["", "## 被网络策略拒绝的主机", ""]
        L += [f"- `{h}`" for h in r["denied_hosts"]]
        L += ["", "## 需要的云环境权限修改", "", r["remediation"]]
    L += ["", "> 分类说明：NETWORK_POLICY_DENIED=出口代理 403（需改云环境网络权限）；"
          "API_AUTH=接口 401/403（需密钥/权限）；RATE_LIMITED=429 限速；"
          "UPSTREAM_FAIL=5xx 接口故障；STALE=数据过旧。网络不可用时不生成任何行情。"]
    return "\n".join(L)
