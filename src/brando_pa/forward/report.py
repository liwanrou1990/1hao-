"""每日 UTC 账户报告（第九阶段）：从各账户事件链（nav / fill / signal / missed_signal）与服务状态汇总，写 daily/YYYY-MM-DD.json + .md。
每个数字标明类别：forward（真正前瞻）/ replay（历史回放）/ backtest（历史回测，本报告不含）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text


def _events(p: Path) -> pd.DataFrame:
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_json(p, lines=True)


def daily_report(log_dir: Path, day: str | None = None, data_status: dict | None = None, health: dict | None = None, out_dir: Path | None = None) -> dict:
    log_dir = Path(log_dir); out_dir = Path(out_dir or (log_dir / "daily")); out_dir.mkdir(parents=True, exist_ok=True)
    day_ts = pd.Timestamp(day, tz="UTC") if day else pd.Timestamp.now(tz="UTC").normalize()
    day_end = day_ts + pd.Timedelta(days=1); prev_end = day_ts
    rep = {"date_utc": str(day_ts.date()), "category": "forward (真正前瞻模拟；不含历史回测/回放)", "accounts": {}, "data_status": {k: data_status.get(k) for k in ("source", "last_success_utc", "stale", "quality_fault", "source_switch")} if data_status else None, "service": health}
    eq = {}
    for acc_dir in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev = _events(acc_dir / "events.jsonl")
        if ev.empty or "mode" not in ev or not (ev.get("mode", pd.Series(dtype=object)) != "replay").any():
            pass
        nav = ev[ev["kind"] == "nav"].copy() if "kind" in ev else pd.DataFrame()
        if nav.empty:
            continue
        nav["t"] = pd.to_datetime(nav["ts"], utc=True)
        at_end = nav[nav["t"] <= day_end]; at_prev = nav[nav["t"] <= prev_end]
        if at_end.empty:
            continue
        last = at_end.iloc[-1]; first_nav = nav.iloc[0]
        fills = ev[(ev["kind"] == "fill")] if "kind" in ev else pd.DataFrame()
        day_fills = fills[(pd.to_datetime(fills["fill_ts"], utc=True) >= day_ts) & (pd.to_datetime(fills["fill_ts"], utc=True) < day_end)] if len(fills) else fills
        missed = ev[ev["kind"] == "missed_signal"] if "kind" in ev else pd.DataFrame()
        a = {"label": acc_dir.name, "forward_start_utc": str(ev.iloc[0].get("forward_start_utc") or first_nav["ts"]), "as_of": str(last["ts"]), "ledgers": {}}
        for k in ("cost", "ideal"):
            L = last["ledgers"][k]; P = at_prev.iloc[-1]["ledgers"][k] if not at_prev.empty else None
            a["ledgers"][k] = {"final_equity_usdt": L["equity_usdt"], "liquidation_usdt": L["liquidation_usdt"], "day_pnl_usdt": (L["equity_usdt"] - P["equity_usdt"]) if P else L["equity_usdt"] - 10000.0, "cum_profit_usdt": L["profit_usdt"], "realized_usdt": L.get("realized_usdt"), "unrealized_usdt": L.get("unrealized_usdt"), "max_drawdown": L["max_drawdown"], "max_drawdown_usdt": L["max_drawdown_usdt"],
                               "weights": L["weights"] | {"USDT": L["cash"] / L["equity_usdt"] if L["equity_usdt"] else 1.0}, "holdings": L["holdings"], "cash": L["cash"], "cum_fees_usdt": L["fees_paid"], "n_trades_today": int((day_fills["ledger"] == k).sum()) if len(day_fills) else 0}
            eq.setdefault(k, {})[acc_dir.name] = L["equity_usdt"]
        a["risk_state"] = last.get("risk_state"); a["n_missed_signals_total"] = int(len(missed)); a["source"] = last.get("source")
        rep["accounts"][acc_dir.name] = a
    for k, d in eq.items():
        for n in d:
            rep["accounts"][n]["ledgers"][k]["diff_vs_others_usdt"] = {m: d[n] - d[m] for m in d if m != n}
    atomic_write_json(out_dir / f"{rep['date_utc']}.json", rep, indent=1, default=str)
    L = [f"# 前瞻账户日报 {rep['date_utc']} (UTC) · 类别：{rep['category']}", "", "| 账户 | 账本 | 期末权益 USDT | 清仓 USDT | 当日盈亏 | 累计净利润 | 最大回撤 | BTC/ETH/USDT 比例 | 当日交易 | 累计成本 | 风险状态 | 相对其他账户 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        for k, v in a["ledgers"].items():
            w = v["weights"]; L.append(f"| {n} | {k} | {v['final_equity_usdt']:,.2f} | {v['liquidation_usdt']:,.2f} | {v['day_pnl_usdt']:+,.2f} | {v['cum_profit_usdt']:+,.2f} | {v['max_drawdown']*100:.2f}% | {w.get('BTCUSDT', 0)*100:.0f}/{w.get('ETHUSDT', 0)*100:.0f}/{w.get('USDT', 0)*100:.0f} | {v['n_trades_today']} | {v['cum_fees_usdt']:.2f} | {a['risk_state']} | {', '.join(f'{m}: {x:+,.0f}' for m, x in v.get('diff_vs_others_usdt', {}).items())} |")
    L += ["", f"数据状态：{rep['data_status']}", f"服务状态：{rep['service']}"]
    atomic_write_text(out_dir / f"{rep['date_utc']}.md", "\n".join(L))
    return rep


def leaderboard(log_dir: Path, out_dir: Path | None = None) -> dict:
    """三账户 USDT 排行榜（真正前瞻）：按含成本账本“假设全部清仓后的净利润”从高到低。数据来自各账户最新 nav 事件。"""
    log_dir = Path(log_dir); out_dir = Path(out_dir or log_dir); rows = []
    for acc_dir in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev = _events(acc_dir / "events.jsonl")
        if ev.empty or "kind" not in ev:
            continue
        nav = ev[ev["kind"] == "nav"]
        if nav.empty:
            continue
        last = nav.iloc[-1]; st = json.loads((acc_dir / "state.json").read_text()) if (acc_dir / "state.json").exists() else {}
        initial = float(st.get("initial_usdt", 10000.0))
        for k in ("cost", "ideal"):
            L = last["ledgers"][k]; eq = L["equity_usdt"]; liq = L["liquidation_usdt"]; w = L["weights"]
            rows.append({"account": acc_dir.name, "ledger": k, "category": "forward", "as_of_utc": str(last["ts"]), "initial_usdt": initial, "equity_usdt": eq, "liquidation_usdt": liq, "liquidation_net_profit_usdt": liq - initial, "realized_usdt": L.get("realized_usdt"), "unrealized_usdt": L.get("unrealized_usdt"),
                         "max_drawdown": L["max_drawdown"], "max_drawdown_usdt": L["max_drawdown_usdt"], "weights": {"BTC": w.get("BTCUSDT", 0.0), "ETH": w.get("ETHUSDT", 0.0), "USDT": (L["cash"] / eq) if eq else 1.0}, "holdings": L["holdings"], "cash_usdt": L["cash"], "fees_paid_usdt": L["fees_paid"], "risk_state": last.get("risk_state"), "prices": last.get("prices")})
    cost = sorted([r for r in rows if r["ledger"] == "cost"], key=lambda r: -r["liquidation_net_profit_usdt"]); ideal = sorted([r for r in rows if r["ledger"] == "ideal"], key=lambda r: -r["liquidation_net_profit_usdt"])
    for i, r in enumerate(cost, 1):
        r["rank"] = i
    rep = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "forward（真正前瞻；含成本 = 10 bps 手续费 + 5 bps 滑点；零成本仅对照）", "ranking_key": "cost ledger liquidation_net_profit_usdt desc", "cost": cost, "ideal": ideal, "real_trading": "disabled (no implementation)"}
    atomic_write_json(out_dir / "leaderboard.json", rep, indent=1, default=str)
    L = [f"# 三账户 USDT 利润排行榜（含成本，真正前瞻）· {rep['generated_at_utc'][:19]} UTC", "", "| 名次 | 账户 | 当前权益 | 清仓余额 | 清仓净利润 | 已实现 | 未实现 | 最大回撤 | BTC/ETH/USDT | 风险状态 | 数据时点 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in cost:
        w = r["weights"]; L.append(f"| {r['rank']} | {r['account']} | {r['equity_usdt']:,.2f} | {r['liquidation_usdt']:,.2f} | {r['liquidation_net_profit_usdt']:+,.2f} | {(r['realized_usdt'] or 0):+,.2f} | {(r['unrealized_usdt'] or 0):+,.2f} | {r['max_drawdown']*100:.2f}% | {w['BTC']*100:.0f}/{w['ETH']*100:.0f}/{w['USDT']*100:.0f} | {r['risk_state']} | {r['as_of_utc'][:16]} |")
    L += ["", "零成本对照（仅供比较，不是可实际获得的收益）：", "", "| 账户 | 当前权益 | 清仓净利润 | 最大回撤 |", "|---|---|---|---|"] + [f"| {r['account']} | {r['equity_usdt']:,.2f} | {r['liquidation_net_profit_usdt']:+,.2f} | {r['max_drawdown']*100:.2f}% |" for r in ideal]
    L += ["", "初始本金各 10,000 USDT；真实交易 API 禁用（无实现）。"]
    atomic_write_text(out_dir / "leaderboard.md", "\n".join(L)); return rep
