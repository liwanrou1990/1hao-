"""第十一阶段统一比较：V0 买入持有 / 现金、V1 Phase 10 代表策略（引用其结果）、V2 趋势+回调（A）、V3 趋势+突破回踩（B）、V4 A+B 合并、V5 V4+宏观过滤、实验组 C；
× 退出 A/B/C × 风险预算 × 成本 0/15/25 bps 单侧 × 窗口（训练/验证/历史测试[已被查看]/全样本）+ 逐年独立起算。全部参数来自 preregistration.md。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir
from ..holding.data import load_dataset
from ..holding.engine import HoldingBacktester
from ..holding.metrics import summarize
from .engine11 import Config, MarketContext, PriceActionBacktester
from .macro import build_events, save_events

SYMS = ["BTCUSDT", "ETHUSDT"]
COSTS = {0: (0.0, 0.0), 15: (10.0, 5.0), 25: (20.0, 5.0)}
WINDOWS = {"full": ("2018-01-01", None), "train": ("2018-01-01", "2021-12-31"), "valid": ("2022-01-01", "2023-12-31"), "test_seen": ("2024-01-01", None)}
MAIN = {"exit_mode": "B", "risk_pct": 0.01, "max_alloc": 0.70, "dd_limit": 0.30, "cost": 25}
GROUPS = {"V2_trend_pullback": ("A",), "V3_trend_breakout_retest": ("B",), "V4_pa_combined": ("A", "B"), "VC_failed_breakdown_experimental": ("C",)}
P10_ROWS = Path("docs/brando_pa_validation/phase10_holding/phase10_results.json")
P10_REF = ["S1_BTC_SMA200", "S2_ETH_SMA200", "S3_DYN_BTC_ETH", "S7_HOLD90_EXTEND", "B2_modelB_state_machine_daily_repro"]


def out_dir(c: dict | None = None) -> Path:
    d = report_dir(c) / "phase11_pa"; d.mkdir(parents=True, exist_ok=True); return d


def extra_metrics(res) -> dict:
    t = res.trades; nav = res.nav
    closed = t[~t["partial"]] if len(t) else t
    tot = float(closed["net_pnl"].sum()) if len(closed) else 0.0; top5 = float(closed["net_pnl"].sort_values(ascending=False).head(5).sum()) if len(closed) else 0.0
    return {"top5_pnl_usdt": top5, "top5_share_of_closed_pnl": (top5 / tot) if tot else None, "pnl_excluding_top5": tot - top5, "median_hold_days": float(closed["hold_days"].median()) if len(closed) else None,
            "time_flat": float((nav["exposure"] < 0.01).mean()), "avg_exposure": float(nav["exposure"].mean()), "capital_efficiency_profit_per_avg_exposure": (res.final["liquidation_net_profit_usdt"] / max(float(nav["exposure"].mean()), 1e-9)) if len(nav) else None,
            "n_signals_accepted": res.final.get("n_signals_accepted"), "n_signals_rejected": res.final.get("n_signals_rejected"), "partial_exits": int(t["partial"].sum()) if len(t) else 0}


def run_pa(bars, group: str, strategies: tuple, exit_mode: str, risk_pct: float, max_alloc: float, dd_limit, macro_mode: str, cost: int, start: str, end: str, events, ctx: MarketContext | None = None) -> dict:
    fee, slip = COSTS[cost]; c = Config(strategies=strategies, exit_mode=exit_mode, risk_pct=risk_pct, max_alloc=max_alloc, dd_limit=dd_limit, macro_mode=macro_mode, name=group)
    r = PriceActionBacktester(bars, c, fee, slip, start, end, events=events, ctx=ctx).run()
    return {"res": r, "row": summarize(r, bars["BTCUSDT"].set_index(pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True)))["close"]) | extra_metrics(r) | {"group": group, "strategies": "+".join(strategies), "exit_mode": exit_mode, "risk_pct": risk_pct, "max_alloc": max_alloc, "dd_limit": dd_limit, "macro_mode": macro_mode, "cost_bps": cost}}


def run_bh(bars, cost: int, start: str, end: str) -> list[dict]:
    fee, slip = COSTS[cost]; rows = []
    for name, assets in (("V0_BUYHOLD_BTC", ["BTCUSDT"]), ("V0_BUYHOLD_ETH", ["ETHUSDT"]), ("V0_BUYHOLD_5050", SYMS)):
        r = HoldingBacktester(bars, {"name": name, "assets": assets, "mode": "buyhold"}, fee, slip, start, end).run(); r.trades["partial"] = False if len(r.trades) else None
        rows.append(summarize(r, bars["BTCUSDT"].set_index(pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True)))["close"]) | {"group": name, "cost_bps": cost, "top5_share_of_closed_pnl": None, "time_flat": 0.0, "avg_exposure": 1.0, "exit_mode": "-", "risk_pct": None, "max_alloc": 1.0, "dd_limit": None, "macro_mode": "-"})
    rows.append({"name": "V0_CASH_USDT", "group": "V0_CASH_USDT", "cost_bps": cost, "final_equity": 10000.0, "liquidation_usdt": 10000.0, "liquidation_net_profit_usdt": 0.0, "equity_profit_usdt": 0.0, "max_drawdown": 0.0, "longest_drawdown_days": 0, "cagr_liquidation": 0.0, "n_fills": 0, "total_cost_usdt": 0.0, "time_flat": 1.0, "avg_exposure": 0.0, "exit_mode": "-", "risk_pct": None, "max_alloc": 0.0, "dd_limit": None, "macro_mode": "-", "trades": {"n_round_trips": 0}, "annual_returns": {}, "realized_net_usdt": 0.0, "unrealized_usdt": 0.0, "hypothetical_exit_cost_usdt": 0.0, "fees_usdt": 0.0, "slippage_usdt": 0.0, "top5_share_of_closed_pnl": None, "start": start, "end": end})
    return rows


def p10_reference(window: str, cost: int) -> list[dict]:
    if not P10_ROWS.exists():
        return []
    rows = json.loads(P10_ROWS.read_text())["rows"]; wmap = {"test_seen": "oos"}
    out = []
    for r in rows:
        if r["name"] in P10_REF and r["window"] == wmap.get(window, window) and r["cost_bps"] == cost:
            out.append({k: r.get(k) for k in ("name", "final_equity", "liquidation_usdt", "liquidation_net_profit_usdt", "equity_profit_usdt", "max_drawdown", "longest_drawdown_days", "cagr_liquidation", "n_fills", "total_cost_usdt", "fees_usdt", "slippage_usdt", "realized_net_usdt", "unrealized_usdt", "hypothetical_exit_cost_usdt", "time_in_market", "annual_returns", "trades", "regimes", "start", "end")} | {"group": "V1_" + r["name"], "cost_bps": cost, "time_flat": 1 - r["time_in_market"], "avg_exposure": r["time_in_market"], "exit_mode": "-", "risk_pct": None, "max_alloc": 1.0, "dd_limit": None, "macro_mode": "-", "top5_share_of_closed_pnl": None, "source": "Phase 10 phase10_results.json（引用，未重跑；日线复现版）"})
    return out


def run_phase11(c: dict | None = None, out: Path | None = None, dry_run: bool = False, write_curves: bool = True) -> dict:
    c = c or cfg(); out = Path(out or out_dir(c)); out.mkdir(parents=True, exist_ok=True); bars = load_dataset(c); latest = min(str(bars[s]["ts"].iloc[-1].date()) for s in SYMS)
    events = save_events(Path("data_store/brando_pa/phase11_pa/macro_events.csv")); ctx = MarketContext(bars)
    windows = {"dry": ("2023-01-01", "2024-06-30")} if dry_run else {k: (a, b or latest) for k, (a, b) in WINDOWS.items()}
    rows = []; curves = {}; trades_all = []; decisions_last = {}; signals_all = []
    # 主配置全组合：组 × 退出 × 成本 × 窗口（风险/配置/回撤/宏观在全样本 25 bps 上做敏感性）
    main_specs = []
    for g, strat in GROUPS.items():
        for ex in (("B",) if dry_run else ("A", "B", "C")):
            main_specs.append(dict(group=g, strategies=strat, exit_mode=ex, risk_pct=MAIN["risk_pct"], max_alloc=MAIN["max_alloc"], dd_limit=MAIN["dd_limit"], macro_mode="none"))
    for ex in (("B",) if dry_run else ("A", "B", "C")):
        for mm in ("pre_event", "post_event"):
            main_specs.append(dict(group="V5_pa_combined_macro_" + mm, strategies=("A", "B"), exit_mode=ex, risk_pct=MAIN["risk_pct"], max_alloc=MAIN["max_alloc"], dd_limit=MAIN["dd_limit"], macro_mode=mm))
    costs = [25] if dry_run else list(COSTS)
    for w, (a, b) in windows.items():
        for cost in costs:
            rows += [r | {"window": w} for r in run_bh(bars, cost, a, b)]
            rows += [r | {"window": w} for r in p10_reference(w, cost)]
            for sp in main_specs:
                o = run_pa(bars, sp["group"], sp["strategies"], sp["exit_mode"], sp["risk_pct"], sp["max_alloc"], sp["dd_limit"], sp["macro_mode"], cost, a, b, events, ctx); r = o["res"]; row = o["row"] | {"window": w}; rows.append(row)
                key = f"{w}|{cost}|{sp['group']}|exit{sp['exit_mode']}"
                if write_curves and cost == 25 and w in ("full", "test_seen", "dry"):
                    curves[key] = r.nav[["ts", "equity", "liquidation", "exposure", "drawdown"]].assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
                if len(r.trades):
                    trades_all.append(r.trades.assign(group=sp["group"], exit_mode=sp["exit_mode"], window=w, cost_bps=cost))
                if len(r.signals):
                    signals_all.append(r.signals.assign(group=sp["group"], exit_mode=sp["exit_mode"], window=w, cost_bps=cost))
                if w in ("full", "dry") and cost == 25:
                    decisions_last[f"{sp['group']}|exit{sp['exit_mode']}"] = r.decisions.tail(6).assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
    # 风险 / 配置 / 回撤约束敏感性（V4，退出 B，25 bps，全样本或 dry）
    wname = "dry" if dry_run else "full"; a, b = windows[wname]; sens = []
    grid = [(0.005, 0.7, 0.30), (0.01, 0.7, 0.30), (0.01, 0.5, 0.30), (0.01, 1.0, 0.30), (0.01, 0.7, 0.20), (0.01, 0.7, 0.40), (0.01, 0.7, None), (0.005, 0.5, 0.20), (0.01, 1.0, None)]
    for rp, ma, dd in (grid[:3] if dry_run else grid):
        o = run_pa(bars, "V4_pa_combined", ("A", "B"), "B", rp, ma, dd, "none", 25, a, b, events, ctx); sens.append(o["row"] | {"window": wname})
    for ex in ("A", "B", "C"):
        if not dry_run:
            o = run_pa(bars, "V4_pa_combined", ("A", "B"), ex, 0.01, 0.7, None, "none", 25, a, b, events, ctx); sens.append(o["row"] | {"window": wname, "note": "无回撤约束"})
    # 逐年独立起算（V4 退出 B 主配置、25 bps）
    yearly = []
    if not dry_run:
        for y in range(2018, int(latest[:4]) + 1):
            try:
                o = run_pa(bars, "V4_pa_combined", ("A", "B"), "B", 0.01, 0.7, 0.30, "none", 25, f"{y}-01-01", min(f"{y}-12-31", latest), events, ctx); f = o["res"].final
                bhb = HoldingBacktester(bars, {"name": "bh", "assets": ["BTCUSDT"], "mode": "buyhold"}, 20.0, 5.0, f"{y}-01-01", min(f"{y}-12-31", latest)).run().final
                yearly.append({"year": y, "V4_exitB_liq_net": f["liquidation_net_profit_usdt"], "V4_fills": f["n_fills"], "BTC_bh_liq_net": bhb["liquidation_net_profit_usdt"]})
            except AssertionError:
                pass
    corr = float(pd.DataFrame({s: bars[s]["close"].pct_change().to_numpy() for s in SYMS}).dropna().corr().iloc[0, 1])
    res = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；日线收盘决策、次日开盘成交；非前瞻）", "dry_run": dry_run, "latest_complete_day": latest, "windows": windows, "costs_one_side_bps": costs, "cost_note": "全部为单侧 bps；往返 = 2 倍；清仓估值再扣一次卖出成本", "main_config": MAIN,
           "btc_eth_daily_return_corr": corr, "order_flow_coverage": "0（无逐笔数据；仅 K 线量能代理）", "macro_events_note": "FOMC 为已知会议日程；CPI/NFP 为规则近似（approx=True），未逐条核验；日粒度过滤，15/30/60 分钟窗口不可支持",
           "rows": rows, "sensitivity": sens, "yearly_independent": yearly, "decisions_tail": decisions_last, "preregistration": "docs/brando_pa_validation/phase11_pa/preregistration.md", "real_trading": "disabled (no implementation)"}
    atomic_write_json(out / ("phase11_dry_run.json" if dry_run else "phase11_results.json"), res, indent=1, default=str)
    if write_curves:
        atomic_write_json(out / ("curves_dry.json" if dry_run else "curves.json"), curves, default=str)
    if trades_all:
        pd.concat(trades_all, ignore_index=True).to_csv(out / ("trades_dry.csv" if dry_run else "trades.csv"), index=False)
    if signals_all:
        pd.concat(signals_all, ignore_index=True).to_csv(out / ("signals_dry.csv" if dry_run else "signals.csv"), index=False)
    atomic_write_text(out / ("summary_dry.md" if dry_run else "summary.md"), render_summary(res)); return res


def _pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.1f}%"


def render_summary(res: dict) -> str:
    rows = pd.DataFrame(res["rows"]); L = [f"# 第十一阶段比较汇总 · {res['generated_at_utc'][:19]} UTC · {res['category']}", "", f"数据截至 {res['latest_complete_day']}；初始 10,000 USDT；成本为**单侧** bps；主配置 {res['main_config']}；BTC/ETH 日收益相关 {res['btc_eth_daily_return_corr']:.2f}；订单流覆盖 {res['order_flow_coverage']}。{'**DRY-RUN**' if res['dry_run'] else ''}", ""]
    for w in res["windows"]:
        for cost in res["costs_one_side_bps"]:
            sel = rows[(rows.window == w) & (rows.cost_bps == cost)].copy()
            if sel.empty:
                continue
            sel = sel.sort_values("liquidation_net_profit_usdt", ascending=False)
            L += [f"## 窗口 {w}（{res['windows'][w][0]} → {res['windows'][w][1]}）· 单侧 {cost} bps", "", "| 组 | 退出 | 期末净值 | 清仓净收益 | 清仓 CAGR | 最大回撤 | 最长回撤(日) | 成交 | 总成本 | 空仓时间 | 最大5笔占比 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
            for _, r in sel.iterrows():
                L.append(f"| {r['group']} | {r.get('exit_mode', '-')} | {r['final_equity']:,.0f} | {r['liquidation_net_profit_usdt']:+,.0f} | {_pct(r.get('cagr_liquidation'))} | {_pct(r['max_drawdown'])} | {r.get('longest_drawdown_days', '—')} | {r['n_fills']} | {r.get('total_cost_usdt', 0):,.0f} | {_pct(r.get('time_flat'))} | {_pct(r.get('top5_share_of_closed_pnl')) if r.get('top5_share_of_closed_pnl') is not None else '—'} |")
            L.append("")
    if res.get("sensitivity"):
        L += ["## 风险预算 / 配置上限 / 回撤约束 / 退出方式敏感性（V4，25 bps）", "", "| 退出 | 单笔风险 | 最大配置 | 回撤约束 | 清仓净收益 | 最大回撤 | 最长回撤 | 成交 | 空仓时间 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in res["sensitivity"]:
            L.append(f"| {r['exit_mode']} | {r['risk_pct']:.1%} | {r['max_alloc']:.0%} | {r['dd_limit'] if r['dd_limit'] is None else f'{r['dd_limit']:.0%}'} | {r['liquidation_net_profit_usdt']:+,.0f} | {_pct(r['max_drawdown'])} | {r['longest_drawdown_days']} | {r['n_fills']} | {_pct(r['time_flat'])} |")
    return "\n".join(L)
