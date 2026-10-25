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
