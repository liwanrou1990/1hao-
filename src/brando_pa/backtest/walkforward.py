"""Walk-Forward 验证（第二阶段升级版）。

* 时间顺序：训练 → （隔离 embargo_days）→ 验证 → （隔离）→ 测试；滚动步长 = 测试窗；测试窗参数固定、只评估一次。
* 参数网格支持任意配置路径（策略参数与退出参数），形如 {"strategies.A_breakout_retest.sl_buffer_atr": [0.3, 0.5], "exits.breakeven_after_rr": [None, 1.5]}。
* 选参标准：训练窗 Expectancy(R)（交易数 ≥ min_trades_select）；验证窗 ExpR < 0 → 退回默认参数（如实记录）。
* 输出：每策略 OOS 全指标（oos_metrics.full_oos_report）、按标的/方向子集、默认参数全程对照、参数扰动稳定性（默认参数 ±20%，在 OOS 跨度上重跑）。
* 上下文由完整历史预计算（每个时点只用 ≤ 该时点的 K 线），窗口只限制评估的执行 K 线范围。
"""
from __future__ import annotations

import copy
import itertools
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import cfg
from .metrics import trade_stats
from .oos_metrics import full_oos_report
from .simulator import Backtester

_CTX: dict = {}


def _init(contexts):
    global _CTX
    _CTX = contexts


