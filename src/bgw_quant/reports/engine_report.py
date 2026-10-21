"""统一收益决策引擎报告：每档位 目标/实际净收益/收益率/交易次数/多空次数/胜率/最大回撤/资金利用率/执行成本/达标/原因 + 样本外 + 能力 + K线买卖点。"""
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


def render_engine_chart(bars: pd.DataFrame, comps: list[dict], symbol: str, day: str, out_path: Path, cost_profile: str) -> str:
    payload = []
    for c in comps:
        r, th, opt = c["run"], c["theoretical"], c["optimization"]
        payload.append({"tier": c["tier"], "capital": c["capital_usd"], "target": c["target_usd"],
                        "run": {**r.to_dict(), "markers": sim_markers(r.trades, bars), "cum": cum_series(r.cum_curve, bars)},
                        "theo": {"profit": th.max_net_profit_usd, "n_trades": th.n_trades, "markers": path_markers(th.trades, bars, "A")},
                        "classification": c["classification"],
                        "oos": {**opt.oos_stats.to_dict(), "days": opt.oos_frame().to_dict(orient="records"),
                                "folds": [{"fold": f.fold, "train": [str(f.train[0]), str(f.train[1])], "test": [str(f.test[0]), str(f.test[1])],
                                           "params": f.params.to_dict(), "train_attainment": f.train_stats.attainment_rate} for f in opt.folds]} if opt else None})
    html = _env.get_template("engine_chart.html.j2").render(
        symbol=symbol, day=day, source=str(bars["source"].iloc[0]), venue=str(bars["venue"].iloc[0]),
        simulated=bool((bars["quality"].astype(int) & 128).any()), candles_json=json.dumps(bars_to_candles(bars)),
        payload_json=json.dumps(payload, ensure_ascii=False, default=str), cost_profile=cost_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")
    return html


def generate_engine_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    symbols = symbols or ["NVDA", "SPCX"]
    per_symbol, csv_rows = [], []
    for sym in symbols:
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        comps = pipeline.engine_tiers(sym, day)
        chart = charts_dir / f"engine_{sym}_{day}_{pipeline.cost_profile_name}.html"
        render_engine_chart(bars, comps, sym, day.isoformat(), chart, pipeline.cost_profile_name)
        per_symbol.append({"symbol": sym, "chart": f"../charts/{chart.name}", "source": str(bars["source"].iloc[0]), "comps": comps,
                           "capability": pipeline.capability(sym), "tradable": sym in pipeline.symbols(tradable_only=True)})
        for c in comps:
            for t in c["run"].trades:
                d = t.to_dict()
                csv_rows.append({"symbol": sym, "day": day.isoformat(), "tier": c["tier"], "capital_usd": c["capital_usd"], "seq": d["seq"], "direction": d["direction"],
                                 "mode": d["mode"], "open_ts": d["open_ts"], "avg_open": d["avg_open"], "close_ts": d["close_ts"], "avg_close": d["avg_close"],
                                 "size_usd": d["size_usd"], "qty": d["qty"], "trading_cost_usd": d["trading_cost_usd"], "funding_cost_usd": d["funding_cost_usd"],
                                 "net_pnl_usd": d["net_pnl_usd"], "net_ret": d["net_ret"], "hold_bars": d["hold_bars"], "reason_exit": d["reason_exit"],
                                 "cum_net_pnl_usd": d["cum_net_pnl_usd"]})
    csv_path = out_dir / f"engine_trades_{day}_{pipeline.cost_profile_name}.csv"
    cols = ["symbol", "day", "tier", "capital_usd", "seq", "direction", "mode", "open_ts", "avg_open", "close_ts", "avg_close", "size_usd", "qty",
            "trading_cost_usd", "funding_cost_usd", "net_pnl_usd", "net_ret", "hold_bars", "reason_exit", "cum_net_pnl_usd"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(csv_rows)
    summary = pipeline.engine_summary(day, symbols)
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in summary.to_dict(orient="records")] if not summary.empty else []
    tag_counts: dict[str, int] = {}
    for r in rows:
        if not r["target_met"]:
            for t in (r["tags"] or "").split("、"):
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
    html = _env.get_template("engine_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(), rows=rows,
        n_met=sum(1 for r in rows if r["target_met"]), tag_counts=tag_counts, per_symbol=per_symbol, cfg=pipeline.engine_cfg,
        cost=pipeline.cost.describe(), cost_profile=pipeline.cost_profile_name, csv_name=csv_path.name, formal=not pipeline.plan.is_simulated)
    html_path = out_dir / f"engine_report_{day}_{pipeline.cost_profile_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(rows), "n_met": sum(1 for r in rows if r["target_met"]),
            "tag_counts": tag_counts, "symbols": [e["symbol"] for e in per_symbol], "formal_validation": not pipeline.plan.is_simulated}
