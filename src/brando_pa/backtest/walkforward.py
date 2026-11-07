"""Walk-Forward 验证：按时间顺序 训练 → 验证 → 样本外测试，滚动前进；参数只在训练窗选，验证窗确认，测试窗只评估一次。

* 每个策略独立跑（独立组合、独立参数网格），结果分别报告；不把三套策略混在一起。
* 选择标准：训练窗 Expectancy(R)（交易数 ≥ min_trades_select，否则该组合不可选）；验证窗 Expectancy(R) < 0 → 退回默认参数（如实记录）。
* 全部测试窗拼接成 OOS 序列，并与“默认参数直接跑全程”对照，暴露过拟合。
* 高周期区域/ATR 由完整历史预计算（PIT 安全，因为每个时点只用 ≤ 该时点的 K 线），窗口只限制“评估的执行 K 线范围”。
"""
from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import pandas as pd

from ..config import cfg, deep_update
from .metrics import equity_stats, trade_stats
from .simulator import Backtester

_CTX: dict = {}


def _init(contexts):
    global _CTX
    _CTX = contexts


def _run_job(job: dict) -> dict:
    bt = Backtester(job["cfg"], job["exec_tf"], [job["strategy"]], job["filters"], {"strategies": {job["strategy"]: job["params"]}}, job["start"], job["end"], job["equity"])
    res = bt.run(_CTX)
    ts = trade_stats(res.trades)
    return {"job": {k: v for k, v in job.items() if k != "cfg"}, "n_trades": ts.get("n_trades", 0), "expectancy_r": ts.get("expectancy_r"), "profit_factor": ts.get("profit_factor"),
            "net_pnl": ts.get("total_net_pnl", 0.0), "trades": res.trades, "equity": res.equity}


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
    notes: list[str] = field(default_factory=list)


def month_windows(start: pd.Timestamp, end: pd.Timestamp, train_m: int, valid_m: int, test_m: int) -> list[tuple]:
    out = []
    t0 = start
    while True:
        t1 = t0 + pd.DateOffset(months=train_m); v1 = t1 + pd.DateOffset(months=valid_m); s1 = v1 + pd.DateOffset(months=test_m)
        if s1 > end + pd.Timedelta(days=1):
            break
        out.append(((t0, t1), (t1, v1), (v1, s1)))
        t0 = t0 + pd.DateOffset(months=test_m)
    return out


def _grid(g: dict) -> list[dict]:
    keys = list(g); return [dict(zip(keys, vals)) for vals in itertools.product(*[g[k] for k in keys])] if keys else [{}]


