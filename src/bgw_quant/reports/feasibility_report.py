"""历史可行性报告：每档位 A 理论最大 / B 执行约束下最大可实现 / 达标所需笔数 / 完整买卖点 / 限制说明。"""
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


def feas_markers(trades: list, bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for i, t in enumerate(trades, 1):
        d = t.to_dict() if hasattr(t, "to_dict") else t
        long = d["direction"] == "long"
        out.append({"time": int(secs[d["buy_idx"]]), "position": "belowBar" if long else "aboveBar", "color": "#2962FF" if long else "#8E24AA",
                    "shape": "arrowUp" if long else "arrowDown", "text": f"{'买' if long else '空'}{i} {d['open_exec']:.2f}×{d['size_usd']:,.0f}"})
        out.append({"time": int(secs[d["sell_idx"]]), "position": "aboveBar" if long else "belowBar", "color": "#2e7d32" if d["net_pnl_usd"] >= 0 else "#D50000",
                    "shape": "arrowDown" if long else "arrowUp", "text": f"{'卖' if long else '平'}{i} {d['close_exec']:.2f} 净{d['net_pnl_usd']:+,.0f}"})
    return sorted(out, key=lambda m: m["time"])


def cum_series(trades: list, bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    cum = [0.0] * len(bars)
    running = 0.0
    j = 0
    sorted_tr = sorted(trades, key=lambda t: t.sell_idx)
    for i in range(len(bars)):
        while j < len(sorted_tr) and sorted_tr[j].sell_idx == i:
            running += sorted_tr[j].net_pnl_usd
            j += 1
        cum[i] = running
    return [{"time": int(s), "value": float(v)} for s, v in zip(secs, cum)]


def render_feasibility_chart(bars: pd.DataFrame, results: list, symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    payload = [{**r.to_dict(), "markers": feas_markers(r.trades, bars), "cum": cum_series(r.trades, bars)} for r in results]
    html = _env.get_template("feasibility_chart.html.j2").render(
        symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
        simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(bars_to_candles(bars)),
        payload_json=json.dumps(payload, ensure_ascii=False, default=str), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_feasibility_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or pipeline.symbols()
    rows, per_symbol, csv_rows = [], [], []
    for sym in symbols:
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        results = pipeline.feasibility_tiers(sym, day)
        chart = charts_dir / f"feasibility_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_feasibility_chart(bars, results, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        per_symbol.append({"symbol": sym, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]), "results": results,
                           "capability": pipeline.capability(sym)})
        for r in results:
            rows.append(r)
            for t in r.trades:
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "tier": r.tier, "capital_usd": r.capital_usd, **t.to_dict()})
    csv_path = out_dir / f"feasibility_trades_{day}_{pipeline.cost_profile_name}.csv"
    cols = ["symbol", "day", "tier", "capital_usd", "tranche", "direction", "buy_idx", "sell_idx", "open_ts", "close_ts", "open_ref", "open_exec",
            "close_ref", "close_exec", "size_usd", "qty", "cost_usd", "net_pnl_usd", "net_ret", "hold_bars", "cum_net_pnl_usd"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(csv_rows)
    html = _env.get_template("feasibility_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(), rows=rows, per_symbol=per_symbol,
        n_a=sum(1 for r in rows if r.theoretical_met), n_b=sum(1 for r in rows if r.executable_met), cost=pipeline.cost.describe(),
        cost_profile=pipeline.cost_profile_name, csv_name=csv_path.name, formal=not pipeline.plan.is_simulated,
        participation=float(pipeline.engine_cfg.execution.get("participation_rate", 0.1)))
    html_path = out_dir / f"feasibility_report_{day}_{pipeline.cost_profile_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(rows), "n_theoretical_met": sum(1 for r in rows if r.theoretical_met),
            "n_executable_met": sum(1 for r in rows if r.executable_met), "symbols": [e["symbol"] for e in per_symbol],
            "formal_validation": not pipeline.plan.is_simulated}
