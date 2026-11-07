"""回测编排：加载上下文 → 跑组合矩阵（全部策略 / 单策略 × 过滤开/关）→ 写产物 → 汇总 compare.json/compare.md。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir
from ..data.store import BarStore
from ..engine.context import MarketContext
from .metrics import summarize
from .report import write_outputs
from .simulator import Backtester

LEVEL_TFS = ("1w", "1d", "4h")


def load_contexts(symbols: list[str], exec_tf: str, c: dict | None = None, store: BarStore | None = None, extras_loader=None) -> dict[str, MarketContext]:
    c = c or cfg(); store = store or BarStore()
    out = {}
    for s in symbols:
        bars = {tf: store.load(s, tf) for tf in set(LEVEL_TFS + (exec_tf,))}
        missing = [tf for tf, df in bars.items() if df.empty]
        if exec_tf in missing:
            raise FileNotFoundError(f"{s} 缺少 {exec_tf} K 线，请先运行 ingest")
        extras = extras_loader(s) if extras_loader else None
        out[s] = MarketContext(s, bars, exec_tf, c, extras)
    return out


def run_matrix(symbols: list[str], exec_tf: str, start: str | None, end: str | None, name: str, c: dict | None = None, variants: list[str] | None = None, out_dir: Path | None = None) -> dict:
    c = c or cfg()
    out_dir = out_dir or (report_dir(c) / "backtest" / name)
    ctxs = load_contexts(symbols, exec_tf, c)
    strategies = [k for k, v in c["strategies"].items() if isinstance(v, dict) and v.get("enabled", True)]
    variants = variants or ["all_filters", "all_nofilters"] + [f"{k}_filters" for k in strategies] + [f"{k}_nofilters" for k in strategies]
    compare = {}
    for v in variants:
        strat_keys = None if v.startswith("all_") else [v.rsplit("_", 1)[0]]
        filters = v.endswith("_filters")
        t0 = time.time()
        res = Backtester(c, exec_tf, strat_keys, filters, start=start, end=end).run(ctxs)
        summ = summarize(res, ctxs)
        paths = write_outputs(res, summ, out_dir, v, ctxs if v.startswith("all_") else None)
        ts_ = summ["trade_stats"]; es = summ["equity_stats"]
        compare[v] = {"n_trades": ts_.get("n_trades", 0), "win_rate": ts_.get("win_rate"), "profit_factor": ts_.get("profit_factor"), "expectancy_r": ts_.get("expectancy_r"),
                      "total_net_return": es.get("total_net_return"), "max_drawdown": es.get("max_drawdown"), "sharpe": es.get("sharpe"), "sortino": es.get("sortino"),
                      "max_consecutive_losses": ts_.get("max_consecutive_losses"), "total_costs": (ts_.get("total_fees") or 0) + (ts_.get("total_funding") or 0),
                      "by_regime": summ.get("by_regime"), "candidates": summ.get("candidates", {}).get("n_candidates"), "accepted": summ.get("candidates", {}).get("n_accepted"),
                      "elapsed_s": round(time.time() - t0, 1), "paths": paths}
        print(f"[{name}] {v}: trades={compare[v]['n_trades']} PF={compare[v]['profit_factor']} expR={compare[v]['expectancy_r']} ret={compare[v]['total_net_return']} dd={compare[v]['max_drawdown']} ({compare[v]['elapsed_s']}s)", flush=True)
    bh = summarize(res, ctxs).get("buy_and_hold", {})
    out = {"name": name, "exec_tf": exec_tf, "symbols": symbols, "start": start, "end": end, "period": list(res.period), "buy_and_hold": bh, "variants": compare}
    atomic_write_json(out_dir / "compare.json", out, indent=2, default=str)
    atomic_write_text(out_dir / "compare.md", compare_markdown(out))
    return out


def _f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


def compare_markdown(out: dict) -> str:
    bh = out.get("buy_and_hold", {})
    lines = [f"# 回测矩阵 {out['name']}（执行周期 {out['exec_tf']}，{out['period'][0]} → {out['period'][1]}）", "",
             f"买入持有基准（期初买入期末卖出，各扣 10 bps）：" + "，".join(f"{k} {_f(v.get('return'), True)}（回撤 {_f(v.get('max_drawdown'), True)}）" for k, v in bh.items() if isinstance(v, dict) and 'return' in v), "",
             "| 组合 | 交易数 | 胜率 | PF | Expectancy(R) | 总净收益 | 最大回撤 | Sharpe | 最大连亏 | 成本 USDT | 候选→通过 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, r in out["variants"].items():
        lines.append(f"| {v} | {r['n_trades']} | {_f(r['win_rate'], True, 1)} | {_f(r['profit_factor'])} | {_f(r['expectancy_r'], d=3)} | {_f(r['total_net_return'], True)} | {_f(r['max_drawdown'], True)} | {_f(r['sharpe'])} | {r['max_consecutive_losses'] or '—'} | {_f(r['total_costs'])} | {r.get('candidates')}→{r.get('accepted')} |")
    lines += ["", "## 各市场环境（入场时日线环境）", ""]
    for v, r in out["variants"].items():
        if r.get("by_regime"):
            lines.append(f"- **{v}**：" + "；".join(f"{k}: {g['n_trades']} 笔 / 胜率 {_f(g['win_rate'], True, 0)} / ExpR {_f(g['expectancy_r'], d=2)} / 净 {_f(g['net_pnl'])}" for k, g in r["by_regime"].items()))
    return "\n".join(lines) + "\n"
