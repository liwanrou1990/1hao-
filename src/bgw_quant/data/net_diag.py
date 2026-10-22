"""网络/接口诊断：DNS、代理、TLS、时钟、签名、限频；确认接口归属（Bitget Wallet ToB OpenAPI，不是 Bitget 交易所）。密钥绝不写入输出。"""
from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import get_settings

API_FAMILY = {
    "bitget_wallet_tob_openapi": {
        "base_url": "https://bopenapi.bgwapi.io",
        "docs": "https://web3.bitget.com/zh-CN/docs/market/rwa",
        "endpoints": ["/bgw-pro/market/v3/rwa/stockList", "/bgw-pro/market/v3/rwa/stockInfo", "/bgw-pro/market/v3/rwa/kline",
                      "/bgw-pro/market/v3/rwa/transactionList", "/bgw-pro/market/v3/rwa/checkContractIsRwa",
                      "/bgw-pro/swapx/order/getSwapPrice（只读可执行报价）", "/bgw-pro/swapx/order/checkOrdersFee（只读费用）"],
        "auth": "x-api-key / x-api-timestamp(ms) / x-api-signature = base64(HMAC-SHA256(secret, sortedJSON{apiPath, body, query…, x-api-key, x-api-timestamp}))",
        "not_used": "Bitget 交易所 API（api.bitget.com，ACCESS-KEY/ACCESS-SIGN/ACCESS-PASSPHRASE）——与本系统无关，不混用",
    }
}
REQUIRED_DOMAINS = [
    ("bopenapi.bgwapi.io", "Bitget Wallet ToB OpenAPI（行情/只读报价）", "必需"),
    ("web3.bitget.com", "官方文档站", "建议"),
    ("api.massive.com", "Massive 原生美股分钟线基准", "可选"),
    ("api.bitget.com", "Bitget 交易所公开现货 K 线（BTC/ETH/SOL/HYPE 行情，无需密钥；仅行情，不含交易）", "加密货币行情必需"),
]


def _dns(host: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        ip = socket.gethostbyname(host)
        return {"host": host, "ok": True, "ip": ip, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"host": host, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _https(host: str, path: str = "/") -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"https://{host}{path}")
        return {"host": host, "ok": True, "status": r.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1), "tls": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"host": host, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def run_diagnostics() -> dict[str, Any]:
    s = get_settings()
    rep: dict[str, Any] = {"ts_utc": datetime.now(timezone.utc).isoformat(), "api_family": API_FAMILY,
                           "configured_base_url": s.bgw_base_url, "has_key": bool(s.bgw_api_key), "has_secret": bool(s.bgw_api_secret),
                           "proxy": {"https_proxy_set": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
                                     "no_proxy": (os.environ.get("NO_PROXY") or "")[:120]},
                           "dns": [_dns(h) for h, _, _ in REQUIRED_DOMAINS], "https": [_https(h) for h, _, _ in REQUIRED_DOMAINS],
                           "clock": {"local_ms": int(time.time() * 1000), "note": "签名时间戳为本机毫秒；若服务端返回时间戳过期错误需做 NTP 校时"},
                           "rate_limit": {"assumed_orders_per_min": 10, "note": "官方未公开限频；客户端遇 429 退避重试，引擎按每分钟子单数限制"},
                           "required_domains": [{"domain": d, "purpose": p, "level": lvl} for d, p, lvl in REQUIRED_DOMAINS]}
    blocked = [d["host"] for d in rep["https"] if not d["ok"]]
    rep["blocked_domains"] = blocked
    rep["conclusion"] = ("网络可达" if not blocked else
                         f"以下域名在当前环境不可达（代理 CONNECT 被策略拒绝/DNS 无解析）：{', '.join(blocked)}。需在云环境 Network access 放行 "
                         f"{', '.join(d for d, _, lvl in REQUIRED_DOMAINS if '必需' in lvl)}（建议同时放行文档站）")
    rep["secrets_in_output"] = False
    return rep
