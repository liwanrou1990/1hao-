"""导出：把最近的信号、回测矩阵、Walk-Forward、模拟盘状态汇总为 CSV/JSON/HTML 研究报告（reports_out/brando_pa/export/）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from .config import cfg, report_dir, resolve


def _read_json(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def collect(c: dict | None = None) -> dict:
    c = c or cfg(); rd = report_dir(c)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "signals": _read_json(rd / "signals" / "latest.json"), "backtests": {}, "walkforward": {}, "paper": None}
    for d in sorted((rd / "backtest").glob("*")) if (rd / "backtest").exists() else []:
        cj = _read_json(d / "compare.json")
        if cj:
            out["backtests"][d.name] = cj
    for d in sorted((rd / "walkforward").glob("*")) if (rd / "walkforward").exists() else []:
        wj = _read_json(d / "walkforward.json")
        if wj:
            out["walkforward"][d.name] = wj
    sp = resolve(c["paper"]["state_file"])
    if sp.exists():
        st = _read_json(sp); out["paper"] = {k: st.get(k) for k in ("initial_equity", "cash", "equity", "unrealized", "n_trades", "realized_pnl", "positions", "pending", "risk", "last_run", "mode")}
        tl = resolve(c["paper"]["log_file"])
        if tl.exists():
            rows = [json.loads(x) for x in tl.read_text().splitlines() if x.strip()]
            out["paper"]["trades"] = rows[-500:]
    return out


def export_all(out_dir: str | None = None, c: dict | None = None) -> dict:
    c = c or cfg(); od = Path(out_dir) if out_dir else report_dir(c) / "export"; od.mkdir(parents=True, exist_ok=True)
    data = collect(c)
    atomic_write_json(od / "research_export.json", data, indent=2, default=str)
    rows = []
    for name, cj in data["backtests"].items():
        for v, r in cj["variants"].items():
            rows.append({"matrix": name, "exec_tf": cj["exec_tf"], "variant": v, **{k: r.get(k) for k in ("n_trades", "win_rate", "profit_factor", "expectancy_r", "total_net_return", "max_drawdown", "sharpe", "max_consecutive_losses", "total_costs", "candidates", "accepted")}})
    pd.DataFrame(rows).to_csv(od / "backtest_matrix.csv", index=False)
    if data["paper"] and data["paper"].get("trades"):
        pd.DataFrame(data["paper"]["trades"]).to_csv(od / "paper_trades.csv", index=False)
    html = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'><title>brando_pa 研究导出</title><style>body{font-family:sans-serif;margin:20px} table{border-collapse:collapse} td,th{border:1px solid #ddd;padding:4px 8px;font-size:13px}</style></head><body>",
            f"<h1>Brando Price Action 引擎 · 研究导出</h1><p>{data['generated_at']}</p><h2>回测矩阵</h2>", pd.DataFrame(rows).to_html(index=False) if rows else "<p>无</p>"]
    if data["signals"]:
        html.append("<h2>最新信号</h2><ul>" + "".join(f"<li><b>{s}</b> {d['ts']} {d['status']} @ {d['price']}：{'；'.join(d['reasons'][:3])}</li>" for s, d in data["signals"]["symbols"].items()) + "</ul>")
    if data["paper"]:
        p = data["paper"]; html.append(f"<h2>模拟盘</h2><p>净值 {p.get('equity')} · 现金 {p.get('cash')} · 已实现 {p.get('realized_pnl')} · 交易 {p.get('n_trades')} · 持仓 {len(p.get('positions') or [])}</p>")
    for name, wj in data["walkforward"].items():
        html.append(f"<h2>Walk-Forward {name}</h2>" + "".join(f"<p><b>{k}</b> OOS {json.dumps(v['oos_stats'], ensure_ascii=False, default=str)[:600]}</p>" for k, v in wj["results"].items()))
    html.append("</body></html>")
    atomic_write_text(od / "research_export.html", "\n".join(html))
    return {"json": str(od / "research_export.json"), "csv": str(od / "backtest_matrix.csv"), "html": str(od / "research_export.html")}
