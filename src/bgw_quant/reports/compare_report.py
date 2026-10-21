"""各档位对比报告：理论最优(A) vs 可执行模拟(B)，失败原因分解，大资金档位无未来信息滚动回测(C)，K线买卖点图。"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .charts import bars_to_candles, ensure_vendor_js
from .daily import _num, _pct
from .hindsight_report import path_markers
from .simple_report import cum_series, sim_markers

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["pct"] = _pct
_env.filters["num"] = _num


def render_compare_chart(bars: pd.DataFrame, comps: list[dict], symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    payload = []
    for c in comps:
        ex, th = c["executable"], c["theoretical"]
        payload.append({"tier": c["tier"], "capital": c["capital_usd"], "target": c["target_usd"],
                        "exec": {**ex.to_dict(), "markers": sim_markers(ex.trades, bars), "cum": cum_series(ex.cum_curve, bars)},
                        "theo": {"profit": th.max_net_profit_usd, "n_trades": th.n_trades, "markers": path_markers(th.trades, bars, "A"),
                                 "trades": [t.to_dict() for t in th.trades]},
                        "analysis": c["analysis"], "tried": c["tried"]})
    html = _env.get_template("compare_chart.html.j2").render(
        symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
        simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(bars_to_candles(bars)),
        payload_json=json.dumps(payload, ensure_ascii=False, default=str), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_compare_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None,
                            wf_symbols: list[str] | None = None, wf_min_capital: float = 175_000) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or pipeline.symbols()
    per_symbol, csv_rows = [], []
    for sym in symbols:
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        comps = pipeline.compare_tiers(sym, day)
        chart = charts_dir / f"compare_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_compare_chart(bars, comps, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        per_symbol.append({"symbol": sym, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]), "comps": comps,
                           "tradable": sym in pipeline.symbols(tradable_only=True), "fallback_native": bool(bars.attrs.get("fallback_native"))})
        for c in comps:
            for t in c["executable"].trades:
                d = t.to_dict()
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "mode": "B可执行", "tier": c["tier"], "capital_usd": c["capital_usd"], "seq": d["seq"],
                                 "direction": d["direction"], "open_ts": d["open_ts"], "avg_open": d["avg_open"], "close_ts": d["close_ts"],
                                 "avg_close": d["avg_close"], "size_usd": d["size_usd"], "qty": d["qty"], "trading_cost_usd": d["trading_cost_usd"],
                                 "capital_cost_usd": d["capital_cost_usd"], "net_pnl_usd": d["net_pnl_usd"], "net_ret": d["net_ret"],
                                 "cum_net_pnl_usd": d["cum_net_pnl_usd"], "mae_pct": d["mae_pct"], "hold_bars": d["hold_bars"]})
            for t in c["theoretical"].trades:
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "mode": "A理论", "tier": c["tier"], "capital_usd": c["capital_usd"], "seq": "",
                                 "direction": "long", "open_ts": t.buy_ts, "avg_open": t.buy_exec, "close_ts": t.sell_ts, "avg_close": t.sell_exec,
                                 "size_usd": t.size_usd, "qty": t.qty, "trading_cost_usd": t.cost_usd, "capital_cost_usd": 0.0,
                                 "net_pnl_usd": t.net_pnl_usd, "net_ret": t.net_ret, "cum_net_pnl_usd": "", "mae_pct": "", "hold_bars": t.hold_bars})
    wf_rows = []
    for sym in (wf_symbols or []):
        for t in pipeline.tiers.tiers:
            cap = t.representative_capitals(("mid",))["mid"]
            if cap < wf_min_capital:
                continue
            try:
                wf = pipeline.target_walkforward(sym, cap)
                st = wf.oos_stats
                wf_rows.append({"symbol": sym, "tier": t.id, "capital_usd": cap, "target_pct": wf.target_return * 100, "folds": len(wf.folds),
                                **st.to_dict(), "in_sample": wf.in_sample_stats_mean, "params_last": wf.folds[-1].params if wf.folds else {}})
            except Exception as e:  # noqa: BLE001
                wf_rows.append({"symbol": sym, "tier": t.id, "capital_usd": cap, "error": str(e)})
    csv_path = out_dir / f"compare_trades_{day}_{pipeline.cost_profile_name}.csv"
    cols = ["symbol", "day", "mode", "tier", "capital_usd", "seq", "direction", "open_ts", "avg_open", "close_ts", "avg_close", "size_usd", "qty",
            "trading_cost_usd", "capital_cost_usd", "net_pnl_usd", "net_ret", "cum_net_pnl_usd", "mae_pct", "hold_bars"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(csv_rows)
    summary = pipeline.compare_summary(day, symbols)
    summary_rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in summary.to_dict(orient="records")] if not summary.empty else []
    n_met = sum(1 for r in summary_rows if r["target_met"])
    html = _env.get_template("compare_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(), rows=summary_rows, n_met=n_met,
        per_symbol=per_symbol, wf_rows=wf_rows, cfg=pipeline.simple_cfg.__dict__, cost=pipeline.cost.describe(),
        cost_profile=pipeline.cost_profile_name, csv_name=csv_path.name, formal=not pipeline.plan.is_simulated)
    html_path = out_dir / f"compare_report_{day}_{pipeline.cost_profile_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(summary_rows), "n_met": n_met, "symbols": [e["symbol"] for e in per_symbol],
            "formal_validation": not pipeline.plan.is_simulated}
