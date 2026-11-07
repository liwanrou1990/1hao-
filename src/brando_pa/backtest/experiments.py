"""第二阶段实验矩阵：入场模式 × 多周期模式 × 退出方案 × 策略，多进程并行（fork 共享已构建的上下文）。
每个变体 = 配置覆盖（params_override）+ 策略子集 + 过滤开关；产出 trades/summary/funnel，并汇总为 compare 表。"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir
from ..diagnostics.funnel import funnel_table
from .metrics import summarize
from .report import write_outputs
from .simulator import Backtester

_CTX: dict = {}


def _init(contexts):
    global _CTX
    _CTX = contexts


def _run_variant(job: dict) -> dict:
    t0 = time.time()
    bt = Backtester(job["cfg"], job["exec_tf"], job.get("strategies"), job.get("filters", True), job.get("override"), job.get("start"), job.get("end"))
    res = bt.run(_CTX)
    summ = summarize(res, _CTX)
    out_dir = Path(job["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
    paths = write_outputs(res, summ, out_dir, job["name"], None)
    fun = funnel_table(res.signals_log, res.trades, res.cancelled_entries, res.limit_log, res.strategy_counters)
    fun.to_csv(out_dir / f"{job['name']}_funnel.csv", index=False)
    if res.limit_log:
        pd.DataFrame(res.limit_log).to_csv(out_dir / f"{job['name']}_limit_orders.csv", index=False)
    ts_ = summ["trade_stats"]; es = summ["equity_stats"]
    ll = pd.DataFrame(res.limit_log)
    lim = {}
    if not ll.empty:
        canc = ll[ll["status"].str.startswith("cancel")]
        lim = {"limit_orders": int(len(ll)), "limit_filled": int((ll["status"] == "filled").sum()), "limit_cancelled": int(len(canc)),
               "limit_cancel_reasons": canc["status"].value_counts().to_dict(),
               "missed_opportunity_net_pnl_sum": float(canc["hypothetical_market_net_pnl"].sum()) if "hypothetical_market_net_pnl" in canc else None,
               "missed_opportunity_avg_r": float(canc["hypothetical_market_r"].mean()) if "hypothetical_market_r" in canc and len(canc) else None}
    row = {"name": job["name"], "exec_tf": job["exec_tf"], "label": job.get("label", ""), "n_trades": ts_.get("n_trades", 0), "win_rate": ts_.get("win_rate"), "profit_factor": ts_.get("profit_factor"), "expectancy_r": ts_.get("expectancy_r"),
           "total_net_return": es.get("total_net_return"), "max_drawdown": es.get("max_drawdown"), "sharpe": es.get("sharpe"), "sortino": es.get("sortino"), "max_consecutive_losses": ts_.get("max_consecutive_losses"),
           "total_fees": ts_.get("total_fees"), "total_funding": ts_.get("total_funding"), "total_slippage": float(res.trades["slippage_cost"].sum()) if not res.trades.empty and "slippage_cost" in res.trades else 0.0,
           "n_long": ts_.get("n_long"), "n_short": ts_.get("n_short"), "candidates": summ.get("candidates", {}).get("n_candidates"), "accepted": summ.get("candidates", {}).get("n_accepted"), "cancelled_entries": len(res.cancelled_entries),
           "by_regime4": _by(res.trades, "regime4"), "by_direction": _by(res.trades, "direction"), "by_symbol": _by(res.trades, "symbol"), "by_strategy": _by(res.trades, "strategy"), "by_year": _by(res.trades, "year"),
           "exit_reasons": ts_.get("exit_reasons"), "elapsed_s": round(time.time() - t0, 1), "paths": paths} | lim
    return row


def _by(tr: pd.DataFrame, col: str) -> dict:
    if tr is None or tr.empty or col not in tr:
        return {}
    out = {}
    for k, g in tr.groupby(col):
        w = g["net_pnl"][g["net_pnl"] > 0].sum(); l = g["net_pnl"][g["net_pnl"] < 0].sum()
        out[str(k)] = {"n": int(len(g)), "win_rate": float((g["net_pnl"] > 0).mean()), "net_pnl": float(g["net_pnl"].sum()), "expectancy_r": float(g["r_multiple"].mean()), "pf": float(w / abs(l)) if l != 0 else None}
    return out


def phase2_variants(strategies: list[str]) -> dict[str, dict]:
    """实验矩阵定义。键 = 变体名；值 = {label, strategies, override}。"""
    V: dict[str, dict] = {}
    base_exits = {}
    entry = {"market": {"execution_modes": {"entry_mode": "market"}}, "limit": {"execution_modes": {"entry_mode": "limit"}}}
    mtf = {"single": {"mtf": {"mode": "single"}}, "aligned": {"mtf": {"mode": "aligned"}}, "zone_limit": {"mtf": {"mode": "zone_limit"}, "execution_modes": {"entry_mode": "limit"}}}
    for sk in strategies + ["ALL"]:
        strat = None if sk == "ALL" else [sk]
        for en, eo in entry.items():
            for mm, mo in mtf.items():
                if mm == "zone_limit" and en == "market":
                    continue
                name = f"{sk}__entry_{en}__mtf_{mm}"
                V[name] = {"label": f"{sk} / 入场 {en} / 多周期 {mm} / 退出 基线", "strategies": strat, "override": _merge(eo, mo, base_exits)}
    exits = {
        "fixed_structure": {"exits": {"stop_mode": "fixed_structure"}},
        "no_breakeven": {"exits": {"breakeven_after_rr": None}},
        "rr_target_only": {"exits": {"tp_mode": "rr", "breakeven_after_rr": None}},
        "structure_exit": {"exits": {"structure_exit": True}},
        "atr_trail": {"exits": {"breakeven_after_rr": None, "atr_trail": {"after_rr": 1.5, "atr_mult": 2.0}}},
        "partial_tp": {"exits": {"partial_tp": {"at_rr": 1.0, "fraction": 0.5}, "breakeven_after_rr": None}},
        "no_time_exit": {"exits": {"time_exit": False}},
    }
    for sk in strategies + ["ALL"]:
        strat = None if sk == "ALL" else [sk]
        for xn, xo in exits.items():
            V[f"{sk}__exit_{xn}"] = {"label": f"{sk} / 入场 market / 多周期 single / 退出 {xn}", "strategies": strat, "override": _merge({"execution_modes": {"entry_mode": "market"}}, {"mtf": {"mode": "single"}}, xo)}
    return V


def _merge(*ds: dict) -> dict:
    out: dict = {}
    for d in ds:
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _merge(out[k], v)
            else:
                out[k] = json.loads(json.dumps(v)) if isinstance(v, (dict, list)) else v
    return out


def run_experiments(contexts: dict, exec_tf: str, name: str, start: str | None, end: str | None, variants: dict[str, dict] | None = None, c: dict | None = None, workers: int | None = None, filters: bool = True) -> dict:
    c = c or cfg()
    strategies = [k for k, v in c["strategies"].items() if isinstance(v, dict) and v.get("enabled", True)]
    variants = variants or phase2_variants(strategies)
    out_dir = report_dir(c) / "phase2" / "experiments" / name
    jobs = [{"cfg": c, "exec_tf": exec_tf, "name": vn, "label": v.get("label", ""), "strategies": v.get("strategies"), "override": v.get("override"), "filters": filters, "start": start, "end": end, "out_dir": str(out_dir)} for vn, v in variants.items()]
    workers = workers or max(1, min(os.cpu_count() or 1, 4))
    rows = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(contexts,)) as ex:
        for r in ex.map(_run_variant, jobs):
            rows.append(r)
            print(f"[{name}] {r['name']}: n={r['n_trades']} PF={_f(r['profit_factor'])} ExpR={_f(r['expectancy_r'], 3)} ret={_f(r['total_net_return'])} dd={_f(r['max_drawdown'])} ({r['elapsed_s']}s)", flush=True)
    out = {"name": name, "exec_tf": exec_tf, "symbols": list(contexts), "start": start, "end": end, "variants": {r["name"]: r for r in rows}}
    atomic_write_json(out_dir / "experiments.json", out, indent=2, default=str)
    atomic_write_text(out_dir / "experiments.md", experiments_markdown(out))
    return out


def _f(x, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x:.{d}f}"


def experiments_markdown(out: dict) -> str:
    L = [f"# 第二阶段实验矩阵 {out['name']}（执行周期 {out['exec_tf']}，{out['start']} → {out['end'] or '最新'}）", "",
         "| 变体 | 交易数 | 胜率 | PF | ExpR | 总净收益 | 最大回撤 | Sharpe | 连亏 | 手续费 | 滑点 | 资金费 | 候选→通过 | 限价: 挂/成/撤 | 未成交机会 PnL |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in out["variants"].items():
        lim = f"{r.get('limit_orders', '—')}/{r.get('limit_filled', '—')}/{r.get('limit_cancelled', '—')}" if r.get("limit_orders") is not None else "—"
        L.append(f"| {n} | {r['n_trades']} | {_f((r['win_rate'] or 0) * 100, 1)}% | {_f(r['profit_factor'])} | {_f(r['expectancy_r'], 3)} | {_f((r['total_net_return'] or 0) * 100)}% | {_f((r['max_drawdown'] or 0) * 100)}% | {_f(r['sharpe'])} | {r['max_consecutive_losses'] or '—'} | {_f(r['total_fees'])} | {_f(r['total_slippage'])} | {_f(r['total_funding'])} | {r.get('candidates')}→{r.get('accepted')} | {lim} | {_f(r.get('missed_opportunity_net_pnl_sum'))} |")
    return "\n".join(L) + "\n"
