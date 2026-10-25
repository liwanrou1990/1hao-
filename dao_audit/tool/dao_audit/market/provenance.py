"""Provenance + real HTTP/JSON-RPC with status, latency and freshness tagging.

Every datum carries where it came from and whether it is trustworthy. Nothing
is fabricated: a blocked/failed fetch yields status=UNAVAILABLE with the real
error string; a too-old datum yields status=STALE.
"""
from __future__ import annotations
import json
import ssl
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

CA_BUNDLE = "/root/.ccr/ca-bundle.crt"

# status values
OK = "OK"
UNAVAILABLE = "UNAVAILABLE"   # network blocked / fetch failed -> no data
STALE = "STALE"              # data older than allowed staleness
ERROR = "ERROR"              # malformed response


@dataclass
class Record:
    """A single provenance-tagged datum or the reason it is missing."""
    source: str                      # e.g. "defillama", "rpc:ethereum", "snapshot"
    url: str
    status: str                      # OK | UNAVAILABLE | STALE | ERROR
    fetched_at: str                  # ISO8601 UTC when the fetch was attempted
    latency_ms: Optional[float] = None
    http_code: Optional[int] = None
    chain: Optional[str] = None
    block: Optional[int] = None
    block_timestamp: Optional[int] = None
    data_latency_s: Optional[float] = None   # now - block_timestamp (freshness)
    address: Optional[str] = None
    tx_hash: Optional[str] = None
    data: Any = None
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.status == OK


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ctx() -> ssl.SSLContext:
    try:
        return ssl.create_default_context(cafile=CA_BUNDLE)
    except Exception:
        return ssl.create_default_context()


def _http(url: str, payload: Optional[dict], timeout: float, source: str,
          chain: Optional[str] = None) -> Record:
    """Perform a real GET/POST through the environment proxy. Never fabricates."""
    data_bytes = None
    headers = {"accept": "application/json", "user-agent": "dao-audit-market/0.1"}
    if payload is not None:
        data_bytes = json.dumps(payload).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data_bytes, headers=headers,
                                 method="POST" if payload is not None else "GET")
    # urllib honors HTTPS_PROXY/https_proxy from the environment automatically.
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
            latency = (time.monotonic() - t0) * 1000
            body = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return Record(source, url, ERROR, _now_iso(), latency_ms=latency,
                              http_code=resp.status, chain=chain,
                              error="non-JSON response", data=body[:200])
            return Record(source, url, OK, _now_iso(), latency_ms=latency,
                          http_code=resp.status, chain=chain, data=parsed)
    except urllib.error.HTTPError as e:
        latency = (time.monotonic() - t0) * 1000
        return Record(source, url, UNAVAILABLE, _now_iso(), latency_ms=latency,
                      http_code=e.code, chain=chain,
                      error=f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        # Blocked egress (CONNECT 403) surfaces here as URLError.
        return Record(source, url, UNAVAILABLE, _now_iso(), latency_ms=latency,
                      chain=chain, error=f"{type(e).__name__}: {e}")


def http_get_json(url: str, source: str, timeout: float = 20.0,
                  chain: Optional[str] = None) -> Record:
    return _http(url, None, timeout, source, chain)


def http_post_json(url: str, payload: dict, source: str, timeout: float = 20.0,
                   chain: Optional[str] = None) -> Record:
    return _http(url, payload, timeout, source, chain)
