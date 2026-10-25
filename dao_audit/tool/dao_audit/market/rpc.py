"""Read-only EVM JSON-RPC client with freshness verification.

Supports Ethereum/Base/Arbitrum (any EVM RPC URL). Read-only methods only:
eth_blockNumber, eth_getBlockByNumber, eth_getBalance, eth_call, eth_getLogs.
It never sends transactions or signs anything. Each call is provenance-tagged
and freshness-checked (now - block.timestamp).
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional

from .provenance import Record, http_post_json, OK, STALE, UNAVAILABLE


class EvmRpc:
    def __init__(self, chain: str, url: str, max_staleness_s: float = 120.0):
        self.chain = chain
        self.url = url
        self.max_staleness_s = max_staleness_s
        self._id = 0

    def _call(self, method: str, params: list, timeout: float = 20.0) -> Record:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        rec = http_post_json(self.url, payload, source=f"rpc:{self.chain}",
                             timeout=timeout, chain=self.chain)
        if rec.status == OK and isinstance(rec.data, dict):
            if "error" in rec.data:
                rec.status = "ERROR"
                rec.error = f"rpc error: {rec.data['error']}"
            else:
                rec.data = rec.data.get("result")
        return rec

    @staticmethod
    def _to_int(hexstr) -> Optional[int]:
        try:
            return int(hexstr, 16)
        except (TypeError, ValueError):
            return None

    def block_number(self) -> Record:
        rec = self._call("eth_blockNumber", [])
        if rec.ok:
            rec.block = self._to_int(rec.data)
        return rec

    def chain_id(self) -> Record:
        rec = self._call("eth_chainId", [])
        if rec.ok:
            rec.data = self._to_int(rec.data)
        return rec

    def get_block(self, block="latest", full_tx=False) -> Record:
        tag = block if isinstance(block, str) else hex(block)
        rec = self._call("eth_getBlockByNumber", [tag, full_tx])
        if rec.ok and isinstance(rec.data, dict):
            rec.block = self._to_int(rec.data.get("number"))
            rec.block_timestamp = self._to_int(rec.data.get("timestamp"))
            if rec.block_timestamp is not None:
                now = int(datetime.now(timezone.utc).timestamp())
                rec.data_latency_s = now - rec.block_timestamp
                if rec.data_latency_s > self.max_staleness_s:
                    rec.status = STALE
                    rec.error = (f"block is {rec.data_latency_s:.0f}s old "
                                 f"(> {self.max_staleness_s:.0f}s staleness limit)")
        return rec

    def get_balance(self, address: str, block="latest") -> Record:
        tag = block if isinstance(block, str) else hex(block)
        rec = self._call("eth_getBalance", [address, tag])
        rec.address = address
        if rec.ok:
            rec.data = self._to_int(rec.data)  # wei
        return rec

    def eth_call(self, to: str, data: str, block="latest") -> Record:
        tag = block if isinstance(block, str) else hex(block)
        rec = self._call("eth_call", [{"to": to, "data": data}, tag])
        rec.address = to
        return rec

    def get_logs(self, params: dict) -> Record:
        return self._call("eth_getLogs", [params])

    def health(self) -> Record:
        """Combined reachability+freshness probe: latest block + its timestamp."""
        bn = self.block_number()
        if not bn.ok:
            return bn
        return self.get_block("latest")


import time as _time
from .provenance import OK as _OK, STALE as _STALE


class MultiRpc:
    """Ordered endpoints with 429 exponential backoff and failover on upstream
    errors. Read-only. Records which endpoint served and all attempt outcomes."""
    def __init__(self, chain: str, urls, max_staleness_s: float = 120.0,
                 backoff=(0.5, 1.0, 2.0)):
        self.chain = chain
        self.endpoints = [EvmRpc(chain, u, max_staleness_s=max_staleness_s) for u in urls if u]
        self.backoff = backoff

    def _try(self, method_name: str):
        attempts = []
        for ep in self.endpoints:
            # 429 backoff: retry the SAME endpoint a few times
            for i, wait in enumerate([0.0] + list(self.backoff)):
                if wait:
                    _time.sleep(wait)
                rec = getattr(ep, method_name)()
                code = rec.http_code
                is_429 = code == 429 or (rec.error and "429" in str(rec.error))
                attempts.append({"url": ep.url, "status": rec.status,
                                 "http": code, "try": i, "error": rec.error})
                if rec.status in (_OK, _STALE):
                    return rec, ep.url, attempts
                if not is_429:
                    break  # non-rate-limit failure -> failover to next endpoint
        last = attempts[-1] if attempts else {}
        from .provenance import Record, UNAVAILABLE, _now_iso
        return (Record(source=f"rpc:{self.chain}", url=last.get("url", ""),
                       status=UNAVAILABLE, fetched_at=_now_iso(), chain=self.chain,
                       error=f"all endpoints failed: {last.get('error')}"),
                None, attempts)

    def health(self):
        rec, served, attempts = self._try("health")
        return rec, served, attempts

    def chain_id(self):
        rec, served, attempts = self._try("chain_id")
        return rec, served, attempts
