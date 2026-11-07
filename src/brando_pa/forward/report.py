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
