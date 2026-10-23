"""每日历史盈利路径模板报告：七档 × （跨资产模板 + 各单资产模板），完整交易流水 CSV、K 线买卖点图 + 收益曲线、HTML、JSON。"""
from __future__ import annotations

import json
import multiprocessing as mp
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..costs.model import profile_for_asset
from ..playbook.optimizer import PlaybookResult, optimize_playbook
from ..playbook.universe_time import grid_ny_strings
from .charts import bars_to_candles, chart_time_seconds, ensure_vendor_js
from .daily import _num, _pct

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["pct"] = _pct
_env.filters["num"] = _num

LEDGER_COLUMNS = ["day", "tier", "capital_usd", "target_usd", "mode", "no", "symbol", "asset_class", "direction", "entry_ts", "entry_price", "exit_ts",
                  "exit_price", "size_usd", "qty", "cost_usd", "funding_usd", "gas_usd", "mae_pct", "net_pnl_usd", "net_ret", "hold_min", "cum_net_pnl_usd",
                  "reaches_target", "n_trades_total", "target_met"]

_ASSETS: dict = {}
_CFG: dict = {}


def _verify(pipeline, r: dict, assets: dict, day: date) -> dict:
    from ..playbook.verify import verify_trades
    trades = {s: pipeline.store.trades_for_day(s, day) for s in assets}
    depth = {s: pipeline.store.depth_for_day(s, day) for s in assets}
    return verify_trades(r["trades"], assets, pipeline.participation_rate, trades, depth).to_dict()


def _worker(task: tuple) -> tuple[tuple, dict]:
    tier, cap, tgt, mode = task
    assets = list(_ASSETS.values()) if mode == "cross" else [_ASSETS[mode.split(":", 1)[1]]]
    r = optimize_playbook(assets, cap, tgt, _CFG["day"], mode=mode, tier=tier, participation=_CFG["participation"], tx_amount=_CFG.get("tx_amount"),
                          weights=_CFG.get("weights"), max_hold_min=_CFG.get("max_hold_min", 480), k_grid=tuple(_CFG.get("k_grid", (1, 2, 4, 8, 16, 32, 64, 128, 256))))
    d = r.to_dict()
    d["equity_curve"] = r.equity_curve
    return task, d


def ledger_rows(day: str, tier: str, mode: str, r: dict) -> list[dict]:
    out = []
    for t in r["trades"]:
        out.append({"day": day, "tier": tier, "capital_usd": r["capital_usd"], "target_usd": r["target_usd"], "mode": mode, **{k: t[k] for k in
                    ("no", "symbol", "asset_class", "direction", "entry_ts", "entry_price", "exit_ts", "exit_price", "size_usd", "qty", "cost_usd",
                     "funding_usd", "gas_usd", "mae_pct", "net_pnl_usd", "net_ret", "hold_min", "cum_net_pnl_usd", "reaches_target")},
                    "n_trades_total": r["n_trades"], "target_met": r["target_met"]})
    return out


def _markers(trades: list[dict], bars: pd.DataFrame, a, symbol: str) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t in trades:
        if t["symbol"] != symbol:
            continue
        bi, si = int(a.bar_at_grid[t["entry_grid"]]), int(a.bar_at_grid[t["exit_grid"]])
        if bi < 0 or si < 0:
            continue
        long = t["direction"] == "long"
        out.append({"time": int(secs[bi]), "position": "belowBar" if long else "aboveBar", "color": "#2962FF" if long else "#8E24AA",
                    "shape": "arrowUp" if long else "arrowDown", "text": f"{'买' if long else '空'}{t['no']} {t['entry_price']:.2f}×{t['size_usd']:,.0f}"})
        out.append({"time": int(secs[si]), "position": "aboveBar" if long else "belowBar", "color": "#2e7d32" if t["net_pnl_usd"] >= 0 else "#D50000",
                    "shape": "arrowDown" if long else "arrowUp", "text": f"{'卖' if long else '平'}{t['no']} {t['exit_price']:.2f} 净{t['net_pnl_usd']:+,.0f}"})
    return sorted(out, key=lambda m: m["time"])


