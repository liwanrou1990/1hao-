"""Batched executable quotes via Multicall3 (one eth_call per batch).

Collapses many QuoterV2 / Slipstream quoteExactInputSingle calls into a single
Multicall3.aggregate3 eth_call, sent with a raw JSON-RPC POST (no `cast`
subprocess per quote). This is the core performance fix: N quotes cost one RPC
round-trip instead of N. Pure ABI encoding/decoding, no external libs.

Still read-only (eth_call). Missing/failed inner calls decode to None — never
fabricated.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..market.provenance import http_post_json

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"  # same address on Base & Arbitrum
# Univ3-style QuoterV2 and Aerodrome Slipstream share the tuple LAYOUT but NOT
# the selector: the 4th field is uint24 (fee) vs int24 (tickSpacing), so the
# canonical signature — and thus the selector — differs. The encoded tuple body
# is byte-identical (a positive int24 encodes exactly like the uint24).
_SEL_QUOTE_UNIV3 = "c6a5026a"  # quoteExactInputSingle((address,address,uint256,uint24,uint160))
_SEL_QUOTE_SLIP = "9e7defe6"   # quoteExactInputSingle((address,address,uint256,int24,uint160))
_SEL_SOLIDLY = "5509a1ac"      # getAmountsOut(uint256,(address,address,bool,address)[])
_SEL_QUOTE = _SEL_QUOTE_UNIV3  # back-compat default
_SEL_AGG3 = "82ad56cb"         # aggregate3((address,bool,bytes)[])

_SEL_BY_VTYPE = {"univ3": _SEL_QUOTE_UNIV3, "slipstream": _SEL_QUOTE_SLIP}


def _w(x) -> str:
    """32-byte hex word (no 0x) for an int or 0x-address/hex."""
    if isinstance(x, str):
        x = int(x, 16)
    return f"{x & ((1 << 256) - 1):064x}"


def enc_quote_inner(token_in: str, token_out: str, amount_in: int, fee_or_tick: int,
                    vtype: str = "univ3") -> str:
    """quoteExactInputSingle static-tuple calldata (0x-prefixed). The tuple body
    is identical for univ3 (fee uint24) and Slipstream (tickSpacing int24); only
    the selector differs, chosen by `vtype`."""
    sel = _SEL_BY_VTYPE.get(vtype, _SEL_QUOTE_UNIV3)
    return "0x" + sel + _w(token_in) + _w(token_out) + _w(amount_in) + \
        _w(fee_or_tick) + _w(0)


def enc_solidly_inner(token_in: str, token_out: str, amount_in: int,
                      stable: bool, factory: str) -> str:
    """getAmountsOut(uint256 amountIn, Route[] routes) calldata for a single-hop
    Route=(from,to,stable,factory). amountIn is static; routes is a dynamic array
    of a static 4-field tuple, so: amountIn, offset(0x40), length(1), route words."""
    return "0x" + _SEL_SOLIDLY + _w(amount_in) + _w(0x40) + _w(1) + \
        _w(token_in) + _w(token_out) + _w(1 if stable else 0) + _w(factory)


def _decode_u256_array_last(data_hex: str):
    """Decode a uint256[] return blob and take the LAST element (solidly
    getAmountsOut returns per-hop amounts; the output is the final one)."""
    d = data_hex
    if len(d) < 128:
        return None
    off = int(d[0:64], 16) // 32           # word index of the length word
    n = int(d[off * 64:(off + 1) * 64], 16)
    if n <= 0:
        return None
    last_idx = off + n                      # words: [len][a0]..[a_{n-1}]
    seg = d[last_idx * 64:(last_idx + 1) * 64]
    if len(seg) < 64:
        return None
    try:
        return int(seg, 16)
    except ValueError:
        return None


def enc_aggregate3(calls: List[Tuple[str, bool, str]]) -> str:
    """Encode Multicall3.aggregate3(Call3[]) where Call3=(address,bool,bytes).
    calls = [(target, allowFailure, calldata_hex)]."""
    n = len(calls)
    # element encodings (each: target, allow, offset(0x60), len, data)
    elems = []
    for target, allow, cd in calls:
        data = cd[2:] if cd.startswith("0x") else cd
        b = bytes.fromhex(data)
        ln = len(b)
        padded = data + "00" * ((32 - ln % 32) % 32) if ln % 32 else data
        elem = _w(target) + _w(1 if allow else 0) + _w(0x60) + _w(ln) + padded
        elems.append(elem)
    # offsets to each element, relative to start of the offsets region
    offsets = []
    running = n * 32  # bytes: the offsets array itself
    for e in elems:
        offsets.append(_w(running))
        running += len(e) // 2
    body = _w(n) + "".join(offsets) + "".join(elems)
    # head: one word = offset (0x20) to the dynamic array
    return "0x" + _SEL_AGG3 + _w(0x20) + body


def _decode_results(ret_hex: str) -> List[Tuple[bool, str]]:
    """Decode aggregate3 return: Result[] where Result=(bool success, bytes data)."""
    h = ret_hex[2:] if ret_hex.startswith("0x") else ret_hex
    if len(h) < 64:
        return []
    def word(i):  # i-th 32-byte word as int
        return int(h[i * 64:(i + 1) * 64], 16)
    arr_off = word(0) // 32
    n = word(arr_off)
    out = []
    base = arr_off + 1  # first element-offset word index
    for i in range(n):
        elem_off = word(base + i) // 32 + base
        success = word(elem_off) != 0
        bytes_off = word(elem_off + 1) // 32 + elem_off
        blen = word(bytes_off)
        start = (bytes_off + 1) * 64
        data = h[start:start + blen * 2]
        out.append((success, data))
    return out


@dataclass
class BatchResult:
    amounts: List[Optional[int]]
    ok: bool
    latency_ms: float
    block: Optional[int]
    error: Optional[str] = None
    n_calls: int = 0


def batch_quote_univ3(rpc: str, reqs: List[dict], block: Optional[int] = None) -> BatchResult:
    """reqs: [{quoter, token_in, token_out, amount_in, fee}]. Returns amountOut
    per request (None if that inner call failed), in one eth_call."""
    def _inner(r):
        if r.get("vtype") == "solidly":
            return enc_solidly_inner(r["token_in"], r["token_out"], r["amount_in"],
                                     r.get("stable", False), r["factory"])
        return enc_quote_inner(r["token_in"], r["token_out"], r["amount_in"],
                               r["fee"], r.get("vtype", "univ3"))

    def _target(r):
        return r.get("router") or r["quoter"]

    calls = [(_target(r), True, _inner(r)) for r in reqs]
    data = enc_aggregate3(calls)
    tag = hex(block) if isinstance(block, int) else (block or "latest")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": MULTICALL3, "data": data}, tag]}
    t0 = time.monotonic()
    rec = http_post_json(rpc, payload, source="multicall3")
    lat = (time.monotonic() - t0) * 1000
    if not rec.ok or not isinstance(rec.data, dict) or "result" not in rec.data:
        err = rec.error
        if isinstance(rec.data, dict) and "error" in rec.data:
            err = str(rec.data["error"])
        return BatchResult([None] * len(reqs), False, lat, block, err, len(reqs))
    results = _decode_results(rec.data["result"])
    amounts: List[Optional[int]] = []
    for i in range(len(reqs)):
        if i < len(results) and results[i][0] and len(results[i][1]) >= 64:
            if reqs[i].get("vtype") == "solidly":
                amounts.append(_decode_u256_array_last(results[i][1]))
            else:
                try:
                    amounts.append(int(results[i][1][:64], 16))
                except ValueError:
                    amounts.append(None)
        else:
            amounts.append(None)
    return BatchResult(amounts, True, lat, block, None, len(reqs))
