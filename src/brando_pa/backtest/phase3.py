"""第三阶段预先登记流程（docs/brando_pa_validation/phase3/preregistration.md）的可复现实现。

步骤（全部 1h，两标的；15m 仅描述性对照）：
  1. 训练窗 2022-04-01→2024-06-30 与验证窗 2024-07-01→2025-06-30：对 {A_breakout_retest, A2_structure_entry, A3_breakout_second_confirm} × 止损模式 {micro, atr_buffer, fixed_structure} 各跑一次（9 次 × 2 窗）。
  2. 对每个策略，按**验证窗** ExpR 选一种止损模式（交易数 < 10 则退回预登记默认：A=atr_buffer，A2/A3=micro）。
  3. 锁定测试只运行一次：L2 半锁定保留集 1h 2025-07-01→2026-11-06；L1 真正未使用集 1h 2022-01-01→2022-03-31（4h 2017-11-01→2018-05-31 仅对原策略 A）。
  4. 压力测试（只在保留集上，不用于选型）：手续费 ×1.67（10 bps）、滑点 10/止损 20 bps、价差 4 bps、资金费 ×3。
  5. 止损距离诊断（§三）与环境 × 方向分解。
产出 reports_out/brando_pa/phase3/ 下的 JSON / CSV / MD / PNG。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, deep_update, report_dir
from ..diagnostics.funnel import funnel_table
from ..diagnostics.stop_distance import run as stop_diag
from .metrics import summarize
from .oos_metrics import full_oos_report
from .runner import load_contexts
from .simulator import Backtester

STRATS = ["A_breakout_retest", "A2_structure_entry", "A3_breakout_second_confirm"]
STOP_MODES = ["micro", "atr_buffer", "fixed_structure"]
DEFAULT_STOP = {"A_breakout_retest": "atr_buffer", "A2_structure_entry": "micro", "A3_breakout_second_confirm": "micro"}
WINDOWS = {"train": ("2022-04-01", "2024-06-30"), "valid": ("2024-07-01", "2025-06-30"), "L2_holdout": ("2025-07-01", None), "L1_locked_1h": ("2022-01-01", "2022-03-31")}
STRESS = {"fee10": {"costs": {"perp": {"taker_fee_bps": 10}}}, "slip10_stop20": {"costs": {"perp": {"slippage_bps": 10}, "stop_slippage_bps": 20}}, "spread4": {"costs": {"perp": {"spread_bps": 4}}}, "funding_x3": {"costs": {"funding": {"assumed_rate_8h": 0.0003}}}}


def _cfg(c: dict, strategy: str, stop_mode: str, extra: dict | None = None) -> dict:
    o = {"strategies": {strategy: {"enabled": True}}, "exits": {"stop_mode": stop_mode}}
    return deep_update(deep_update(json.loads(json.dumps(c)), o), extra or {})


def _run(c: dict, ctxs: dict, exec_tf: str, strategy: str, stop_mode: str, start: str | None, end: str | None, extra: dict | None = None):
    bt = Backtester(_cfg(c, strategy, stop_mode, extra), exec_tf, [strategy], True, None, start, end)
    return bt.run(ctxs)


def _stats(res, eq0: float) -> dict:
    tr = res.trades
    rep = full_oos_report(tr, eq0) if not tr.empty else {"n_trades": 0}
    sl = res.signals_log
    rep["candidates"] = int(len(sl)); rep["accepted"] = int(sl["accepted"].sum()) if len(sl) else 0
    rep["stop_atr_median_all"] = float(sl["stop_distance_atr"].median()) if len(sl) else None
    rep["stop_atr_median_trades"] = float(((tr["entry_price"] - tr["stop_init"]).abs() / tr["entry_price"]).median()) if not tr.empty else None
    rep["first_fail"] = sl["first_fail"].fillna("").replace("", "accepted").value_counts().head(6).to_dict() if len(sl) else {}
    if not tr.empty:
        risk = (tr["entry_price"] - tr["stop_init"]).abs(); los = tr[tr["net_pnl"] <= 0]
        rep["losers_mae_between_0_5_and_1R_share"] = float((((los["mae"] / risk.loc[los.index]) >= 0.5) & ((los["mae"] / risk.loc[los.index]) < 1.0)).mean()) if len(los) else None
        rep["stopped_share"] = float(tr["exit_reason"].isin(["stop", "stop_gap", "stop_same_bar"]).mean())
        rep["by_symbol_regime_direction"] = {f"{k[0]}/{k[1]}/{k[2]}": {"n": int(len(g)), "expectancy_r": float(g["r_multiple"].mean()), "net_pnl": float(g["net_pnl"].sum()), "win_rate": float((g["net_pnl"] > 0).mean())} for k, g in tr.groupby(["symbol", "regime4", "direction"])}
    return rep


def run_phase3(symbols: list[str] | None = None, c: dict | None = None, out_dir: Path | None = None, exec_tf: str = "1h") -> dict:
    c = c or cfg(); symbols = symbols or c["symbols"]
    out_dir = out_dir or (report_dir(c) / "phase3"); out_dir.mkdir(parents=True, exist_ok=True)
    eq0 = float(c["backtest"]["initial_equity"])
    ctxs = load_contexts(symbols, exec_tf, c)
    log = {"exec_tf": exec_tf, "symbols": symbols, "windows": WINDOWS, "train_valid": {}, "selected_stop_mode": {}, "holdout": {}, "locked": {}, "stress": {}, "notes": []}
    t0 = time.time()
    # 1) 训练 / 验证
    for sk in STRATS:
        log["train_valid"][sk] = {}
        for sm in STOP_MODES:
            row = {}
            for w in ("train", "valid"):
                res = _run(c, ctxs, exec_tf, sk, sm, *WINDOWS[w])
                row[w] = _stats(res, eq0)
                res.trades.to_csv(out_dir / f"tv_{sk}_{sm}_{w}_trades.csv", index=False)
                if w == "train":
                    res.signals_log.to_csv(out_dir / f"tv_{sk}_{sm}_{w}_signals_log.csv", index=False)
            log["train_valid"][sk][sm] = row
            print(f"[phase3] {sk} stop={sm}: train n={row['train']['n_trades']} ExpR={row['train'].get('expectancy_r')} | valid n={row['valid']['n_trades']} ExpR={row['valid'].get('expectancy_r')}", flush=True)
        # 2) 选止损模式（验证窗 ExpR；交易数 < 10 → 退回预登记默认）
        cands = {sm: r["valid"] for sm, r in log["train_valid"][sk].items() if r["valid"].get("n_trades", 0) >= 10 and r["valid"].get("expectancy_r") is not None}
        chosen = max(cands, key=lambda k: cands[k]["expectancy_r"]) if cands else DEFAULT_STOP[sk]
        log["selected_stop_mode"][sk] = {"chosen": chosen, "fallback_to_default": not bool(cands), "valid_expectancy_by_mode": {sm: r["valid"].get("expectancy_r") for sm, r in log["train_valid"][sk].items()}}
    # 3) 锁定测试：只跑一次
    for sk in STRATS:
        sm = log["selected_stop_mode"][sk]["chosen"]
        res = _run(c, ctxs, exec_tf, sk, sm, *WINDOWS["L2_holdout"])
        log["holdout"][sk] = _stats(res, eq0) | {"stop_mode": sm}
        res.trades.to_csv(out_dir / f"holdout_{sk}_trades.csv", index=False); res.signals_log.to_csv(out_dir / f"holdout_{sk}_signals_log.csv", index=False)
        pd.DataFrame(res.order_events).to_csv(out_dir / f"holdout_{sk}_order_events.csv", index=False)
        funnel_table(res.signals_log, res.trades, res.cancelled_entries, res.limit_log, res.strategy_counters).to_csv(out_dir / f"holdout_{sk}_funnel.csv", index=False)
        print(f"[phase3] HOLDOUT {sk} stop={sm}: n={log['holdout'][sk]['n_trades']} ExpR={log['holdout'][sk].get('expectancy_r')} PF={log['holdout'][sk].get('profit_factor')}", flush=True)
        resL = _run(c, ctxs, exec_tf, sk, sm, *WINDOWS["L1_locked_1h"])
        log["locked"][sk] = _stats(resL, eq0) | {"stop_mode": sm}
        resL.trades.to_csv(out_dir / f"locked1h_{sk}_trades.csv", index=False)
        # 4) 压力测试（保留集）
        log["stress"][sk] = {}
        for name, extra in STRESS.items():
            rs = _run(c, ctxs, exec_tf, sk, sm, *WINDOWS["L2_holdout"], extra=extra)
            st = _stats(rs, eq0); log["stress"][sk][name] = {k: st.get(k) for k in ("n_trades", "expectancy_r", "profit_factor", "net_return", "max_drawdown", "total_fees", "total_slippage", "total_funding")}
    # 4h 原策略 A 的真正未使用集
    try:
        ctx4 = load_contexts(symbols, "4h", c)
        r4 = _run(c, ctx4, "4h", "A_breakout_retest", log["selected_stop_mode"]["A_breakout_retest"]["chosen"], "2017-11-01", "2018-05-31")
        log["locked"]["A_breakout_retest_4h_2017-11_2018-05"] = _stats(r4, eq0)
        r4.trades.to_csv(out_dir / "locked4h_A_breakout_retest_trades.csv", index=False)
    except Exception as e:  # noqa: BLE001
        log["notes"].append(f"4h 锁定集失败：{e}")
    # 5) 止损诊断（训练窗 A 的 signals_log + trades，默认 atr_buffer）
    try:
        log["stop_diag"] = stop_diag(out_dir / "tv_A_breakout_retest_atr_buffer_train_signals_log.csv", out_dir / "tv_A_breakout_retest_atr_buffer_train_trades.csv", exec_tf, out_dir, 0.5, tag=f"A_{exec_tf}_train")
    except Exception as e:  # noqa: BLE001
        log["notes"].append(f"止损诊断失败：{e}")
    log["elapsed_s"] = round(time.time() - t0, 1)
    atomic_write_json(out_dir / "phase3_results.json", log, indent=2, default=str)
    atomic_write_text(out_dir / "phase3_results.md", to_markdown(log))
    return log


def _f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


def to_markdown(log: dict) -> str:
    L = [f"# 第三阶段预登记流程结果（执行周期 {log['exec_tf']}）", "", f"窗口：{json.dumps(log['windows'], ensure_ascii=False)}", "", "## 训练 / 验证（策略 × 止损模式）", "",
         "| 策略 | 止损模式 | 训练 n/ExpR/PF | 训练 止损ATR中位(候选) | 验证 n/ExpR/PF | 验证 回撤 | 验证 被扫损占比 |", "|---|---|---|---|---|---|---|"]
    for sk, d in log["train_valid"].items():
        for sm, r in d.items():
            tr_, va = r["train"], r["valid"]
            L.append(f"| {sk} | {sm} | {tr_.get('n_trades', 0)} / {_f(tr_.get('expectancy_r'), d=3)} / {_f(tr_.get('profit_factor'))} | {_f(tr_.get('stop_atr_median_all'))} | {va.get('n_trades', 0)} / {_f(va.get('expectancy_r'), d=3)} / {_f(va.get('profit_factor'))} | {_f(va.get('max_drawdown'), True)} | {_f(va.get('stopped_share'), True, 0)} |")
    L += ["", "选定止损模式（验证窗 ExpR，<10 笔退回默认）：" + "；".join(f"{k}: {v['chosen']}{'（默认）' if v['fallback_to_default'] else ''}" for k, v in log["selected_stop_mode"].items()), "",
          "## 锁定测试（只运行一次）", "", "| 集合 | 策略 | 止损 | n | ExpR | 胜率 | PF | 净收益 | 回撤 | 均值R 95%CI | 候选→通过 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, d in (("L2 半锁定保留集 2025-07→", log["holdout"]), ("L1 锁定集", log["locked"])):
        for sk, r in d.items():
            ci = r.get("bootstrap_mean_r_95ci") or {}
            L.append(f"| {name} | {sk} | {r.get('stop_mode', '')} | {r.get('n_trades', 0)} | {_f(r.get('expectancy_r'), d=3)} | {_f(r.get('win_rate'), True, 1)} | {_f(r.get('profit_factor'))} | {_f(r.get('net_return'), True)} | {_f(r.get('max_drawdown'), True)} | [{_f(ci.get('lower'), d=3)}, {_f(ci.get('upper'), d=3)}] | {r.get('candidates')}→{r.get('accepted')} |")
    L += ["", "## 压力测试（保留集，不参与选型）", "", "| 策略 | 场景 | n | ExpR | PF | 净收益 | 回撤 |", "|---|---|---|---|---|---|---|"]
    for sk, d in log["stress"].items():
        for name, r in d.items():
            L.append(f"| {sk} | {name} | {r.get('n_trades')} | {_f(r.get('expectancy_r'), d=3)} | {_f(r.get('profit_factor'))} | {_f(r.get('net_return'), True)} | {_f(r.get('max_drawdown'), True)} |")
    L += ["", "## 保留集：标的 × 环境 × 方向（平均 R）", ""]
    for sk, r in log["holdout"].items():
        g = r.get("by_symbol_regime_direction") or {}
        if g:
            L.append(f"- **{sk}**：" + "；".join(f"{k} {v['n']} 笔 ExpR {_f(v['expectancy_r'], d=2)} 净 {_f(v['net_pnl'])}" for k, v in sorted(g.items())))
    if log.get("stop_diag"):
        sd = log["stop_diag"]
        L += ["", "## 策略 A 止损距离诊断（训练窗）", "", f"- 候选 {sd['n_candidates']}，止损/ATR 中位 {_f(sd['stop_atr_all'].get('median'))}（通过过滤 {_f(sd['stop_atr_accepted'].get('median'))}，因止损距离被拒 {_f(sd['stop_atr_rejected_stop_distance'].get('median'))}）",
              f"- 分解（均值 ATR）：{ {k: round(v, 2) for k, v in sd['decomposition_mean_atr'].items()} }；占比 { {k: round(v, 2) for k, v in sd['decomposition_share'].items()} }；结构点落在区域另一侧占 {_f(sd.get('share_structure_on_zone_far_side'), True, 0)}",
              f"- 时序：突破→回踩中位 {sd['timing'].get('bars_armed_to_retest_median')} 根，回踩→确认中位 {sd['timing'].get('bars_retest_to_confirm_median')} 根", "- 结论：" + "；".join(sd["verdict"]), f"- 图：{sd.get('charts')}"]
    L += ["", f"耗时 {log.get('elapsed_s')} s；备注 {log.get('notes')}", ""]
    return "\n".join(L)
