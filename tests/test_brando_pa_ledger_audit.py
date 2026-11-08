"""费用 / 净值一致性只读审计（2026-11-08）：公式恒等式、逐笔算术复核能发现重复计费、事件链重放与 state/nav 一致、只读（不改任何账户文件）、
重复成交能被发现、Mac 报告的 10008 → 9993 差额 = 假设清仓成本 15 bps。"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from brando_pa.forward.ledger_audit import audit_ledger_account, check_fill_arithmetic, ledger_audit, replay_ledger
from brando_pa.spot.ledger import SpotLedger
from brando_pa.trendsys.paper import _append
from brando_pa.usdt.engine import liquidation_value

REAL = Path("reports_out/brando_pa/phase8_forward")


def _tree_hashes(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def test_fee_formulas_single_charge_and_pnl_identity():
    L = SpotLedger(10000.0, 10.0, 5.0); ref = 75865.5
    amt = 10000.0 * 0.999; px = ref * 1.0005 * 1.001; q = amt / px
    lot = L.buy("BTCUSDT", q, ref, "t0", "test", 0.0, 0.0, 0.0)
    assert lot.price == pytest.approx(ref * 1.0005)                       # 买入成交价 = 参考 × (1 + 5 bps)
    assert L.fees_paid == pytest.approx(lot.price * q * 0.001)             # 手续费 = 成交价 × 数量 × 10 bps，只收一次
    assert L.cash == pytest.approx(10000.0 - amt)                          # 现金减少 = 成交额 + 手续费 = 订单金额
    mark = {"BTCUSDT": 76040.9, "ETHUSDT": 0.0}; eq = L.equity(mark); liq = liquidation_value(L, mark)
    assert eq - 10000.0 == pytest.approx(L.realized + L.unrealized(mark) - L.fees_paid)          # 恒等式：权益 − 初始 = 已实现 + 未实现 − 买入手续费
    assert eq - liq == pytest.approx(q * mark["BTCUSDT"] * (1 - 0.9995 * 0.999))                 # 清仓差额 = 假设卖出滑点 + 手续费（15 bps − 交叉项）
    rec = L.sell("BTCUSDT", q, mark["BTCUSDT"], "t1", "exit")
    assert rec["price"] == pytest.approx(mark["BTCUSDT"] * 0.9995) and rec["fee"] == pytest.approx(rec["price"] * q * 0.001)
    assert L.cash == pytest.approx(liq)                                    # 真实卖出后现金 = 之前的清仓估值 → 口径一致，无重复计费
    assert L.qty("BTCUSDT") == 0 and L.equity(mark) == pytest.approx(L.cash)


def test_fill_arithmetic_detects_double_fee_and_wrong_slippage():
    f = {"side": "BUY", "qty": 0.1, "reference_price": 1000.0, "execution_price": 1000.5, "fee": 0.10005, "cash_before": 1000.0, "cash_after": 1000.0 - (100.05 + 0.10005), "symbol": "X", "position_before": {"X": 0.0}, "position_after": {"X": 0.1}}
    assert check_fill_arithmetic(f, 10.0, 5.0) == []
    assert any("fee" in i["check"] for i in check_fill_arithmetic(f | {"fee": 0.2001}, 10.0, 5.0))                       # 手续费翻倍
    assert any("cash" in i["check"] for i in check_fill_arithmetic(f | {"cash_after": f["cash_after"] - 0.10005}, 10.0, 5.0))  # 现金多扣一次手续费
    assert any("slippage" in i["check"] for i in check_fill_arithmetic(f | {"execution_price": 1001.0}, 10.0, 5.0))      # 滑点 10 bps ≠ 5 bps


def test_real_cloud_copy_consistent_and_audit_is_read_only(tmp_path):
    if not (REAL / "A_BTC_SMA200" / "events.jsonl").exists():
        pytest.skip("no forward records")
    before = _tree_hashes(REAL)
    rep = ledger_audit(REAL, tmp_path / "out", write=True)
    assert _tree_hashes(REAL) == before                                    # 只读：账户目录任何文件未变
    assert rep["ok"], rep
    for a in rep["accounts"].values():
        for k, l in a["ledgers"].items():
            assert l["arithmetic_issues"] == [] and l["state_mismatch"] == {} and l["nav_mismatch"] == {} and l["identity"]["ok"]
            assert l["exit_cost_if_liquidated_usdt"] == pytest.approx(l["equity_usdt"] - l["liquidation_usdt"])
        assert a["signals"]["duplicate_fills_per_order"] == {} and a["signals"]["causality_violations"] == [] and a["signals"]["duplicate_executed_decisions"] == {}
    assert (tmp_path / "out").exists() and len(list((tmp_path / "out").glob("ledger_audit_*.md"))) == 1
    assert not any("acceptance_status" in w for w in rep.get("written", []))


def test_duplicate_fill_is_detected(tmp_path):
    if not (REAL / "A_BTC_SMA200" / "events.jsonl").exists():
        pytest.skip("no forward records")
    d = tmp_path / "A_BTC_SMA200"; shutil.copytree(REAL / "A_BTC_SMA200", d)
    fills = [json.loads(l) for l in open(d / "events.jsonl") if '"fill"' in l]
    f = {k: v for k, v in fills[-1].items() if k not in ("prev_hash", "hash", "recorded_at")}
    _append(d / "events.jsonl", f)                                        # 同一 order_id 第二次成交（链仍有效）
    a = audit_ledger_account(d)
    assert not a["ok"] and a["signals"]["duplicate_fills_per_order"] and any(i["type"] == "duplicate_fill" for i in a["issues"])
    assert any(i["type"] in ("state_vs_replayed_fills", "replay_violates_spot_constraints") for i in a["issues"])  # 重放后与 state 不一致或违反现货约束（现金不足）


def test_mac_reported_gap_10008_vs_9993_equals_hypothetical_exit_cost():
    """Mac 报告：cost 账本权益 ≈ 10008、清仓 ≈ 9993。用真实成交（cash 10、0.13148312 BTC @ 75903.43）反推标记价，差额应为持仓市值 × (1 − 0.9995 × 0.999) ≈ 15 USDT。"""
    L = replay_ledger([{"side": "BUY", "symbol": "BTCUSDT", "qty": 0.1314831176725664, "reference_price": 75865.5, "execution_price": 75903.43275, "fee": 9.98002, "reason": "x", "fill_ts": "t"}], 10000.0, 10.0, 5.0)
    assert L.cash == pytest.approx(10.0, abs=1e-6)
    P = (10008.0 - L.cash) / L.qty("BTCUSDT"); mark = {"BTCUSDT": P, "ETHUSDT": 0.0}
    assert L.equity(mark) == pytest.approx(10008.0)
    liq = liquidation_value(L, mark)
    assert 10008.0 - liq == pytest.approx(9998.0 * (1 - 0.9995 * 0.999), rel=1e-9)   # ≈ 14.99 USDT
    assert liq == pytest.approx(9993.0, abs=0.05)
    assert L.unrealized(mark) - L.fees_paid == pytest.approx(8.0, abs=1e-6)         # 权益口径利润 = 未实现 − 买入手续费
