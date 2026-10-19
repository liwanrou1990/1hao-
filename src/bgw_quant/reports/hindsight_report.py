"""事后最优日报：每档本金的最高可实现净收益、最少交易次数路径、逐笔明细、K线标记、目标达标对照。"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .charts import bars_to_candles, chart_time_seconds, ensure_vendor_js
from .daily import _num, _pct

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["pct"] = _pct
_env.filters["num"] = _num


def path_markers(trades: list, bars: pd.DataFrame, label: str = "") -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for i, t in enumerate(trades, 1):
        d = t.to_dict() if hasattr(t, "to_dict") else t
        out.append({"time": int(secs[d["buy_idx"]]), "position": "belowBar", "color": "#2962FF", "shape": "arrowUp",
                    "text": f"{label}买{i} {d['buy_exec']:.2f}"})
        out.append({"time": int(secs[d["sell_idx"]]), "position": "aboveBar", "color": "#D50000", "shape": "arrowDown",
                    "text": f"{label}卖{i} {d['sell_exec']:.2f}"})
    return sorted(out, key=lambda m: m["time"])


def render_hindsight_chart(bars: pd.DataFrame, tier_rows: list[dict], symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    candles = bars_to_candles(bars)
    tiers_json = []
    for row in tier_rows:
        r = row["result"]
        tiers_json.append({"key": f"{row['tier']}-{row['point']}", "tier": row["tier"], "point": row["point"], "capital": row["capital_usd"],
                           "target_pct": row["target_daily_pct"][0], "max_ret": r.max_net_return, "profit": r.max_net_profit_usd,
                           "n_trades": r.n_trades, "target_met": r.target_met, "min_k": r.min_trades_for_target,
                           "markers_max": path_markers(r.trades, bars), "markers_target": path_markers(r.target_path, bars, "目标"),
                           "trades": [t.to_dict() for t in r.trades], "target_path": [t.to_dict() for t in r.target_path],
                           "notes": r.notes, "unverifiable": r.unverifiable})
    tpl = _env.get_template("hindsight_chart.html.j2")
    html = tpl.render(symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
                      simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(candles),
                      tiers_json=json.dumps(tiers_json, ensure_ascii=False), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_hindsight_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None,
                              with_portfolio: bool = True) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or pipeline.symbols()
    per_symbol = []
    csv_rows = []
    for sym in symbols:
        try:
            bars = pipeline.hindsight_bars(sym, day)
            if bars.empty:
                continue
            rows = pipeline.hindsight_tiers(sym, day)
        except LookupError:
            continue
        chart = charts_dir / f"hindsight_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_hindsight_chart(bars, rows, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        for row in rows:
            r = row["result"]
            for kind, trades in (("最高收益路径", r.trades), ("最少交易达标路径", r.target_path)):
                for i, t in enumerate(trades, 1):
                    d = t.to_dict()
                    csv_rows.append({"symbol": sym, "day": day.isoformat(), "tier": row["tier"], "point": row["point"],
                                     "capital_usd": row["capital_usd"], "path_kind": kind, "seq": i, **d})
        per_symbol.append({"symbol": sym, "rows": rows, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]),
                           "venue": str(bars["venue"].iloc[0]), "fallback_native": bool(bars.attrs.get("fallback_native")),
                           "tradable": sym in pipeline.symbols(tradable_only=True)})
    portfolio = []
    if with_portfolio:
        for t in pipeline.tiers.tiers:
            cap = t.representative_capitals(("mid",))["mid"]
            try:
                pr = pipeline.hindsight_portfolio(day, cap, target_return=t.target_daily_pct[0] / 100)
                portfolio.append({"tier": t.id, "capital_usd": cap, "target_pct": t.target_daily_pct[0], "res": pr})
                for i, tr in enumerate(pr.sequential_trades, 1):
                    csv_rows.append({"symbol": tr.symbol, "day": day.isoformat(), "tier": t.id, "point": "mid", "capital_usd": cap,
                                     "path_kind": "组合轮动路径", "seq": i, **tr.to_dict()})
            except Exception as e:  # noqa: BLE001
                portfolio.append({"tier": t.id, "capital_usd": cap, "target_pct": t.target_daily_pct[0], "error": str(e)})
    csv_path = out_dir / f"hindsight_trades_{day}.csv"
    _write_csv(csv_rows, csv_path)
    summary = pipeline.hindsight_summary(day, symbols)
    summary_rows = []
    for r in (summary.to_dict(orient="records") if not summary.empty else []):
        mk = r.get("min_trades_for_target")
        r["min_trades_for_target"] = None if mk is None or (isinstance(mk, float) and mk != mk) else int(mk)
        summary_rows.append(r)
    tpl = _env.get_template("hindsight_report.html.j2")
    html = tpl.render(day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(),
                      per_symbol=per_symbol, portfolio=portfolio, summary=summary_rows,
                      tiers=pipeline.tiers.to_dict(), csv_name=csv_path.name, cost_profile=pipeline.cost_profile_name,
                      participation=pipeline.participation_rate, cost=pipeline.cost.describe())
    html_path = out_dir / f"hindsight_report_{day}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(csv_rows), "symbols": [e["symbol"] for e in per_symbol]}


def _write_csv(rows: list[dict], path: Path) -> None:
    cols = ["symbol", "day", "tier", "point", "capital_usd", "path_kind", "seq", "buy_ts", "buy_ref", "buy_exec", "sell_ts", "sell_ref",
            "sell_exec", "size_usd", "qty", "proceeds_usd", "cost_usd", "net_pnl_usd", "net_ret", "hold_bars", "liquidity_capped", "child_orders"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
