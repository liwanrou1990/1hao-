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


# --- Aerodrome Slipstream (concentrated liquidity, tickSpacing-based) ---
_SLIP_SIG = ("quoteExactInputSingle((address,address,uint256,int24,uint160))"
             "(uint256,uint160,uint32,uint256)")


def slipstream_quote(rpc, venue, quoter, token_in, token_out, amount_in,
                     tick_spacing, block=None) -> Quote:
    args = ["call", quoter, _SLIP_SIG,
            f"({token_in},{token_out},{amount_in},{tick_spacing},0)", "--rpc-url", rpc]
    if block is not None:
        args += ["--block", str(block)]
    out, lat, err = _run(args)
    amount_out = None; ok = False
    if out:
        try:
            amount_out = int(out.splitlines()[0].strip().split()[0]); ok = amount_out > 0
        except ValueError:
            err = f"parse: {out[:80]}"
    return Quote(venue=venue, token_in=token_in, token_out=token_out, amount_in=amount_in,
                 fee=tick_spacing, amount_out=amount_out, block=block, latency_ms=lat,
                 ok=ok, error=err, rpc=rpc)


def best_slipstream_quote(rpc, venue, quoter, token_in, token_out, amount_in,
                          tick_spacings, block=None) -> Quote:
    best = None
    for ts in tick_spacings:
        q = slipstream_quote(rpc, venue, quoter, token_in, token_out, amount_in, ts, block)
        if q.ok and (best is None or q.amount_out > best.amount_out):
            best = q
    return best or Quote(venue, token_in, token_out, amount_in, None, None, block, 0.0,
                         False, "no slipstream pool", rpc)


# --- Aerodrome / Velodrome classic (solidly) Router.getAmountsOut ---
_SOLIDLY_SIG = "getAmountsOut(uint256,(address,address,bool,address)[])(uint256[])"


def solidly_quote(rpc, venue, router, token_in, token_out, amount_in, stable,
                  factory, block=None) -> Quote:
    route = f"[({token_in},{token_out},{'true' if stable else 'false'},{factory})]"
    args = ["call", router, _SOLIDLY_SIG, str(amount_in), route, "--rpc-url", rpc]
    if block is not None:
        args += ["--block", str(block)]
    out, lat, err = _run(args)
    amount_out = None; ok = False
    if out:
        # cast prints an array like "[a, b]"; take the last element
        nums = [t for t in out.replace("[", " ").replace("]", " ").replace(",", " ").split()
                if t.lstrip("-").isdigit()]
        if nums:
            try:
                amount_out = int(nums[-1]); ok = amount_out > 0
            except ValueError:
                err = f"parse: {out[:80]}"
    return Quote(venue=venue, token_in=token_in, token_out=token_out, amount_in=amount_in,
                 fee=("stable" if stable else "volatile"), amount_out=amount_out, block=block,
                 latency_ms=lat, ok=ok, error=err, rpc=rpc)


def best_solidly_quote(rpc, venue, router, token_in, token_out, amount_in, factory, block=None) -> Quote:
    best = None
    for stable in (False, True):
        q = solidly_quote(rpc, venue, router, token_in, token_out, amount_in, stable, factory, block)
        if q.ok and (best is None or q.amount_out > best.amount_out):
            best = q
    return best or Quote(venue, token_in, token_out, amount_in, None, None, block, 0.0,
                         False, "no solidly pool", rpc)


def best_quote(venue_cfg: dict, rpc, token_in, token_out, amount_in, block,
               fee_tiers, tick_spacings):
    """Dispatch to the right quoter by venue type."""
    vt = venue_cfg["type"]; name = venue_cfg["name"]
    if vt == "univ3":
        return best_univ3_quote(rpc, name, venue_cfg["quoter"], token_in, token_out,
                                amount_in, fee_tiers, block)
    if vt == "slipstream":
        return best_slipstream_quote(rpc, name, venue_cfg["quoter"], token_in, token_out,
                                     amount_in, tick_spacings, block)
    if vt == "solidly":
        return best_solidly_quote(rpc, name, venue_cfg["router"], token_in, token_out,
                                  amount_in, venue_cfg["factory"], block)
    return Quote(name, token_in, token_out, amount_in, None, None, block, 0.0,
                 False, f"unknown venue type {vt}", rpc)
