"""Executable on-chain DEX quotes via `cast` (read-only eth_call).

Every quote is the amount a swap of a SPECIFIC input size would actually
return (QuoterV2 simulates the swap, so price impact is already included).
Quotes are pinned to one block so both legs of a round trip are consistent.
A missing pool / failed call returns None (never a fabricated number).
"""
from __future__ import annotations
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional


def _cast_bin() -> str:
    c = shutil.which("cast") or "/root/.foundry/bin/cast"
    return c


def _run(args: List[str], timeout: float = 25.0):
    t0 = time.monotonic()
    try:
        p = subprocess.run([_cast_bin()] + args, capture_output=True, text=True, timeout=timeout)
        lat = (time.monotonic() - t0) * 1000
        if p.returncode != 0:
            return None, lat, (p.stderr or p.stdout).strip()[:160]
        return p.stdout.strip(), lat, None
    except Exception as e:
        return None, (time.monotonic() - t0) * 1000, f"{type(e).__name__}: {e}"


def block_number(rpc: str) -> Optional[int]:
    out, _, err = _run(["block-number", "--rpc-url", rpc])
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


def gas_price_wei(rpc: str) -> Optional[int]:
    out, _, err = _run(["gas-price", "--rpc-url", rpc])
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


@dataclass
class Quote:
    venue: str
    token_in: str
    token_out: str
    amount_in: int
    fee: Optional[int]
    amount_out: Optional[int]
    block: Optional[int]
    latency_ms: float
    ok: bool
    error: Optional[str] = None
    rpc: str = ""

    def to_dict(self):
        return self.__dict__


_Q_SIG = ("quoteExactInputSingle((address,address,uint256,uint24,uint160))"
          "(uint256,uint160,uint32,uint256)")


def univ3_quote(rpc: str, venue: str, quoter: str, token_in: str, token_out: str,
                amount_in: int, fee: int, block: Optional[int] = None) -> Quote:
    args = ["call", quoter, _Q_SIG,
            f"({token_in},{token_out},{amount_in},{fee},0)", "--rpc-url", rpc]
    if block is not None:
        args += ["--block", str(block)]
    out, lat, err = _run(args)
    amount_out = None
    ok = False
    if out:
        first = out.splitlines()[0].strip().split()[0]
        try:
            amount_out = int(first)
            ok = amount_out > 0
        except ValueError:
            err = f"parse: {out[:80]}"
    return Quote(venue=venue, token_in=token_in, token_out=token_out,
                 amount_in=amount_in, fee=fee, amount_out=amount_out, block=block,
                 latency_ms=lat, ok=ok, error=err, rpc=rpc)


def best_univ3_quote(rpc, venue, quoter, token_in, token_out, amount_in,
                     fee_tiers, block=None) -> Quote:
    """Best executable quote across fee tiers for one venue."""
    best = None
    for fee in fee_tiers:
        q = univ3_quote(rpc, venue, quoter, token_in, token_out, amount_in, fee, block)
        if q.ok and (best is None or q.amount_out > best.amount_out):
            best = q
    return best or Quote(venue=venue, token_in=token_in, token_out=token_out,
                         amount_in=amount_in, fee=None, amount_out=None, block=block,
                         latency_ms=0.0, ok=False, error="no pool/quote on any fee tier",
                         rpc=rpc)
