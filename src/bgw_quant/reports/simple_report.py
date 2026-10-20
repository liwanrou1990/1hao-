"""简单报告：本金、目标利润、实际利润、买卖/多空次数、资金利用率、是否达标 + 真实K线上标记全部买卖点。"""
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


def sim_markers(trades: list, bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t in trades:
        d = t.to_dict() if hasattr(t, "to_dict") else t
        long = d["direction"] == "long"
        for f in d["open_fill"]["children"]:
            out.append({"time": int(secs[f["idx"]]), "position": "belowBar" if long else "aboveBar", "color": "#2962FF" if long else "#8E24AA",
                        "shape": "arrowUp" if long else "arrowDown", "text": f"{'买' if long else '空'}{d['seq']} {f['exec_price']:.2f}"})
        for f in d["close_fill"]["children"]:
            out.append({"time": int(secs[f["idx"]]), "position": "aboveBar" if long else "belowBar",
                        "color": "#2e7d32" if d["net_pnl_usd"] >= 0 else "#D50000", "shape": "arrowDown" if long else "arrowUp",
                        "text": f"{'卖' if long else '平'}{d['seq']} {f['exec_price']:.2f} 净{d['net_pnl_usd']:+,.0f}"})
    return sorted(out, key=lambda m: m["time"])


def cum_series(curve, bars):
    secs = chart_time_seconds(bars)
    return [{"time": int(s), "value": float(v)} for s, v in zip(secs, curve)]


def render_simple_chart(bars: pd.DataFrame, results: list, symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    payload = [{**r.to_dict(), "markers": sim_markers(r.trades, bars), "cum": cum_series(r.cum_curve, bars)} for r in results]
    html = _env.get_template("simple_chart.html.j2").render(
        symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
        simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(bars_to_candles(bars)),
        payload_json=json.dumps(payload, ensure_ascii=False, default=str), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_simple_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or pipeline.symbols()
    rows, per_symbol, csv_rows = [], [], []
    for sym in symbols:
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        results = pipeline.simple_tiers(sym, day)
        chart = charts_dir / f"simple_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_simple_chart(bars, results, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        per_symbol.append({"symbol": sym, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]),
                           "fallback_native": bool(bars.attrs.get("fallback_native")), "tradable": sym in pipeline.symbols(tradable_only=True),
                           "results": results})
        for r in results:
            rows.append(r)
            for t in r.trades:
                d = t.to_dict()
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "tier": r.tier, "capital_usd": r.capital_usd, "seq": d["seq"],
                                 "direction": d["direction"], "open_ts": d["open_ts"], "avg_open": d["avg_open"], "close_ts": d["close_ts"],
                                 "avg_close": d["avg_close"], "size_usd": d["size_usd"], "qty": d["qty"], "trading_cost_usd": d["trading_cost_usd"],
                                 "capital_cost_usd": d["capital_cost_usd"], "net_pnl_usd": d["net_pnl_usd"], "net_ret": d["net_ret"],
                                 "mae_pct": d["mae_pct"], "hold_bars": d["hold_bars"], "cum_net_pnl_usd": d["cum_net_pnl_usd"]})
    csv_path = out_dir / f"simple_trades_{day}_{pipeline.cost_profile_name}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["symbol", "day", "tier", "capital_usd", "seq", "direction", "open_ts", "avg_open", "close_ts", "avg_close", "size_usd", "qty",
                "trading_cost_usd", "capital_cost_usd", "net_pnl_usd", "net_ret", "mae_pct", "hold_bars", "cum_net_pnl_usd"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(csv_rows)
    html = _env.get_template("simple_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(), rows=rows, per_symbol=per_symbol,
        cfg=pipeline.simple_cfg.__dict__, cost=pipeline.cost.describe(), cost_profile=pipeline.cost_profile_name, csv_name=csv_path.name,
        formal=not pipeline.plan.is_simulated)
    html_path = out_dir / f"simple_report_{day}_{pipeline.cost_profile_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_trades": len(csv_rows), "symbols": [e["symbol"] for e in per_symbol],
            "formal_validation": not pipeline.plan.is_simulated}
