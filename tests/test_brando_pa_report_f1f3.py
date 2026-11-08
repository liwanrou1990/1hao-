"""F1/F3（2026-11-08 用户批准）：排行榜按预计清仓净利润排序并区分账面权益 / 账面利润 / 预计清仓余额 / 预计清仓净利润 / 已实现 / 未实现；
日报与排行榜含买入手续费、买入滑点、卖出手续费估计、卖出滑点估计、当前持仓成本、交易成本合计与对账公式；买入成本不重复扣除；
Markdown 数字与 JSON 可复核；报告只读账户目录；旧格式成交（无 reference_price）不致崩溃；三账户 nav 与成交重放一致。"""
import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.forward.report import account_breakdown, daily_report, leaderboard
from brando_pa.trendsys.paper import _append
from tests.test_brando_pa_acceptance_report import _drive

REAL = Path("reports_out/brando_pa/phase8_forward")
COST_KEYS = ("buy_fee_usdt", "buy_slippage_usdt", "est_sell_fee_usdt", "est_sell_slippage_usdt", "holding_cost_basis_usdt", "total_trading_cost_usdt")


def _tree_hashes(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file() and p.parent.name != "daily" and p.name not in ("leaderboard.json", "leaderboard.md")}


def _check_rows(rows, fee_bps, slip_bps):
    for r in rows:
        c = r["costs"]; rc = r["reconciliation"]
        assert all(k in c for k in COST_KEYS) and rc["equity_ok"] and rc["profit_ok"] and rc["nav_consistent_with_replayed_fills"], r["account"]
        assert r["book_profit_usdt"] == pytest.approx(r["book_equity_usdt"] - r["initial_usdt"])
        assert r["est_liquidation_net_profit_usdt"] == pytest.approx(r["est_liquidation_usdt"] - r["initial_usdt"])
        # 不重复扣除：账面利润 = 已实现 + 未实现 − 买入手续费；预计清仓净利润 = 账面利润 − 预计清仓成本（只对持仓计一次卖出成本）
        assert r["book_profit_usdt"] == pytest.approx(r["realized_usdt"] + r["unrealized_usdt"] - c["buy_fee_usdt"], abs=1e-6)
        assert r["est_liquidation_net_profit_usdt"] == pytest.approx(r["book_profit_usdt"] - c["est_exit_cost_usdt"], abs=1e-6)
        gross = c["holding_market_value_usdt"]
        assert c["est_sell_slippage_usdt"] == pytest.approx(gross * slip_bps / 1e4, abs=1e-9) and c["est_sell_fee_usdt"] == pytest.approx(gross * (1 - slip_bps / 1e4) * fee_bps / 1e4, abs=1e-9)
        assert c["total_trading_cost_usdt"] == pytest.approx(c["incurred_cost_usdt"] + c["est_exit_cost_usdt"])
        assert c["incurred_cost_usdt"] == pytest.approx(c["buy_fee_usdt"] + c["sell_fee_paid_usdt"] + c["buy_slippage_usdt"] + c["sell_slippage_paid_usdt"])
        if r["ledger"] == "ideal":
            assert c["total_trading_cost_usdt"] == 0 and r["est_liquidation_usdt"] == pytest.approx(r["book_equity_usdt"])


