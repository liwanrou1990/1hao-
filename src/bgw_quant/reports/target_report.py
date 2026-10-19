"""固定收益目标日报：教学路径（事后）与无未来信息策略回测（样本外）严格分开。"""
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


def roundtrip_markers(trades: list, bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t in trades:
        d = t.to_dict() if hasattr(t, "to_dict") else t
        for f in d["entry"]["children"]:
            out.append({"time": int(secs[f["idx"]]), "position": "belowBar", "color": "#2962FF", "shape": "arrowUp",
                        "text": f"买{d['seq']} {f['exec_price']:.2f}×{f['notional_usd']:,.0f}"})
        for f in d["exit"]["children"]:
            out.append({"time": int(secs[f["idx"]]), "position": "aboveBar", "color": "#2e7d32" if d["net_pnl_usd"] >= 0 else "#D50000",
                        "shape": "arrowDown", "text": f"卖{d['seq']} {f['exec_price']:.2f} 净{d['net_pnl_usd']:+,.0f}"})
    return sorted(out, key=lambda m: m["time"])


def cum_series(curve: list[float], bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    return [{"time": int(s), "value": float(v)} for s, v in zip(secs, curve)]


def render_target_chart(bars: pd.DataFrame, entries: list[dict], symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    """entries: [{label, capital, teaching: TargetPathResult, strategy: StrategyDayResult|None}]"""
    payload = []
    for e in entries:
        t, s = e["teaching"], e.get("strategy")
        payload.append({"label": e["label"], "capital": e["capital"],
                        "teaching": {**t.to_dict(), "markers": roundtrip_markers(t.trades, bars), "cum": cum_series(t.cum_curve, bars)},
                        "strategy": None if s is None else {**s.to_dict(), "markers": roundtrip_markers(s.trades, bars), "cum": cum_series(s.cum_curve, bars)}})
    tpl = _env.get_template("target_chart.html.j2")
    html = tpl.render(symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
                      simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(bars_to_candles(bars)),
                      payload_json=json.dumps(payload, ensure_ascii=False, default=str), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_target_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None,
                           with_walkforward: bool = True, wf_symbols: list[str] | None = None) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or pipeline.symbols()
    caps = pipeline.target_capitals()
    per_symbol, csv_rows = [], []
    for sym in symbols:
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        entries = []
        for c in caps:
            try:
                t = pipeline.target_teaching(sym, day, c["capital_usd"])
            except (LookupError, ValueError):
                continue
            s = None
            try:
                s = pipeline.target_strategy_day(sym, day, c["capital_usd"])
            except Exception:  # noqa: BLE001
                s = None
            entries.append({"label": f"{c['label']} {c['capital_usd']:,.0f}", "capital": c["capital_usd"], "teaching": t, "strategy": s})
            for tr in t.trades:
                d = tr.to_dict()
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "mode": "教学(事后)", "capital_usd": c["capital_usd"], **_flat(d)})
            if s is not None:
                for tr in s.trades:
                    csv_rows.append({"symbol": sym, "day": day.isoformat(), "mode": "策略(无未来信息)", "capital_usd": c["capital_usd"], **_flat(tr.to_dict())})
        chart = charts_dir / f"target_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_target_chart(bars, entries, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        per_symbol.append({"symbol": sym, "entries": entries, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]),
                           "tradable": sym in pipeline.symbols(tradable_only=True), "fallback_native": bool(bars.attrs.get("fallback_native"))})
    wf_rows = []
    if with_walkforward:
        for sym in (wf_symbols or symbols[:1]):
            for cap in pipeline.target_cfg.validation_capitals:
                try:
                    wf = pipeline.target_walkforward(sym, cap)
                    st = wf.oos_stats
                    wf_rows.append({"symbol": sym, "capital_usd": cap, "target_pct": wf.target_return * 100, "folds": len(wf.folds),
                                    **st.to_dict(), "in_sample": wf.in_sample_stats_mean, "params_last": wf.folds[-1].params if wf.folds else {}})
                except Exception as e:  # noqa: BLE001
                    wf_rows.append({"symbol": sym, "capital_usd": cap, "error": str(e)})
    csv_path = out_dir / f"target_trades_{day}.csv"
    _write_csv(csv_rows, csv_path)
    summary = pipeline.target_summary(day, symbols)
    tpl = _env.get_template("target_report.html.j2")
    html = tpl.render(day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(),
                      per_symbol=per_symbol, summary=summary.to_dict(orient="records") if not summary.empty else [], wf_rows=wf_rows,
                      cfg=pipeline.target_cfg.__dict__, csv_name=csv_path.name, cost_profile=pipeline.cost_profile_name,
                      cost=pipeline.cost.describe(), formal=not pipeline.plan.is_simulated)
    html_path = out_dir / f"target_report_{day}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(csv_rows), "symbols": [e["symbol"] for e in per_symbol],
            "formal_validation": not pipeline.plan.is_simulated}


def _flat(d: dict) -> dict:
    return {"seq": d["seq"], "buy_ts": d["buy_ts"], "sell_ts": d["sell_ts"], "avg_entry": d["avg_entry"], "avg_exit": d["avg_exit"],
            "size_usd": d["size_usd"], "qty": d["qty"], "cost_usd": d["cost_usd"], "net_pnl_usd": d["net_pnl_usd"], "net_ret": d["net_ret"],
            "cum_net_pnl_usd": d["cum_net_pnl_usd"], "mae_pct": d["mae_pct"], "mfe_pct": d["mfe_pct"], "hold_bars": d["hold_bars"],
            "entry_children": d["entry"]["n_children"], "exit_children": d["exit"]["n_children"], "reason_entry": d["reason_entry"],
            "reason_exit": d["reason_exit"]}


def _write_csv(rows: list[dict], path: Path) -> None:
    cols = ["symbol", "day", "mode", "capital_usd", "seq", "buy_ts", "sell_ts", "avg_entry", "avg_exit", "size_usd", "qty", "cost_usd",
            "net_pnl_usd", "net_ret", "cum_net_pnl_usd", "mae_pct", "mfe_pct", "hold_bars", "entry_children", "exit_children",
            "reason_entry", "reason_exit"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
