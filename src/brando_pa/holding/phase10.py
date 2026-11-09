"""第十阶段流程：预登记策略集合 × 成本 × 窗口 → 结果 JSON/CSV → 中文汇总。全部参数来自 preregistration.md，不做搜索。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir
from .data import load_dataset
from .engine import HoldingBacktester
from .metrics import summarize

BOTH = ["BTCUSDT", "ETHUSDT"]
RISK_EXIT = 0.20
STRATEGIES = [
    {"name": "S1_BTC_SMA200", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S2_ETH_SMA200", "assets": ["ETHUSDT"], "mode": "trend", "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S3_DYN_BTC_ETH", "assets": BOTH, "mode": "trend", "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S4_HOLD30", "assets": BOTH, "mode": "fixed", "min_hold": 30, "max_hold": 30, "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S5_HOLD60", "assets": BOTH, "mode": "fixed", "min_hold": 60, "max_hold": 60, "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S6_HOLD90", "assets": BOTH, "mode": "fixed", "min_hold": 90, "max_hold": 90, "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S7_HOLD90_EXTEND", "assets": BOTH, "mode": "minhold", "min_hold": 90, "max_hold": None, "risk_exit_dd": RISK_EXIT, "group": "core"},
    {"name": "S8a_BUYHOLD_BTC", "assets": ["BTCUSDT"], "mode": "buyhold", "group": "benchmark_bh"},
    {"name": "S8b_BUYHOLD_ETH", "assets": ["ETHUSDT"], "mode": "buyhold", "group": "benchmark_bh"},
    {"name": "S8c_BUYHOLD_5050", "assets": BOTH, "mode": "buyhold", "group": "benchmark_bh"},
    {"name": "B1_BTC_SMA200_daily_repro", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None, "group": "brando_baseline_daily_repro", "note": "原账户 A（BTC SMA200，无熔断）日线执行复现；原始为 1h 执行、2022-04 起"},
    {"name": "B2_modelB_state_machine_daily_repro", "assets": BOTH, "mode": "trend", "risk_exit_dd": None, "risk_mode": "state_machine", "group": "brando_baseline_daily_repro", "note": "原账户 B（模型 B 等权 + 熔断状态机）日线执行复现；原始为 1h 执行"},
    {"name": "B3_ETH_SMA200_daily_repro", "assets": ["ETHUSDT"], "mode": "trend", "risk_exit_dd": None, "group": "brando_baseline_daily_repro", "note": "原账户 REF（ETH SMA200，无熔断）日线执行复现"},
    {"name": "X_MINHOLD30", "assets": BOTH, "mode": "minhold", "min_hold": 30, "max_hold": None, "risk_exit_dd": RISK_EXIT, "group": "supplementary"},
    {"name": "X_MINHOLD60", "assets": BOTH, "mode": "minhold", "min_hold": 60, "max_hold": None, "risk_exit_dd": RISK_EXIT, "group": "supplementary"},
]
COSTS = {0: (0.0, 0.0), 10: (5.0, 5.0), 15: (10.0, 5.0), 25: (20.0, 5.0)}      # one-side bps → (fee_bps, slip_bps)
WINDOWS = {"full": ("2018-01-01", None), "train": ("2018-01-01", "2021-12-31"), "valid": ("2022-01-01", "2023-12-31"), "oos": ("2024-01-01", None)}
RANK_COST = 25


def phase10_out_dir(c: dict | None = None) -> Path:
    d = report_dir(c) / "phase10_holding"; d.mkdir(parents=True, exist_ok=True); return d


def run_one(bars: dict, spec: dict, cost_bps: int, start: str, end: str | None, c: dict, initial: float = 10000.0):
    fee, slip = COSTS[cost_bps]
    return HoldingBacktester(bars, spec, fee, slip, start, end, initial=initial, sma=int(c["usdt"]["trend"]["sma"]), risk_params=c.get("trendsys", {}).get("risk", {})).run()


def run_phase10(c: dict | None = None, out_dir: Path | None = None, dry_run: bool = False, costs: list[int] | None = None, strategies: list[dict] | None = None, write_curves: bool = True) -> dict:
    c = c or cfg(); out = Path(out_dir or phase10_out_dir(c)); out.mkdir(parents=True, exist_ok=True); bars = load_dataset(c)
    latest = min(str(bars[s]["ts"].iloc[-1].date()) for s in BOTH)
    windows = {"dry": ("2023-01-01", "2023-12-31")} if dry_run else {k: (a, b or latest) for k, (a, b) in WINDOWS.items()}
    strategies = strategies or (STRATEGIES[:3] + STRATEGIES[7:8] if dry_run else STRATEGIES); costs = costs or ([25] if dry_run else list(COSTS))
    years = sorted(set(bars["BTCUSDT"]["ts"].dt.year)) if not dry_run else []
    btc_close = bars["BTCUSDT"].set_index(pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True)))["close"]
    rows = []; curves = {}; trades_all = []
    for w, (a, b) in windows.items():
        for cost in costs:
            for spec in strategies:
                r = run_one(bars, spec, cost, a, b, c); sm = summarize(r, btc_close) | {"window": w, "cost_bps": cost, "group": spec.get("group"), "note": spec.get("note"), "spec": {k: v for k, v in spec.items() if k not in ("name", "group", "note")}}
                rows.append(sm)
                if write_curves and w in ("full", "oos", "dry") and cost in (RANK_COST, 0):
                    curves[f"{w}|{cost}|{spec['name']}"] = r.nav[["ts", "equity", "liquidation", "exposure", "drawdown"]].assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
                if len(r.trades):
                    trades_all.append(r.trades.assign(strategy=spec["name"], window=w, cost_bps=cost))
    # 逐年独立起算（全部策略，RANK_COST）
    yearly = []
    for y in [yy for yy in years if yy >= 2018]:
        for spec in strategies:
            try:
                r = run_one(bars, spec, RANK_COST, f"{y}-01-01", min(f"{y}-12-31", latest), c); f = r.final
                yearly.append({"year": y, "strategy": spec["name"], "cost_bps": RANK_COST, "liquidation_net_profit_usdt": f["liquidation_net_profit_usdt"], "equity_profit_usdt": f["equity_profit_usdt"], "n_fills": f["n_fills"]})
            except AssertionError:
                pass
    # 超额收益（同窗口同成本 vs 三个买入持有）
    bh = {(r["window"], r["cost_bps"], r["name"]): r for r in rows if r["group"] == "benchmark_bh"}
    for r in rows:
        r["excess_vs_bh"] = {k: r["liquidation_net_profit_usdt"] - bh[(r["window"], r["cost_bps"], k)]["liquidation_net_profit_usdt"] for k in ("S8a_BUYHOLD_BTC", "S8b_BUYHOLD_ETH", "S8c_BUYHOLD_5050") if (r["window"], r["cost_bps"], k) in bh}
        r["mdd_vs_bh"] = {k: r["max_drawdown"] - bh[(r["window"], r["cost_bps"], k)]["max_drawdown"] for k in ("S8a_BUYHOLD_BTC", "S8b_BUYHOLD_ETH", "S8c_BUYHOLD_5050") if (r["window"], r["cost_bps"], k) in bh}
    # 排名：full × RANK_COST，核心 + 基准，按假设清仓净收益
    def ranking(window: str, cost: int) -> list[dict]:
        sel = sorted([r for r in rows if r["window"] == window and r["cost_bps"] == cost and r["group"] != "supplementary"], key=lambda r: -r["liquidation_net_profit_usdt"])
        return [{"rank": i + 1, "name": r["name"], "group": r["group"], "liquidation_net_profit_usdt": r["liquidation_net_profit_usdt"], "equity_profit_usdt": r["equity_profit_usdt"], "cagr_liquidation": r["cagr_liquidation"], "max_drawdown": r["max_drawdown"], "n_fills": r["n_fills"], "total_cost_usdt": r["total_cost_usdt"], "excess_vs_bh": r.get("excess_vs_bh")} for i, r in enumerate(sel)]
    rankings = {f"{w}|{cost}": ranking(w, cost) for w in windows for cost in costs}
    res = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；日线决策、次日开盘执行；非前瞻、非回放）", "dry_run": dry_run, "latest_complete_day": latest, "windows": windows, "costs_one_side_bps": costs, "rank_cost_bps": RANK_COST, "initial_usdt": 10000.0,
           "preregistration": "docs/brando_pa_validation/phase10_holding/preregistration.md", "rows": rows, "rankings": rankings, "yearly_independent": yearly, "real_trading": "disabled (no implementation)"}
    atomic_write_json(out / ("phase10_dry_run.json" if dry_run else "phase10_results.json"), res, indent=1, default=str)
    if write_curves:
        atomic_write_json(out / ("curves_dry.json" if dry_run else "curves.json"), curves, default=str)
    if trades_all:
        pd.concat(trades_all, ignore_index=True).to_csv(out / ("trades_dry.csv" if dry_run else "trades.csv"), index=False)
    atomic_write_text(out / ("summary_dry.md" if dry_run else "summary.md"), render_summary(res)); return res


def _pct(x):
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x*100:.1f}%"


def render_summary(res: dict) -> str:
    L = [f"# 第十阶段结果汇总 · {res['generated_at_utc'][:19]} UTC · {res['category']}", "", f"数据截至 {res['latest_complete_day']}（最新已收盘日线）；初始本金 10,000 USDT；排名依据：{res['rank_cost_bps']} bps 单侧成本下的假设清仓净收益。{'**DRY-RUN（短窗口、部分策略）**' if res['dry_run'] else ''}", ""]
    for key, rk in res["rankings"].items():
        w, cost = key.split("|")
        L += [f"## 窗口 {w}（{res['windows'][w][0]} → {res['windows'][w][1]}）· 单侧成本 {cost} bps", "", "| 名次 | 策略 | 组 | 假设清仓净收益 | 账面利润 | 清仓 CAGR | 最大回撤 | 成交笔数 | 总成本 | 超额 vs BTC 持有 | vs ETH 持有 | vs 50/50 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rk:
            ex = r.get("excess_vs_bh") or {}
            L.append(f"| {r['rank']} | {r['name']} | {r['group']} | {r['liquidation_net_profit_usdt']:+,.0f} | {r['equity_profit_usdt']:+,.0f} | {_pct(r['cagr_liquidation'])} | {_pct(r['max_drawdown'])} | {r['n_fills']} | {r['total_cost_usdt']:,.0f} | {ex.get('S8a_BUYHOLD_BTC', 0):+,.0f} | {ex.get('S8b_BUYHOLD_ETH', 0):+,.0f} | {ex.get('S8c_BUYHOLD_5050', 0):+,.0f} |")
        L.append("")
    return "\n".join(L)