def run_walkforward(contexts: dict, exec_tf: str, strategy: str, c: dict | None = None, filters_enabled: bool = True, start: str | None = None, end: str | None = None, workers: int | None = None) -> WFResult:
    c = c or cfg(); wf = c["backtest"]["walkforward"]
    ts_all = sorted(set().union(*[set(mc.ts.tolist()) for mc in contexts.values()]))
    first = max(pd.Timestamp(start, tz="UTC") if start else ts_all[0], ts_all[0]); last = min(pd.Timestamp(end, tz="UTC") if end else ts_all[-1], ts_all[-1])
    first = first.normalize()
    wins = month_windows(first, last, int(wf["train_months"]), int(wf["valid_months"]), int(wf["test_months"]))
    grid = _grid(wf["grid"].get(strategy, {}))
    default_params = {k: c["strategies"][strategy][k] for k in (wf["grid"].get(strategy, {}) or {})}
    eq0 = float(c["backtest"]["initial_equity"])
    jobs = []
    for fi, (tr, va, te) in enumerate(wins):
        for gi, params in enumerate(grid):
            jobs.append({"cfg": c, "exec_tf": exec_tf, "strategy": strategy, "filters": filters_enabled, "params": params, "start": str(tr[0]), "end": str(tr[1]), "equity": eq0, "fold": fi, "phase": "train", "gi": gi})
    workers = workers or max(1, min(os.cpu_count() or 1, 8))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(contexts,)) as ex:
        train_res = list(ex.map(_run_job, jobs))
        folds: list[WFFold] = []; oos_frames = []; notes = []
        # 选参 → 验证
        val_jobs = []
        chosen = {}
        for fi, (tr, va, te) in enumerate(wins):
            rows = [r for r in train_res if r["job"]["fold"] == fi]
            table = [{"params": r["job"]["params"], "n_trades": r["n_trades"], "expectancy_r": r["expectancy_r"], "profit_factor": r["profit_factor"], "net_pnl": r["net_pnl"]} for r in rows]
            eligible = [r for r in rows if r["n_trades"] >= int(wf["min_trades_select"]) and r["expectancy_r"] is not None]
            best = max(eligible, key=lambda r: r["expectancy_r"]) if eligible else None
            chosen[fi] = (best["job"]["params"] if best else default_params, table, best is None)
            val_jobs.append({"cfg": c, "exec_tf": exec_tf, "strategy": strategy, "filters": filters_enabled, "params": chosen[fi][0], "start": str(va[0]), "end": str(va[1]), "equity": eq0, "fold": fi, "phase": "valid", "gi": -1})
        val_res = list(ex.map(_run_job, val_jobs))
        test_jobs = []
        for fi, (tr, va, te) in enumerate(wins):
            params, table, no_eligible = chosen[fi]
            vr = val_res[fi]
            fallback = no_eligible or (vr["expectancy_r"] is not None and vr["expectancy_r"] < 0 and params != default_params)
            final_params = default_params if fallback else params
            chosen[fi] = (final_params, table, fallback, vr)
            test_jobs.append({"cfg": c, "exec_tf": exec_tf, "strategy": strategy, "filters": filters_enabled, "params": final_params, "start": str(te[0]), "end": str(te[1]), "equity": eq0, "fold": fi, "phase": "test", "gi": -1})
        test_res = list(ex.map(_run_job, test_jobs))
        full = _run_job({"cfg": c, "exec_tf": exec_tf, "strategy": strategy, "filters": filters_enabled, "params": default_params, "start": str(wins[0][2][0]) if wins else str(first), "end": str(last), "equity": eq0, "fold": -1, "phase": "default_full", "gi": -1}) if wins else None
    for fi, (tr, va, te) in enumerate(wins):
        params, table, fallback, vr = chosen[fi]
        trs = test_res[fi]
        folds.append(WFFold(fi, (str(tr[0])[:10], str(tr[1])[:10]), (str(va[0])[:10], str(va[1])[:10]), (str(te[0])[:10], str(te[1])[:10]), params, bool(fallback), table,
                            {"n_trades": vr["n_trades"], "expectancy_r": vr["expectancy_r"], "net_pnl": vr["net_pnl"]}, {"n_trades": trs["n_trades"], "expectancy_r": trs["expectancy_r"], "net_pnl": trs["net_pnl"], "profit_factor": trs["profit_factor"]}))
        if not trs["trades"].empty:
            oos_frames.append(trs["trades"].assign(fold=fi))
    oos = pd.concat(oos_frames, ignore_index=True) if oos_frames else pd.DataFrame()
    oos_stats = trade_stats(oos)
    if not oos.empty:
        # 拼接 OOS 净值：每折从 eq0 起算的净盈亏累加
        eq = eq0 + oos.sort_values("exit_ts")["net_pnl"].cumsum()
        oos_stats["stitched_total_return"] = float(eq.iloc[-1] / eq0 - 1)
        oos_stats["stitched_max_drawdown"] = float((eq / eq.cummax() - 1).min())
    if not wins:
        notes.append("数据长度不足以构成一个 训练+验证+测试 窗口")
    return WFResult(strategy, folds, oos, oos_stats, {"n_trades": full["n_trades"], "expectancy_r": full["expectancy_r"], "net_pnl": full["net_pnl"], "profit_factor": full["profit_factor"]} if full else {},
                    notes + [f"训练 {wf['train_months']} 月 / 验证 {wf['valid_months']} 月 / 测试 {wf['test_months']} 月，滚动步长 = 测试窗；参数网格 {wf['grid'].get(strategy)}；选参标准 Expectancy(R)，最少 {wf['min_trades_select']} 笔"])


def wf_to_dict(r: WFResult) -> dict:
    return {"strategy": r.strategy, "oos_stats": r.oos_stats, "default_full_stats": r.default_full_stats, "notes": r.notes,
            "folds": [{"fold": f.fold, "train": f.train, "valid": f.valid, "test": f.test, "chosen_params": f.chosen_params, "fallback_to_default": f.fallback_to_default,
                       "train_table": f.train_table, "valid_stats": f.valid_stats, "test_stats": f.test_stats} for f in r.folds]}
