"""每日 UTC 账户报告与三账户 USDT 排行榜（第九阶段；2026-11-08 F1/F3 口径改进）。

数据来自各账户事件链（nav / fill / signal / missed_signal）与服务状态；每个数字标明类别：forward（真正前瞻）/ replay（历史回放）/ backtest（不含）。

口径（与账本 SpotLedger / liquidation_value 完全一致，报告只做展示与对账，不改账本）：
  当前账面权益   = 现金 + Σ 持仓 × 标记价（最新已收盘 1h 收盘价）
  当前账面利润   = 当前账面权益 − 初始本金
  预计清仓余额   = 现金 + Σ 持仓 × 标记价 × (1 − 滑点) × (1 − 费率)   ← 卖出成本只在“假设此刻全部卖出”时计一次，尚未发生
  预计清仓净利润 = 预计清仓余额 − 初始本金
  已实现利润     = Σ(卖出成交价 − 批次成本) × 数量 − 卖出手续费（卖出时记入）
  未实现利润     = Σ(标记价 − 批次成交价) × 持仓数量（批次成交价含买入滑点、不含买入手续费）
  买入手续费 / 买入滑点：成交时已从现金扣除一次；卖出手续费估计 / 卖出滑点估计：仅假设清仓时发生
  对账：当前账面权益 = 初始本金 + 已实现 + 未实现 − 买入手续费；预计清仓余额 = 当前账面权益 − 卖出滑点估计 − 卖出手续费估计
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from .ledger_audit import fee_breakdown, replay_ledger

SYMS = ["BTCUSDT", "ETHUSDT"]
TOL = 1e-6
EXIT_COST_NOTE = "预计清仓余额 = 当前账面权益 − 假设卖出成本（滑点 5 bps + 手续费 10 bps，≈ 持仓市值 × 15 bps）；该成本尚未发生，买入成本已在成交时扣除一次、不重复"
QUOTE_RULE_NOTE = "前瞻成交参考价：BUY 用实时 Ask、SELL 用实时 Bid（报价年龄 ≤ 120 s），无新鲜报价时用最新已收盘 1h 收盘价并标记 estimated；账本再按预登记加 5 bps 滑点 + 10 bps 手续费（Ask/Bid 已含半价差，叠加后比预登记略保守，BTC/ETH 半价差约 0.01–0.05 bps；规则与参数保持不变）"


def _events(p: Path) -> pd.DataFrame:
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_json(p, lines=True)


def _fills_until(ev: pd.DataFrame, k: str, until: pd.Timestamp | None) -> list[dict]:
    if ev.empty or "kind" not in ev:
        return []
    f = ev[(ev["kind"] == "fill") & (ev["ledger"] == k)]
    if until is not None and len(f):
        f = f[pd.to_datetime(f["fill_ts"], utc=True) <= until]
    return [{c: r[c] for c in f.columns if not (isinstance(r[c], float) and pd.isna(r[c]))} for _, r in f.iterrows()]


def account_breakdown(ev: pd.DataFrame, st: dict, k: str, prices: dict, until: pd.Timestamp | None = None) -> dict:
    """F1/F3：从事件链成交重放账本，输出权益 / 利润 / 清仓 / 费用明细 / 对账（不读不写账本文件）。"""
    initial = float(st.get("initial_usdt", 10000.0)); fee = 0.0 if k == "ideal" else float(st.get("fee_bps", 10.0)); slip = 0.0 if k == "ideal" else float(st.get("slippage_bps", 5.0))
    fills = _fills_until(ev, k, until); L = replay_ledger(fills, initial, fee, slip)
    px = {s: float(prices.get(s, 0.0)) for s in SYMS}
    eq = L.equity(px); fb = fee_breakdown(fills, L, px, fee, slip); inc, hyp = fb["incurred"], fb["hypothetical_liquidation"]
    liq = eq - hyp["sell_slippage_usdt"] - hyp["sell_fee_usdt"]
    cost_basis = sum(l.qty * l.price for ls in L.lots.values() for l in ls)                       # 当前持仓成本（按批次成交价，含买入滑点）
    recon_equity = initial + L.realized + L.unrealized(px) - inc["buy_fee_usdt"]
    out = {"initial_usdt": initial, "fee_bps": fee, "slippage_bps": slip, "n_fills": len(fills),
           "book_equity_usdt": eq, "book_profit_usdt": eq - initial, "est_liquidation_usdt": liq, "est_liquidation_net_profit_usdt": liq - initial,
           "realized_usdt": L.realized, "unrealized_usdt": L.unrealized(px), "cash_usdt": L.cash, "holdings": {s: L.qty(s) for s in SYMS}, "holding_cost_basis_usdt": cost_basis, "holding_market_value_usdt": hyp["gross_holdings_usdt"],
           "buy_fee_usdt": inc["buy_fee_usdt"], "buy_slippage_usdt": inc["buy_slippage_usdt"], "sell_fee_paid_usdt": inc["sell_fee_usdt"], "sell_slippage_paid_usdt": inc["sell_slippage_usdt"],
           "est_sell_fee_usdt": hyp["sell_fee_usdt"], "est_sell_slippage_usdt": hyp["sell_slippage_usdt"], "half_spread_usdt_live_quotes": inc["half_spread_usdt_on_live_quotes"],
           "incurred_cost_usdt": inc["total_incurred_usdt"], "est_exit_cost_usdt": hyp["total_usdt"], "total_trading_cost_usdt": inc["total_incurred_usdt"] + hyp["total_usdt"],
           "replay_errors": getattr(L, "replay_errors", []) or [],
           "reconciliation": {"formula_equity": "当前账面权益 = 初始本金 + 已实现 + 未实现 − 买入手续费（买入滑点已含在批次成本中，不再扣）", "equity_lhs": eq, "equity_rhs": recon_equity, "equity_ok": abs(eq - recon_equity) < TOL * max(1.0, abs(eq)),
                              "formula_liquidation": "预计清仓余额 = 当前账面权益 − 卖出滑点估计 − 卖出手续费估计", "liquidation_lhs": liq, "liquidation_rhs": eq - hyp["sell_slippage_usdt"] - hyp["sell_fee_usdt"], "liquidation_ok": True,
                              "formula_profit": "预计清仓净利润 = 当前账面利润 − 预计清仓成本", "profit_ok": abs((liq - initial) - ((eq - initial) - hyp["total_usdt"])) < TOL * max(1.0, abs(eq)),
                              "no_double_deduction": "买入手续费与买入滑点只在成交时从现金扣除一次；清仓估值只对仍持有的数量计一次卖出成本"}}
    return out


def _load_account(acc_dir: Path):
    ev = _events(acc_dir / "events.jsonl"); st = json.loads((acc_dir / "state.json").read_text()) if (acc_dir / "state.json").exists() else {}
    return ev, st


def daily_report(log_dir: Path, day: str | None = None, data_status: dict | None = None, health: dict | None = None, out_dir: Path | None = None) -> dict:
    log_dir = Path(log_dir); out_dir = Path(out_dir or (log_dir / "daily")); out_dir.mkdir(parents=True, exist_ok=True)
    day_ts = pd.Timestamp(day, tz="UTC") if day else pd.Timestamp.now(tz="UTC").normalize()
    day_end = day_ts + pd.Timedelta(days=1); prev_end = day_ts
    rep = {"date_utc": str(day_ts.date()), "category": "forward (真正前瞻模拟；不含历史回测/回放)", "accounts": {}, "data_status": {k: data_status.get(k) for k in ("source", "last_success_utc", "stale", "quality_fault", "source_switch")} if data_status else None, "service": health,
           "notes": {"exit_cost": EXIT_COST_NOTE, "quote_rule": QUOTE_RULE_NOTE}}
    eq = {}
    for acc_dir in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev, st = _load_account(acc_dir)
        nav = ev[ev["kind"] == "nav"].copy() if "kind" in ev else pd.DataFrame()
        if nav.empty:
            continue
        nav["t"] = pd.to_datetime(nav["ts"], utc=True)
        at_end = nav[nav["t"] <= day_end]; at_prev = nav[nav["t"] <= prev_end]
        if at_end.empty:
            continue
        last = at_end.iloc[-1]; first_nav = nav.iloc[0]; prices = last.get("prices") or {}
        fills = ev[(ev["kind"] == "fill")] if "kind" in ev else pd.DataFrame()
        day_fills = fills[(pd.to_datetime(fills["fill_ts"], utc=True) >= day_ts) & (pd.to_datetime(fills["fill_ts"], utc=True) < day_end)] if len(fills) else fills
        missed = ev[ev["kind"] == "missed_signal"] if "kind" in ev else pd.DataFrame()
        a = {"label": acc_dir.name, "forward_start_utc": str(ev.iloc[0].get("forward_start_utc") or first_nav["ts"]), "as_of": str(last["ts"]), "mark_prices": prices, "ledgers": {}}
        for k in ("cost", "ideal"):
            Lv = last["ledgers"][k]; P = at_prev.iloc[-1]["ledgers"][k] if not at_prev.empty else None
            bd = account_breakdown(ev, st, k, prices, until=pd.Timestamp(last["ts"]))
            nav_consistent = abs(bd["book_equity_usdt"] - float(Lv["equity_usdt"])) < TOL * max(1.0, abs(bd["book_equity_usdt"])) and abs(bd["est_liquidation_usdt"] - float(Lv["liquidation_usdt"])) < TOL * max(1.0, abs(bd["est_liquidation_usdt"]))
            a["ledgers"][k] = {"final_equity_usdt": Lv["equity_usdt"], "book_equity_usdt": Lv["equity_usdt"], "book_profit_usdt": Lv["profit_usdt"], "liquidation_usdt": Lv["liquidation_usdt"], "est_liquidation_usdt": Lv["liquidation_usdt"], "est_liquidation_net_profit_usdt": Lv["liquidation_usdt"] - bd["initial_usdt"],
                               "day_pnl_usdt": (Lv["equity_usdt"] - P["equity_usdt"]) if P else Lv["equity_usdt"] - bd["initial_usdt"], "cum_profit_usdt": Lv["profit_usdt"], "realized_usdt": Lv.get("realized_usdt"), "unrealized_usdt": Lv.get("unrealized_usdt"), "max_drawdown": Lv["max_drawdown"], "max_drawdown_usdt": Lv["max_drawdown_usdt"],
                               "weights": Lv["weights"] | {"USDT": Lv["cash"] / Lv["equity_usdt"] if Lv["equity_usdt"] else 1.0}, "holdings": Lv["holdings"], "cash": Lv["cash"], "cum_fees_usdt": Lv["fees_paid"], "n_trades_today": int((day_fills["ledger"] == k).sum()) if len(day_fills) else 0,
                               "costs": {kk: bd[kk] for kk in ("buy_fee_usdt", "buy_slippage_usdt", "sell_fee_paid_usdt", "sell_slippage_paid_usdt", "est_sell_fee_usdt", "est_sell_slippage_usdt", "half_spread_usdt_live_quotes", "holding_cost_basis_usdt", "holding_market_value_usdt", "incurred_cost_usdt", "est_exit_cost_usdt", "total_trading_cost_usdt")},
                               "reconciliation": bd["reconciliation"] | {"nav_consistent_with_replayed_fills": nav_consistent}}
            eq.setdefault(k, {})[acc_dir.name] = Lv["equity_usdt"]
        a["risk_state"] = last.get("risk_state"); a["n_missed_signals_total"] = int(len(missed)); a["source"] = last.get("source")
        rep["accounts"][acc_dir.name] = a
    for k, d in eq.items():
        for n in d:
            rep["accounts"][n]["ledgers"][k]["diff_vs_others_usdt"] = {m: d[n] - d[m] for m in d if m != n}
    atomic_write_json(out_dir / f"{rep['date_utc']}.json", rep, indent=1, default=str)
    L = [f"# 前瞻账户日报 {rep['date_utc']} (UTC) · 类别：{rep['category']}", "", "## 权益与利润", "", "| 账户 | 账本 | 当前账面权益 | 当前账面利润 | 预计清仓余额 | 预计清仓净利润 | 已实现利润 | 未实现利润 | 当日盈亏 | 最大回撤 | BTC/ETH/USDT 比例 | 当日交易 | 风险状态 | 相对其他账户 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        for k, v in a["ledgers"].items():
            w = v["weights"]; L.append(f"| {n} | {k} | {v['book_equity_usdt']:,.2f} | {v['book_profit_usdt']:+,.2f} | {v['est_liquidation_usdt']:,.2f} | {v['est_liquidation_net_profit_usdt']:+,.2f} | {(v['realized_usdt'] or 0):+,.2f} | {(v['unrealized_usdt'] or 0):+,.2f} | {v['day_pnl_usdt']:+,.2f} | {v['max_drawdown']*100:.2f}% | {w.get('BTCUSDT', 0)*100:.0f}/{w.get('ETHUSDT', 0)*100:.0f}/{w.get('USDT', 0)*100:.0f} | {v['n_trades_today']} | {a['risk_state']} | {', '.join(f'{m}: {x:+,.0f}' for m, x in v.get('diff_vs_others_usdt', {}).items())} |")
    L += ["", "## 交易成本明细（已发生 vs 假设清仓估计）", "", "| 账户 | 账本 | 买入手续费 | 买入滑点 | 卖出手续费（已付） | 卖出滑点（已付） | 卖出手续费估计 | 卖出滑点估计 | 当前持仓成本 | 持仓市值 | 已发生成本 | 预计清仓成本 | 交易成本合计 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        for k, v in a["ledgers"].items():
            c = v["costs"]; L.append(f"| {n} | {k} | {c['buy_fee_usdt']:.2f} | {c['buy_slippage_usdt']:.2f} | {c['sell_fee_paid_usdt']:.2f} | {c['sell_slippage_paid_usdt']:.2f} | {c['est_sell_fee_usdt']:.2f} | {c['est_sell_slippage_usdt']:.2f} | {c['holding_cost_basis_usdt']:,.2f} | {c['holding_market_value_usdt']:,.2f} | {c['incurred_cost_usdt']:.2f} | {c['est_exit_cost_usdt']:.2f} | {c['total_trading_cost_usdt']:.2f} |")
    L += ["", "## 权益计算对账", "", "| 账户 | 账本 | 初始本金 + 已实现 + 未实现 − 买入手续费 | 当前账面权益 | 一致 | 账面权益 − 卖出滑点估计 − 卖出手续费估计 | 预计清仓余额 | 一致 | nav 与成交重放一致 |", "|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        for k, v in a["ledgers"].items():
            r = v["reconciliation"]; L.append(f"| {n} | {k} | {r['equity_rhs']:,.4f} | {r['equity_lhs']:,.4f} | {'是' if r['equity_ok'] else '否'} | {r['liquidation_rhs']:,.4f} | {r['liquidation_lhs']:,.4f} | {'是' if r['profit_ok'] else '否'} | {'是' if r['nav_consistent_with_replayed_fills'] else '否'} |")
    L += ["", f"- {EXIT_COST_NOTE}", f"- {QUOTE_RULE_NOTE}", "- 未实现利润以批次成交价（含买入滑点、不含买入手续费）为成本；已实现利润已扣卖出手续费；买入手续费只体现在现金里，因此“已实现 + 未实现 − 买入手续费 = 当前账面利润”。", "", f"数据状态：{rep['data_status']}", f"服务状态：{rep['service']}"]
    atomic_write_text(out_dir / f"{rep['date_utc']}.md", "\n".join(L))
    return rep


def leaderboard(log_dir: Path, out_dir: Path | None = None) -> dict:
    """三账户 USDT 排行榜（真正前瞻）：按含成本账本“预计清仓净利润”从高到低。数字来自各账户最新 nav 事件，并用事件链成交重放做对账。"""
    log_dir = Path(log_dir); out_dir = Path(out_dir or log_dir); rows = []
    for acc_dir in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev, st = _load_account(acc_dir)
        if ev.empty or "kind" not in ev:
            continue
        nav = ev[ev["kind"] == "nav"]
        if nav.empty:
            continue
        last = nav.iloc[-1]; initial = float(st.get("initial_usdt", 10000.0)); prices = last.get("prices") or {}
        for k in ("cost", "ideal"):
            Lv = last["ledgers"][k]; eq = Lv["equity_usdt"]; liq = Lv["liquidation_usdt"]; w = Lv["weights"]
            bd = account_breakdown(ev, st, k, prices, until=pd.Timestamp(last["ts"]))
            rows.append({"account": acc_dir.name, "ledger": k, "category": "forward", "as_of_utc": str(last["ts"]), "initial_usdt": initial,
                         "book_equity_usdt": eq, "book_profit_usdt": eq - initial, "est_liquidation_usdt": liq, "est_liquidation_net_profit_usdt": liq - initial,
                         "equity_usdt": eq, "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - initial,                       # 旧字段名保留，兼容已有读取方
                         "realized_usdt": Lv.get("realized_usdt"), "unrealized_usdt": Lv.get("unrealized_usdt"), "max_drawdown": Lv["max_drawdown"], "max_drawdown_usdt": Lv["max_drawdown_usdt"],
                         "weights": {"BTC": w.get("BTCUSDT", 0.0), "ETH": w.get("ETHUSDT", 0.0), "USDT": (Lv["cash"] / eq) if eq else 1.0}, "holdings": Lv["holdings"], "cash_usdt": Lv["cash"], "fees_paid_usdt": Lv["fees_paid"], "risk_state": last.get("risk_state"), "prices": prices,
                         "costs": {kk: bd[kk] for kk in ("buy_fee_usdt", "buy_slippage_usdt", "sell_fee_paid_usdt", "sell_slippage_paid_usdt", "est_sell_fee_usdt", "est_sell_slippage_usdt", "half_spread_usdt_live_quotes", "holding_cost_basis_usdt", "holding_market_value_usdt", "incurred_cost_usdt", "est_exit_cost_usdt", "total_trading_cost_usdt")},
                         "reconciliation": bd["reconciliation"] | {"nav_consistent_with_replayed_fills": abs(bd["book_equity_usdt"] - eq) < TOL * max(1.0, abs(eq)) and abs(bd["est_liquidation_usdt"] - liq) < TOL * max(1.0, abs(liq))}})
    cost = sorted([r for r in rows if r["ledger"] == "cost"], key=lambda r: -r["est_liquidation_net_profit_usdt"]); ideal = sorted([r for r in rows if r["ledger"] == "ideal"], key=lambda r: -r["est_liquidation_net_profit_usdt"])
    for i, r in enumerate(cost, 1):
        r["rank"] = i
    rep = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "forward（真正前瞻；含成本 = 10 bps 手续费 + 5 bps 滑点；零成本仅对照）", "ranking_key": "cost ledger est_liquidation_net_profit_usdt desc（预计清仓净利润）",
           "notes": {"exit_cost": EXIT_COST_NOTE, "quote_rule": QUOTE_RULE_NOTE}, "cost": cost, "ideal": ideal, "real_trading": "disabled (no implementation)"}
    atomic_write_json(out_dir / "leaderboard.json", rep, indent=1, default=str)
    L = [f"# 三账户 USDT 利润排行榜（含成本，真正前瞻）· {rep['generated_at_utc'][:19]} UTC", "", "排名依据：**预计清仓净利润**（含成本账本）从高到低。", "",
         "| 名次 | 账户 | 当前账面权益 | 当前账面利润 | 预计清仓余额 | 预计清仓净利润 | 已实现利润 | 未实现利润 | 最大回撤 | BTC/ETH/USDT | 风险状态 | 数据时点 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in cost:
        w = r["weights"]; L.append(f"| {r['rank']} | {r['account']} | {r['book_equity_usdt']:,.2f} | {r['book_profit_usdt']:+,.2f} | {r['est_liquidation_usdt']:,.2f} | {r['est_liquidation_net_profit_usdt']:+,.2f} | {(r['realized_usdt'] or 0):+,.2f} | {(r['unrealized_usdt'] or 0):+,.2f} | {r['max_drawdown']*100:.2f}% | {w['BTC']*100:.0f}/{w['ETH']*100:.0f}/{w['USDT']*100:.0f} | {r['risk_state']} | {r['as_of_utc'][:16]} |")
    L += ["", "## 交易成本明细（含成本账本；已发生 vs 假设清仓估计）", "", "| 账户 | 买入手续费 | 买入滑点 | 卖出手续费估计 | 卖出滑点估计 | 当前持仓成本 | 持仓市值 | 已发生成本 | 预计清仓成本 | 交易成本合计 | 对账：本金+已实现+未实现−买入手续费 = 账面权益 | 账面权益−清仓估计成本 = 预计清仓余额 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in cost:
        c = r["costs"]; rc = r["reconciliation"]
        L.append(f"| {r['account']} | {c['buy_fee_usdt']:.2f} | {c['buy_slippage_usdt']:.2f} | {c['est_sell_fee_usdt']:.2f} | {c['est_sell_slippage_usdt']:.2f} | {c['holding_cost_basis_usdt']:,.2f} | {c['holding_market_value_usdt']:,.2f} | {c['incurred_cost_usdt']:.2f} | {c['est_exit_cost_usdt']:.2f} | {c['total_trading_cost_usdt']:.2f} | {rc['equity_rhs']:,.2f} = {rc['equity_lhs']:,.2f} {'✓' if rc['equity_ok'] and rc['nav_consistent_with_replayed_fills'] else '✗'} | {rc['liquidation_rhs']:,.2f} = {rc['liquidation_lhs']:,.2f} {'✓' if rc['profit_ok'] else '✗'} |")
    L += ["", f"- {EXIT_COST_NOTE}", f"- {QUOTE_RULE_NOTE}", "", "零成本对照（仅供比较，不是可实际获得的收益）：", "", "| 账户 | 当前账面权益 | 当前账面利润 | 预计清仓净利润 | 最大回撤 |", "|---|---|---|---|---|"] + [f"| {r['account']} | {r['book_equity_usdt']:,.2f} | {r['book_profit_usdt']:+,.2f} | {r['est_liquidation_net_profit_usdt']:+,.2f} | {r['max_drawdown']*100:.2f}% |" for r in ideal]
    L += ["", "初始本金各 10,000 USDT；真实交易 API 禁用（无实现）。"]
    atomic_write_text(out_dir / "leaderboard.md", "\n".join(L)); return rep