def set_path(d: dict, path: str, value) -> None:
    keys = path.split("."); cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def get_path(d: dict, path: str, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def override_from_params(params: dict) -> dict:
    o: dict = {}
    for k, v in params.items():
        set_path(o, k, v)
    return o


def _run_job(job: dict) -> dict:
    bt = Backtester(job["cfg"], job["exec_tf"], [job["strategy"]], job["filters"], override_from_params(job["params"]), job["start"], job["end"], job["equity"])
    res = bt.run(_CTX)
    ts = trade_stats(res.trades)
    return {"job": {k: v for k, v in job.items() if k != "cfg"}, "n_trades": ts.get("n_trades", 0), "expectancy_r": ts.get("expectancy_r"), "profit_factor": ts.get("profit_factor"),
            "net_pnl": ts.get("total_net_pnl", 0.0), "trades": res.trades}


@dataclass
class WFFold:
    fold: int
    train: tuple[str, str]
    valid: tuple[str, str]
    test: tuple[str, str]
    chosen_params: dict
    fallback_to_default: bool
    train_table: list[dict]
    valid_stats: dict
    test_stats: dict


@dataclass
class WFResult:
    strategy: str
    folds: list[WFFold]
    oos_trades: pd.DataFrame
    oos_stats: dict
    default_full_stats: dict
    default_full_trades: pd.DataFrame
    perturbation: dict = field(default_factory=dict)
    subsets: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def month_windows(start: pd.Timestamp, end: pd.Timestamp, train_m: int, valid_m: int, test_m: int, embargo_days: int = 0) -> list[tuple]:
    out = []; t0 = start; emb = pd.Timedelta(days=embargo_days)
    while True:
        t1 = t0 + pd.DateOffset(months=train_m); v0 = t1 + emb; v1 = v0 + pd.DateOffset(months=valid_m); s0 = v1 + emb; s1 = s0 + pd.DateOffset(months=test_m)
        if s1 > end + pd.Timedelta(days=1):
            break
        out.append(((t0, t1), (v0, v1), (s0, s1)))
        t0 = t0 + pd.DateOffset(months=test_m)
    return out


def _grid(g: dict) -> list[dict]:
    keys = list(g); return [dict(zip(keys, vals)) for vals in itertools.product(*[g[k] for k in keys])] if keys else [{}]


def _norm_grid(strategy: str, g: dict) -> dict:
    """兼容旧写法（策略参数名）与新写法（完整路径）。"""
    out = {}
    for k, v in (g or {}).items():
        out[k if "." in k else f"strategies.{strategy}.{k}"] = v
    return out


def _perturbations(default_params: dict, c: dict, strategy: str) -> list[tuple[str, dict]]:
    out = []
    for k, v in default_params.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None:
            for mult in (0.8, 1.2):
                out.append((f"{k}×{mult}", {**default_params, k: type(v)(v * mult) if isinstance(v, int) and v * mult == int(v * mult) else v * mult}))
    return out


def run_walkforward(contexts: dict, exec_tf: str, strategy: str, c: dict | None = None, filters_enabled: bool = True, start: str | None = None, end: str | None = None,
                    workers: int | None = None, perturb: bool = True) -> WFResult:
    c = c or cfg(); wf = c["backtest"]["walkforward"]
    _init(contexts)
    ts_all = sorted(set().union(*[set(mc.ts.tolist()) for mc in contexts.values()]))
    first = max(pd.Timestamp(start, tz="UTC") if start else ts_all[0], ts_all[0]).normalize(); last = min(pd.Timestamp(end, tz="UTC") if end else ts_all[-1], ts_all[-1])
    embargo = int(wf.get("embargo_days", 0))
    wins = month_windows(first, last, int(wf["train_months"]), int(wf["valid_months"]), int(wf["test_months"]), embargo)
    grid_def = _norm_grid(strategy, wf["grid"].get(strategy, {}))
    grid = _grid(grid_def)
    default_params = {k: get_path(c, k) for k in grid_def}
    eq0 = float(c["backtest"]["initial_equity"])
    base = {"cfg": c, "exec_tf": exec_tf, "strategy": strategy, "filters": filters_enabled, "equity": eq0}
    jobs = [base | {"params": params, "start": str(tr[0]), "end": str(tr[1]), "fold": fi, "phase": "train", "gi": gi} for fi, (tr, va, te) in enumerate(wins) for gi, params in enumerate(grid)]
    workers = workers or max(1, min(os.cpu_count() or 1, 4))
    folds: list[WFFold] = []; oos_frames = []; notes = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(contexts,)) as ex:
        train_res = list(ex.map(_run_job, jobs))
        chosen = {}; val_jobs = []
        for fi, (tr, va, te) in enumerate(wins):
            rows = [r for r in train_res if r["job"]["fold"] == fi]
            table = [{"params": r["job"]["params"], "n_trades": r["n_trades"], "expectancy_r": r["expectancy_r"], "profit_factor": r["profit_factor"], "net_pnl": r["net_pnl"]} for r in rows]
            eligible = [r for r in rows if r["n_trades"] >= int(wf["min_trades_select"]) and r["expectancy_r"] is not None]
            best = max(eligible, key=lambda r: r["expectancy_r"]) if eligible else None
            chosen[fi] = [best["job"]["params"] if best else default_params, table, best is None]
            val_jobs.append(base | {"params": chosen[fi][0], "start": str(va[0]), "end": str(va[1]), "fold": fi, "phase": "valid", "gi": -1})
        val_res = list(ex.map(_run_job, val_jobs))
        test_jobs = []
        for fi, (tr, va, te) in enumerate(wins):
            params, table, no_elig = chosen[fi]; vr = val_res[fi]
            fallback = no_elig or (vr["expectancy_r"] is not None and vr["expectancy_r"] < 0 and params != default_params)
            final = default_params if fallback else params
            chosen[fi] = [final, table, fallback, vr]
            test_jobs.append(base | {"params": final, "start": str(te[0]), "end": str(te[1]), "fold": fi, "phase": "test", "gi": -1})
        test_res = list(ex.map(_run_job, test_jobs))
        oos_start = str(wins[0][2][0]) if wins else str(first)
        full_job = base | {"params": default_params, "start": oos_start, "end": str(last), "fold": -1, "phase": "default_full", "gi": -1}
        pert_jobs = [base | {"params": pp, "start": oos_start, "end": str(last), "fold": -1, "phase": f"perturb:{lab}", "gi": -1} for lab, pp in (_perturbations(default_params, c, strategy) if perturb else [])]
        extra = list(ex.map(_run_job, [full_job] + pert_jobs))
    full = extra[0]; perts = extra[1:]
    for fi, (tr, va, te) in enumerate(wins):
        params, table, fallback, vr = chosen[fi]; trs = test_res[fi]
        folds.append(WFFold(fi, (str(tr[0])[:10], str(tr[1])[:10]), (str(va[0])[:10], str(va[1])[:10]), (str(te[0])[:10], str(te[1])[:10]), params, bool(fallback), table,
                            {"n_trades": vr["n_trades"], "expectancy_r": vr["expectancy_r"], "net_pnl": vr["net_pnl"]}, {"n_trades": trs["n_trades"], "expectancy_r": trs["expectancy_r"], "net_pnl": trs["net_pnl"], "profit_factor": trs["profit_factor"]}))
        if not trs["trades"].empty:
            oos_frames.append(trs["trades"].assign(fold=fi))
    oos = pd.concat(oos_frames, ignore_index=True) if oos_frames else pd.DataFrame()
    oos_stats = full_oos_report(oos, eq0, [f.test_stats for f in folds])
    subsets = {}
    if not oos.empty:
        for col in ("symbol", "direction"):
            for k, g in oos.groupby(col):
                subsets[f"{col}={k}"] = full_oos_report(g, eq0)
        for (s_, d_), g in oos.groupby(["symbol", "direction"]):
            subsets[f"{s_}/{d_}"] = full_oos_report(g, eq0)
    pert = {"default": {"n_trades": full["n_trades"], "expectancy_r": full["expectancy_r"], "profit_factor": full["profit_factor"], "net_pnl": full["net_pnl"]}}
    for lab, r in zip([lab for lab, _ in (_perturbations(default_params, c, strategy) if perturb else [])], perts):
        pert[lab] = {"n_trades": r["n_trades"], "expectancy_r": r["expectancy_r"], "profit_factor": r["profit_factor"], "net_pnl": r["net_pnl"]}
    if len(pert) > 1:
        exps = [v["expectancy_r"] for k, v in pert.items() if v["expectancy_r"] is not None]
        pert["summary"] = {"expectancy_r_min": float(min(exps)), "expectancy_r_max": float(max(exps)), "share_positive": float(np.mean([e > 0 for e in exps])), "n_variants": len(exps)}
    if not wins:
        notes.append("数据长度不足以构成一个 训练+验证+测试 窗口")
    notes.append(f"训练 {wf['train_months']} 月 / 验证 {wf['valid_months']} 月 / 测试 {wf['test_months']} 月，隔离 {embargo} 天，滚动步长 = 测试窗；网格 {grid_def}；选参 Expectancy(R) 且 ≥ {wf['min_trades_select']} 笔；验证窗为负退回默认")
    return WFResult(strategy, folds, oos, oos_stats, {"n_trades": full["n_trades"], "expectancy_r": full["expectancy_r"], "net_pnl": full["net_pnl"], "profit_factor": full["profit_factor"], "span": [oos_start, str(last)]},
                    full["trades"], pert, subsets, notes)


def wf_to_dict(r: WFResult) -> dict:
    return {"strategy": r.strategy, "oos_stats": r.oos_stats, "default_full_stats": r.default_full_stats, "perturbation": r.perturbation, "subsets": r.subsets, "notes": r.notes,
            "folds": [{"fold": f.fold, "train": f.train, "valid": f.valid, "test": f.test, "chosen_params": f.chosen_params, "fallback_to_default": f.fallback_to_default,
                       "train_table": f.train_table, "valid_stats": f.valid_stats, "test_stats": f.test_stats} for f in r.folds]}
