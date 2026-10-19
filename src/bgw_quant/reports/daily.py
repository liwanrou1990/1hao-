"""每日中文报告：HTML（含图表链接）、CSV 交易明细、可选 PDF（weasyprint 可用时）。"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))


def _pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:.{nd}f}%"


def _num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:,.{nd}f}"


_env.filters["pct"] = _pct
_env.filters["num"] = _num


def generate_daily_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None,
                          with_ml: bool = True, with_backtest: bool = True) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = symbols or pipeline.symbols()
    rankings = pipeline.rankings(day)
    per_symbol = []
    all_trades = []
    for sym in symbols:
        try:
            ab = pipeline.replay_day(sym, day)
        except LookupError:
            continue
        best = ab.executable[ab.best_strategy]
        for t in ab.hindsight.trades:
            all_trades.append({"symbol": sym, "day": day.isoformat(), "mode": "A_hindsight", "strategy": "DP", **_flat(t)})
        for name, r in ab.executable.items():
            for t in r.trades:
                all_trades.append({"symbol": sym, "day": day.isoformat(), "mode": "B_executable", "strategy": name, **_flat(t)})
        entry = {"symbol": sym, "ab": ab, "best": best, "chart": f"../charts/{sym}_{day}_{pipeline.cost_profile_name}.html",
                 "tradable": sym in pipeline.symbols(tradable_only=True)}
        if with_backtest:
            try:
                bt = pipeline.backtest(sym)
                entry["bt"] = bt
                entry["attain"] = pipeline.tier_attainment(sym).to_dict(orient="records")
            except Exception as e:  # noqa: BLE001
                entry["bt_error"] = str(e)
        if with_ml:
            try:
                wf = pipeline.train_ml(sym)
                entry["ml"] = wf.summary().to_dict(orient="records")
                sig = pipeline.ai_signals(sym, day)
                entry["ai_n_signals"] = int(sig["decision"].sum()) if not sig.empty else 0
                entry["ai_reasons"] = sig["reason"].value_counts().head(4).to_dict() if not sig.empty else {}
            except Exception as e:  # noqa: BLE001
                entry["ml_error"] = str(e)
        per_symbol.append(entry)
    cap = pipeline.capacity(symbols[0] if symbols else None)
    csv_path = out_dir / f"trades_{day}.csv"
    _write_csv(all_trades, csv_path)
    tpl = _env.get_template("daily_report.html.j2")
    html = tpl.render(day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(),
                      rankings=rankings.to_dict(orient="records") if not rankings.empty else [], per_symbol=per_symbol,
                      capacity=cap.to_dict(orient="records"), tiers=pipeline.tiers.to_dict(), csv_name=csv_path.name,
                      cost_profile=pipeline.cost_profile_name, capital=pipeline.capital_usd)
    html_path = out_dir / f"daily_report_{day}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = _try_pdf(html, out_dir / f"daily_report_{day}.pdf")
    return {"html": str(html_path), "csv": str(csv_path), "pdf": pdf_path, "n_trades": len(all_trades), "symbols": [e["symbol"] for e in per_symbol]}


def _flat(t: dict) -> dict:
    keys = ["signal_ts", "buy_ts", "buy_ref", "buy_exec", "sell_ts", "sell_ref", "sell_exec", "qty", "cost_usd", "gross_pnl_usd",
            "net_pnl_usd", "net_ret", "hold_bars", "reason", "fill_ratio", "unverifiable", "note"]
    return {k: t.get(k) for k in keys}


def _write_csv(rows: list[dict], path: Path) -> None:
    cols = ["symbol", "day", "mode", "strategy", "signal_ts", "buy_ts", "buy_ref", "buy_exec", "sell_ts", "sell_ref", "sell_exec", "qty",
            "cost_usd", "gross_pnl_usd", "net_pnl_usd", "net_ret", "hold_bars", "reason", "fill_ratio", "unverifiable", "note"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _try_pdf(html: str, path: Path) -> str | None:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        HTML(string=html).write_pdf(str(path))
        return str(path)
    except Exception:  # noqa: BLE001
        return None
