"""Deterministic unit tests for the Multicall3 batched-quote ABI codec (no
network). These lock in the hand-rolled encode/decode and, critically, the
univ3-vs-Slipstream selector split: a regression there silently drops every
Slipstream quote from the batch (an invisible missed-detection bug the live
equivalence check caught once already)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from dao_audit.dex.fastquote import (  # noqa: E402
    enc_quote_inner, enc_aggregate3, _decode_results, _w,
    _SEL_QUOTE_UNIV3, _SEL_QUOTE_SLIP, MULTICALL3,
)

TIN = "0x4200000000000000000000000000000000000006"   # WETH (Base)
TOUT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC (Base)
AMT = 10 ** 18


def test_word_encoding():
    assert _w(0) == "0" * 64
    assert _w(1) == "0" * 63 + "1"
    # address low-20-bytes right-aligned
    assert _w(TIN).endswith("4200000000000000000000000000000000000006")
    assert _w(TIN)[:24] == "0" * 24


def test_univ3_vs_slipstream_selectors_differ_body_identical():
    u = enc_quote_inner(TIN, TOUT, AMT, 500, "univ3")
    s = enc_quote_inner(TIN, TOUT, AMT, 500, "slipstream")
    # selectors are the real on-chain 4-byte ids and MUST differ
    assert u[2:10] == _SEL_QUOTE_UNIV3 == "c6a5026a"
    assert s[2:10] == _SEL_QUOTE_SLIP == "9e7defe6"
    assert u[2:10] != s[2:10]
    # the tuple body (everything after the selector) is byte-identical:
    # a positive int24 tickSpacing encodes exactly like the uint24 fee word
    assert u[10:] == s[10:]
    assert enc_quote_inner(TIN, TOUT, AMT, 500).startswith("0x" + _SEL_QUOTE_UNIV3)  # default univ3


def test_quote_inner_reference_calldata():
    # exact 5-word static tuple: in, out, amount, fee, sqrtPriceLimit(0)
    cd = enc_quote_inner(TIN, TOUT, AMT, 500, "univ3")
    body = cd[10:]
    words = [body[i * 64:(i + 1) * 64] for i in range(5)]
    assert words[0].endswith("4200000000000000000000000000000000000006")
    assert words[1].lower().endswith("833589fcd6edb6e08f4c7c32d4f71b54bda02913")
    assert int(words[2], 16) == AMT
    assert int(words[3], 16) == 500
    assert int(words[4], 16) == 0
    assert len(body) == 5 * 64


def test_aggregate3_encoding_structure():
    calls = [(MULTICALL3, True, enc_quote_inner(TIN, TOUT, AMT, 500, "univ3")),
             (MULTICALL3, True, enc_quote_inner(TIN, TOUT, AMT, 100, "slipstream"))]
    enc = enc_aggregate3(calls)
    assert enc[2:10] == "82ad56cb"              # aggregate3 selector
    h = enc[10:]
    assert int(h[0:64], 16) == 0x20             # head: offset to dynamic array
    assert int(h[64:128], 16) == 2              # array length = 2 calls


def test_decode_results_roundtrip():
    # hand-built Result[] return blob: [(true, amountOut=123456789), (false, "")]
    amt = 123456789
    words = [
        _w(0x20),   # 0: offset to array
        _w(2),      # 1: length
        _w(0x40),   # 2: elem0 offset (rel to word 2)
        _w(0xc0),   # 3: elem1 offset
        _w(1),      # 4: result0.success = true
        _w(0x40),   # 5: result0.bytes offset (rel to word 4)
        _w(32),     # 6: result0.bytes length
        _w(amt),    # 7: result0.bytes data (amountOut)
        _w(0),      # 8: result1.success = false
        _w(0x40),   # 9: result1.bytes offset
        _w(0),      # 10: result1.bytes length (empty)
    ]
    blob = "0x" + "".join(words)
    out = _decode_results(blob)
    assert len(out) == 2
    assert out[0][0] is True
    assert int(out[0][1][:64], 16) == amt
    assert out[1][0] is False
    assert out[1][1] == ""


def test_decode_empty_or_short():
    assert _decode_results("0x") == []
    assert _decode_results("0x" + "00" * 10) == []