def test_leaderboard_ranks_by_est_liquidation_net_profit_and_reconciles(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"
    lj = leaderboard(d); cost = lj["cost"]
    assert "预计清仓净利润" in lj["ranking_key"] and [x["rank"] for x in cost] == [1, 2, 3]
    assert all(cost[i]["est_liquidation_net_profit_usdt"] >= cost[i + 1]["est_liquidation_net_profit_usdt"] for i in range(2))
    _check_rows(cost, 10.0, 5.0); _check_rows(lj["ideal"], 0.0, 0.0)
    md = (d / "leaderboard.md").read_text()
    for col in ("当前账面权益", "当前账面利润", "预计清仓余额", "预计清仓净利润", "已实现利润", "未实现利润", "买入手续费", "买入滑点", "卖出手续费估计", "卖出滑点估计", "当前持仓成本", "交易成本合计", "对账"):
        assert col in md
    assert "尚未发生" in md and "Ask" in md and "Bid" in md                   # F1 清仓成本说明 + F2 规则说明


def test_markdown_numbers_match_json(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"; lj = leaderboard(d); md = (d / "leaderboard.md").read_text().splitlines()
    for x in lj["cost"]:
        row = next(l for l in md if l.startswith(f"| {x['rank']} | {x['account']} |")); cells = [c.strip() for c in row.strip("|").split("|")]
        assert float(cells[2].replace(",", "")) == pytest.approx(x["book_equity_usdt"], abs=0.005) and float(cells[4].replace(",", "")) == pytest.approx(x["est_liquidation_usdt"], abs=0.005)
        assert float(cells[5].replace(",", "")) == pytest.approx(x["est_liquidation_net_profit_usdt"], abs=0.005)
        crow = next(l for l in md if l.startswith(f"| {x['account']} |") and "✓" in l); cc = [c.strip() for c in crow.strip("|").split("|")]
        assert float(cc[1]) == pytest.approx(x["costs"]["buy_fee_usdt"], abs=0.005) and float(cc[2]) == pytest.approx(x["costs"]["buy_slippage_usdt"], abs=0.005)
        assert float(cc[5].replace(",", "")) == pytest.approx(x["costs"]["holding_cost_basis_usdt"], abs=0.005)


def test_daily_report_has_costs_and_reconciliation(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"
    rep = daily_report(d, "2024-08-03"); j = json.loads((d / "daily" / "2024-08-03.json").read_text())
    for a in j["accounts"].values():
        for k, v in a["ledgers"].items():
            assert all(kk in v["costs"] for kk in COST_KEYS) and v["reconciliation"]["equity_ok"] and v["reconciliation"]["profit_ok"] and v["reconciliation"]["nav_consistent_with_replayed_fills"]
            assert v["book_profit_usdt"] == pytest.approx(v["book_equity_usdt"] - 10000.0) and v["est_liquidation_net_profit_usdt"] == pytest.approx(v["est_liquidation_usdt"] - 10000.0)
            assert "买入手续费" in v["reconciliation"]["formula_equity"]
    md = (d / "daily" / "2024-08-03.md").read_text()
    assert "权益计算对账" in md and "交易成本明细" in md and "当前持仓成本" in md and "尚未发生" in md


def test_real_three_accounts_consistent_and_reports_read_only(tmp_path):
    if not (REAL / "A_BTC_SMA200" / "events.jsonl").exists():
        pytest.skip("no forward records")
    before = _tree_hashes(REAL)
    lj = leaderboard(REAL, tmp_path); dj = daily_report(REAL, "2026-11-08", out_dir=tmp_path)
    assert _tree_hashes(REAL) == before                                      # 账户文件未被报告触碰
    assert len(lj["cost"]) == 3 and len(dj["accounts"]) == 3
    _check_rows(lj["cost"], 10.0, 5.0); _check_rows(lj["ideal"], 0.0, 0.0)
    for x in lj["cost"]:                                                       # 三账户权益一致：nav 权益 = 成交重放权益 = 现金 + 持仓 × 标记价
        assert x["book_equity_usdt"] == pytest.approx(x["cash_usdt"] + sum(x["holdings"][s] * x["prices"][s] for s in ("BTCUSDT", "ETHUSDT")), abs=1e-6)
        assert x["costs"]["buy_fee_usdt"] == pytest.approx(9.98, abs=0.01) and x["costs"]["est_exit_cost_usdt"] == pytest.approx(x["costs"]["holding_market_value_usdt"] * (1 - 0.9995 * 0.999), abs=1e-6)


def test_legacy_fill_without_reference_price_does_not_crash(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs" / "A_BTC_SMA200"
    ev = pd.read_json(d / "events.jsonl", lines=True); st = json.loads((d / "state.json").read_text())
    f = ev[ev["kind"] == "fill"].iloc[-1].to_dict(); f = {k: v for k, v in f.items() if k not in ("prev_hash", "hash", "recorded_at", "reference_price", "quote", "price_kind") and not (isinstance(v, float) and pd.isna(v))}
    f["order_id"] = f["order_id"] + ":legacy"
    _append(d / "events.jsonl", f)                                             # 旧格式：无 reference_price / quote / price_kind
    ev2 = pd.read_json(d / "events.jsonl", lines=True); prices = ev2[ev2["kind"] == "nav"].iloc[-1]["prices"]
    bd = account_breakdown(ev2, st, f["ledger"], prices)
    assert bd["n_fills"] >= 2 and "buy_fee_usdt" in bd and isinstance(bd["reconciliation"]["equity_ok"], bool)