def render_playbook_chart(pipeline, r: dict, assets: dict, out_path: Path, mode_name: str) -> None:
    day = r["day"]
    used = set(r["assets_used"]) or set(list(assets)[:1])
    payload = {}
    for sym in r["universe"]:
        if sym not in used or sym not in assets:
            continue
        a = assets[sym]
        payload[sym] = {"candles": bars_to_candles(a.bars), "markers": _markers(r["trades"], a.bars, a, sym),
                        "n_trades": sum(t["symbol"] == sym for t in r["trades"]), "source": a.source}
    grid = pd.to_datetime(grid_ny_strings(day))
    secs = ((grid - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()
    eq = [{"time": int(s), "value": float(v)} for s, v in zip(secs, r["equity_curve"])]
    kpis = [("达标", ("是 @ " + r["time_to_target_ts"][11:16]) if r["target_met"] else "否"), ("净收益", f"{r['net_usd']:,.0f} USD ({r['net_return']*100:.3f}%)"),
            ("最高可实现（λ=0）", f"{r['max_achievable_net_usd']:,.0f}"), ("缺口", f"{r['gap_usd']:,.0f}"), ("笔数 多/空", f"{r['n_trades']} ({r['n_long']}/{r['n_short']})"),
            ("资产切换", r["n_switches"]), ("资金片 / λ", f"{r['n_tranches']} / {r['lam']}"), ("最大回撤（盯市）", f"{r['max_drawdown_usd']:,.0f}"),
            ("Σ最大不利波动", f"{r['sum_mae_usd']:,.0f}"), ("成本 / 资金成本", f"{r['cost_usd']:,.0f} / {r['funding_usd']:,.0f}"),
            ("峰值占用", f"{r['peak_deployed_usd']/r['capital_usd']*100:.0f}%"), ("流动性占用", "未知" if r["liquidity_used_ratio"] is None else f"{r['liquidity_used_ratio']*100:.1f}%")]
    html = _env.get_template("playbook_chart.html.j2").render(day=day, tier=r.get("tier") or "", capital=r["capital_usd"], target=r["target_usd"], mode_name=mode_name,
                                                             simulated=r["data_is_simulated"], kpis=kpis, trades=r["trades"], limits=r["limits"],
                                                             assets_json=json.dumps(payload), eq_json=json.dumps(eq))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")


def generate_playbook_report(pipeline, day: date, symbols: list[str] | None = None, workers: int = 4, out_dir: Path | None = None,
                             single_assets: bool = True) -> dict:
    """生成某日（默认昨天）七档模板：跨资产 + 单资产；流水 CSV、图表、HTML、JSON。"""
    global _ASSETS, _CFG
    syms = symbols or (pipeline.symbols(tradable_only=True) + pipeline.crypto_symbols(tradable_only=True))
    assets = {}
    for s in syms:
        a = pipeline.asset_day(s, day)
        if a is not None and a.cap.directions:
            assets[s] = a
    if not assets:
        raise LookupError(f"{day} 无任何可交易资产数据")
    tx = {s: pipeline.tx_amount_per_bar(s, day) for s in assets}
    _ASSETS = assets
    _CFG = {"day": day.isoformat(), "participation": pipeline.participation_rate, "tx_amount": {k: v for k, v in tx.items() if v is not None},
            "weights": (pipeline.playbook_cfg or {}).get("weights"), "max_hold_min": (pipeline.playbook_cfg or {}).get("max_hold_min", 480),
            "k_grid": (pipeline.playbook_cfg or {}).get("k_grid", [1, 2, 4, 8, 16, 32, 64, 128, 256])}
    tiers = []
    for t in pipeline.tiers.tiers:
        cap = t.representative_capitals(("mid",))["mid"]
        lo, hi = t.target_daily_pct
        pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(pipeline.simple_cfg.target_mode, lo)
        tiers.append((t.id, float(cap), float(pct / 100 * cap)))
    tasks = [(tier, cap, tgt, "cross") for tier, cap, tgt in tiers]
    if single_assets:
        tasks += [(tier, cap, tgt, f"single:{s}") for tier, cap, tgt in tiers for s in assets]
    results: dict[tuple, dict] = {}
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        for task, d in pool.imap_unordered(_worker, tasks):
            results[(task[0], task[3])] = d
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / "playbook" / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    prof = pipeline.cost_profile_name
    rows, ledger, single_matrix, checks_rows = [], [], {}, []
    for tier, cap, tgt in tiers:
        r = results[(tier, "cross")]
        v = _verify(pipeline, r, assets, day)
        r["verification"] = {k: v[k] for k in v if k != "checks"}
        checks_rows += [{"tier": tier, "mode": "cross", **c} for c in v["checks"]]
        fn = f"playbook_{tier}_{day}_{prof}.html"
        render_playbook_chart(pipeline, r, assets, charts_dir / fn, "跨资产模板")
        rows.append({**r, "mode_name": "跨资产（可切换）", "chart": f"../../charts/{fn}"})
        ledger += ledger_rows(day.isoformat(), tier, "cross", r)
        cells = {}
        best_single = None
        for s in assets:
            rs = results.get((tier, f"single:{s}"))
            if not rs:
                continue
            cells[s] = {"target_met": rs["target_met"], "net_usd": rs["net_usd"], "n_trades": rs["n_trades"], "note": "；".join(rs["limits"][:1])}
            ledger += ledger_rows(day.isoformat(), tier, f"single:{s}", rs)
            key = (rs["target_met"], -rs["risk_score_usd"] if rs["target_met"] else rs["net_usd"])
            if best_single is None or key > best_single[0]:
                best_single = (key, s, rs)
        single_matrix[tier] = cells
        if best_single:
            _, s, rs = best_single
            v = _verify(pipeline, rs, {s: assets[s]}, day)
            rs["verification"] = {k: v[k] for k in v if k != "checks"}
            checks_rows += [{"tier": tier, "mode": f"single:{s}", **c} for c in v["checks"]]
            fn = f"playbook_{tier}_{s}_{day}_{prof}.html"
            render_playbook_chart(pipeline, rs, assets, charts_dir / fn, f"单资产模板 {s}")
            rows.append({**rs, "mode_name": f"单资产最佳 {s}", "chart": f"../../charts/{fn}"})
    csv_name = f"playbook_ledger_{day}_{prof}.csv"
    pd.DataFrame(ledger, columns=LEDGER_COLUMNS).to_csv(out_dir / csv_name, index=False)
    checks_name = f"playbook_checks_{day}_{prof}.csv"
    pd.DataFrame(checks_rows).to_csv(out_dir / checks_name, index=False)
    statuses = sorted({r["verification"]["status"] for r in rows})
    overall = "SIMULATED" if "SIMULATED" in statuses else ("EXECUTION_ESTIMATED" if "EXECUTION_ESTIMATED" in statuses else "REAL_HISTORICAL")
    prov = pipeline.data_provenance(day, list(assets))
    json_name = f"playbook_{day}_{prof}.json"
    summary = {"day": day.isoformat(), "cost_profile": prof, "formal_validation": overall == "REAL_HISTORICAL", "validation_status": overall, "provenance": prov,
               "tiers": [{k: v for k, v in r.items() if k not in ("trades", "equity_curve", "candidates")} for r in rows]}
    (out_dir / json_name).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html = _env.get_template("playbook_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), cost_profile=prof,
        crypto_profile=profile_for_asset(prof, "crypto"), participation=f"{pipeline.participation_rate:.0%}", max_hold=_CFG["max_hold_min"],
        stocks=[s for s in assets if assets[s].asset_class == "stock"], cryptos=[s for s in assets if assets[s].asset_class == "crypto"],
        formal=summary["formal_validation"], banner=pipeline.plan.banner(), rows=rows, all_symbols=list(assets), single_matrix=single_matrix,
        status=overall, provenance=prov, checks_name=checks_name,
        data_note=("数据：SIMULATED 模拟行情，流动性来源为 K 线成交额；真实行情接入后以官方逐笔成交为流动性来源" if pipeline.plan.is_simulated else "数据：官方行情"),
        files={"csv": csv_name, "json": json_name, "checks": checks_name})
    out = out_dir / f"playbook_report_{day}_{prof}.html"
    out.write_text(html, encoding="utf-8")
    return {"html": str(out), "csv": str(out_dir / csv_name), "checks_csv": str(out_dir / checks_name), "json": str(out_dir / json_name), "day": day.isoformat(),
            "validation_status": overall, "formal_validation": summary["formal_validation"],
            "tiers": [{"tier": r["tier"], "mode": r["mode_name"], "target_met": r["target_met"], "net_usd": round(r["net_usd"], 2), "max_achievable_net_usd": round(r["max_achievable_net_usd"], 2),
                       "n_trades": r["n_trades"], "n_switches": r["n_switches"], "assets": r["assets_used"], "max_drawdown_usd": round(r["max_drawdown_usd"], 2),
                       "status": r["verification"]["status"], "checks_ok": f"{r['verification']['n_ok']}/{r['verification']['n_trades']}"} for r in rows]}
