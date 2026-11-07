"""BenchmarkEvaluator：十组对照的统一指标表（绝对收益、年化、最大回撤、Sharpe、Calmar、交易数、资金利用率、上涨/下跌月捕获、与买入持有差距）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..spot.report import equity_stats, trade_stats, utilization, yearly


def evaluate(name: str, eq: pd.DataFrame, trades: pd.DataFrame | None, initial: float, exposure_metrics: dict | None = None, bench_return: float | None = None) -> dict:
    es = equity_stats(eq, initial); ts = trade_stats(trades) if trades is not None else {"n_trades": 0}
    ut = utilization(eq) if "exposure" in eq else {}
    row = {"name": name, "total_return": es.get("total_return"), "cagr": es.get("cagr"), "max_drawdown": es.get("max_drawdown"), "sharpe": es.get("sharpe_daily"), "calmar": es.get("calmar"),
           "n_trades": ts.get("n_trades", 0), "win_rate": ts.get("win_rate"), "profit_factor": ts.get("profit_factor"), "avg_exposure": ut.get("avg_exposure"), "time_in_market": ut.get("time_in_market_share"),
           "yearly": yearly(eq), "exit_reasons": ts.get("by_exit"), "by_strategy": ts.get("by_strategy")}
    if exposure_metrics:
        row |= {k: exposure_metrics.get(k) for k in ("bull_time_in_market_share", "up_month_capture", "down_month_capture", "idle_share", "idle_longest_days", "missed_after_sell_sum", "reentries_after_stop", "avg_hold_days", "gap_vs_bh_final", "gap_vs_bh_min")}
    if bench_return is not None and es.get("total_return") is not None:
        row["excess_vs_bh"] = es["total_return"] - bench_return
    row["low_dd_due_to_idle_cash"] = bool((ut.get("avg_exposure") or 0) < 0.3 and (es.get("max_drawdown") or -1) > -0.15)
    return row


def _f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


def table(rows: list[dict]) -> str:
    cols = [("总收益", "total_return", True), ("年化", "cagr", True), ("最大回撤", "max_drawdown", True), ("Sharpe", "sharpe", False), ("Calmar", "calmar", False), ("交易", "n_trades", None), ("胜率", "win_rate", True), ("PF", "profit_factor", False),
            ("平均仓位", "avg_exposure", True), ("持仓时间", "time_in_market", True), ("BULL期持仓", "bull_time_in_market_share", True), ("上涨月捕获", "up_month_capture", True), ("下跌月捕获", "down_month_capture", True), ("卖后错过合计", "missed_after_sell_sum", True),
            ("止损后再入", "reentries_after_stop", None), ("均持仓天", "avg_hold_days", False), ("超额 vs B&H", "excess_vs_bh", True), ("低回撤因空仓", "low_dd_due_to_idle_cash", None)]
    L = ["| 组合 | " + " | ".join(c[0] for c in cols) + " |", "|---|" + "---|" * len(cols)]
    for r in rows:
        vals = []
        for _, k, pct in cols:
            v = r.get(k)
            vals.append(str(v) if pct is None or v is None or isinstance(v, bool) else _f(v, pct))
        L.append(f"| {r['name']} | " + " | ".join(vals) + " |")
    return "\n".join(L)
