"""第十二阶段实验运行器：A 原 B2 / B 原 SMA200 / C B2+回调加仓 / D B2+突破回踩加仓 / E B2+结构退出 / F B2+回调加仓+结构退出 / G 买入持有
× core_frac {1.0,0.7,0.5} × risk_pct {0.5%,1%,2%} × 成本 {0,15,25} 单侧 × 窗口 + 逐年 + 参数扰动（k / ATR 倍数 / 量比）。全部来自 preregistration.md。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir
from ..holding.data import load_dataset
from ..holding.engine import HoldingBacktester
from ..holding.metrics import regime_labels, summarize
from ..pa11.engine11 import MarketContext
from .engine12 import Cfg12, CompositeBacktester

SYMS = ["BTCUSDT", "ETHUSDT"]
COSTS = {0: (0.0, 0.0), 15: (10.0, 5.0), 25: (20.0, 5.0)}
WINDOWS = {"full": ("2018-01-01", None), "train": ("2018-01-01", "2021-12-31"), "valid": ("2022-01-01", "2023-12-31"), "test_seen": ("2024-01-01", None)}
CORE_FRACS = (1.0, 0.7, 0.5); RISK_PCTS = (0.005, 0.01, 0.02)


def experiments(core_fracs=CORE_FRACS, risk_pcts=RISK_PCTS) -> list[Cfg12]:
    ex = [Cfg12(core="B2", name="A_B2"), Cfg12(core="SMA200_btc", name="B_SMA200_btc"), Cfg12(core="SMA200_dual", name="B_SMA200_dual"),
          Cfg12(core="B2", exit_mode="structure_reduce", name="E_B2_structure_reduce"), Cfg12(core="B2", exit_mode="vol_trail", name="E_B2_vol_trail")]
    for cf in core_fracs:
        if cf >= 1.0:
            continue                                                   # core_frac=1 时无机会预算：C/D/F 退化为 A/E
        for rp in risk_pcts:
            ex += [Cfg12(core="B2", core_frac=cf, pa_add=("A",), risk_pct=rp, name=f"C_B2_pullback_cf{cf}_r{rp}"), Cfg12(core="B2", core_frac=cf, pa_add=("B",), risk_pct=rp, name=f"D_B2_breakout_cf{cf}_r{rp}"),
                   Cfg12(core="B2", core_frac=cf, pa_add=("A",), exit_mode="structure_reduce", risk_pct=rp, name=f"F_B2_pullback_structure_cf{cf}_r{rp}"), Cfg12(core="B2", core_frac=cf, pa_add=("A", "B"), exit_mode="t1_partial", risk_pct=rp, name=f"F2_B2_AB_t1partial_cf{cf}_r{rp}")]
    return ex


def out_dir(c=None) -> Path:
    d = report_dir(c) / "phase12_core_pa"; d.mkdir(parents=True, exist_ok=True); return d


def extra(res, btc_close: pd.Series, bh_btc_nav: pd.DataFrame | None) -> dict:
    nav = res.nav; t = res.trades; closed = t[~t["partial"]] if len(t) else t
    tot = float(closed["net_pnl"].sum()) if len(closed) else 0.0; top5 = float(closed["net_pnl"].sort_values(ascending=False).head(5).sum()) if len(closed) else 0.0
    on = (btc_close.reindex(pd.DatetimeIndex(nav["ts"])) > btc_close.rolling(200).mean().reindex(pd.DatetimeIndex(nav["ts"]))).to_numpy()
    part = float(nav["exposure"].to_numpy()[on].mean()) if on.any() else None
    # 牛市月捕获率 = 策略牛市月盈亏 / BTC 持有牛市月盈亏
    cap = None
    if bh_btc_nav is not None:
        s = nav.set_index("ts")["equity"].resample("1ME").last(); b = bh_btc_nav.set_index("ts")["equity"].resample("1ME").last(); lab = regime_labels(btc_close).reindex(s.index, method="nearest")
        ps = s.diff(); pb = b.diff(); m = lab == "bull"
        cap = float(ps[m].sum() / pb[m].sum()) if pb[m].sum() else None
    return {"top5_pnl_usdt": top5, "top5_share_of_closed_pnl": (top5 / tot) if tot else None, "pnl_excluding_top5": tot - top5, "median_hold_days": float(closed["hold_days"].dropna().median()) if len(closed) and closed["hold_days"].notna().any() else None,
            "time_flat": float((nav["exposure"] < 0.01).mean()), "avg_exposure": float(nav["exposure"].mean()), "avg_core_exposure": float(nav["core_exposure"].mean()) if "core_exposure" in nav else None, "avg_opp_exposure": float(nav["opp_exposure"].mean()) if "opp_exposure" in nav else None,
            "upside_participation_exposure_when_btc_above_sma200": part, "bull_month_capture_vs_btc_bh": cap, "n_opp_adds": res.final.get("n_opp_adds"), "n_core_trades": int((t["book"] == "core").sum()) if len(t) and "book" in t else None, "n_opp_trades": int((t["book"] == "opp").sum()) if len(t) and "book" in t else None}


def run_phase12(c: dict | None = None, out: Path | None = None, dry_run: bool = False, write_curves: bool = True) -> dict:
    c = c or cfg(); rp = c.get("trendsys", {}).get("risk", {}); out = Path(out or out_dir(c)); out.mkdir(parents=True, exist_ok=True); bars = load_dataset(c); latest = min(str(bars[s]["ts"].iloc[-1].date()) for s in SYMS)
    ctx = MarketContext(bars); btc_close = bars["BTCUSDT"].set_index(pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True)))["close"]
    windows = {"dry": ("2023-01-01", "2024-06-30")} if dry_run else {k: (a, b or latest) for k, (a, b) in WINDOWS.items()}
    exps = experiments((1.0, 0.7), (0.01,)) if dry_run else experiments(); costs = [25] if dry_run else list(COSTS)
    rows = []; curves = {}; trades_all = []; adds_all = []; decisions_tail = {}
    for w, (a, b) in windows.items():
        for cost in costs:
            fee, slip = COSTS[cost]; bh = {}
            for name, assets in (("G_BUYHOLD_BTC", ["BTCUSDT"]), ("G_BUYHOLD_ETH", ["ETHUSDT"]), ("G_BUYHOLD_5050", SYMS)):
                r = HoldingBacktester(bars, {"name": name, "assets": assets, "mode": "buyhold"}, fee, slip, a, b).run(); r.trades["partial"] = False if len(r.trades) else None; r.nav["core_exposure"] = r.nav["exposure"]; r.nav["opp_exposure"] = 0.0; bh[name] = r
                rows.append(summarize(r, btc_close) | extra(r, btc_close, None) | {"group": name, "experiment": "G", "window": w, "cost_bps": cost, "core": "-", "core_frac": 1.0, "pa_add": "", "exit_mode": "-", "risk_pct": None})
            rows.append({"name": "G_CASH_USDT", "group": "G_CASH_USDT", "experiment": "G", "window": w, "cost_bps": cost, "final_equity": 10000.0, "liquidation_usdt": 10000.0, "liquidation_net_profit_usdt": 0.0, "equity_profit_usdt": 0.0, "max_drawdown": 0.0, "longest_drawdown_days": 0, "cagr_liquidation": 0.0, "n_fills": 0, "total_cost_usdt": 0.0, "fees_usdt": 0.0, "slippage_usdt": 0.0, "time_flat": 1.0, "avg_exposure": 0.0, "avg_core_exposure": 0.0, "avg_opp_exposure": 0.0, "core": "-", "core_frac": 0.0, "pa_add": "", "exit_mode": "-", "risk_pct": None, "trades": {"n_round_trips": 0}, "annual_returns": {}, "realized_net_usdt": 0.0, "unrealized_usdt": 0.0, "hypothetical_exit_cost_usdt": 0.0, "top5_share_of_closed_pnl": None, "start": a, "end": b})
            for e in exps:
                r = CompositeBacktester(bars, e, fee, slip, a, b, ctx=ctx, risk_params=rp).run()
                row = summarize(r, btc_close) | extra(r, btc_close, bh["G_BUYHOLD_BTC"].nav) | {"group": e.name, "experiment": e.name.split("_")[0], "window": w, "cost_bps": cost, "core": e.core, "core_frac": e.core_frac, "pa_add": "+".join(e.pa_add), "exit_mode": e.exit_mode, "risk_pct": e.risk_pct}
                rows.append(row)
                if write_curves and cost == 25 and w in ("full", "test_seen", "dry") and (e.risk_pct == 0.01):
                    curves[f"{w}|{cost}|{e.name}"] = r.nav[["ts", "equity", "liquidation", "exposure", "core_exposure", "opp_exposure", "drawdown"]].assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
                if len(r.trades):
                    trades_all.append(r.trades.assign(group=e.name, window=w, cost_bps=cost))
                if len(r.adds):
                    adds_all.append(r.adds.assign(group=e.name, window=w, cost_bps=cost))
                if w in ("full", "dry") and cost == 25:
                    decisions_tail[e.name] = r.decisions.tail(4).assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
            if write_curves and cost == 25 and w in ("full", "test_seen", "dry"):
                for name, r in bh.items():
                    curves[f"{w}|{cost}|{name}"] = r.nav[["ts", "equity", "liquidation", "exposure", "drawdown"]].assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
    # 逐年独立起算（主要实验，25 bps）
    yearly = []
    if not dry_run:
        for y in range(2018, int(latest[:4]) + 1):
            for e in [x for x in exps if x.name in ("A_B2", "B_SMA200_btc", "E_B2_structure_reduce") or (x.risk_pct == 0.01 and x.core_frac == 0.7)]:
                try:
                    r = CompositeBacktester(bars, e, 20.0, 5.0, f"{y}-01-01", min(f"{y}-12-31", latest), ctx=ctx, risk_params=rp).run(); yearly.append({"year": y, "group": e.name, "liq_net": r.final["liquidation_net_profit_usdt"], "mdd": float(r.nav.drawdown.min()), "fills": r.final["n_fills"]})
                except AssertionError:
                    pass
    # 参数扰动（PA 侧与退出侧；全样本 25 bps；F 主配置 core_frac 0.7 / risk 1%；SMA200 不动）
    perturb = []
    if not dry_run:
        from ..pa11.signals import PullbackConfirmation
        from ..pa11.structure import DailyStructureDetector
        def run_with(det_res_override, e):
            saved = ctx.det_res
            if det_res_override is not None:
                ctx.det_res = det_res_override
            try:
                r = CompositeBacktester(bars, e, 20.0, 5.0, "2018-01-01", latest, ctx=ctx, risk_params=rp).run()
            finally:
                ctx.det_res = saved
            return r
        for atr_mult in (2.5, 3.0, 3.5):
            e = Cfg12(core="B2", core_frac=0.7, pa_add=("A",), exit_mode="vol_trail", atr_mult=atr_mult, name=f"P_F_voltrail_atr{atr_mult}"); r = run_with(None, e)
            perturb.append({"param": "atr_mult", "value": atr_mult, "group": e.name, "liq_net": r.final["liquidation_net_profit_usdt"], "mdd": float(r.nav.drawdown.min()), "fills": r.final["n_fills"], "adds": r.final["n_opp_adds"]})
        for k in (2, 3, 4):
            det = PullbackConfirmation(); res_k = {}
            for s in SYMS:
                d2 = DailyStructureDetector(ctx.d[s].d, k=k); res_k[s] = {"A": [det.check(d2, ctx.z[s], i, ctx.zones[s][i]) if i >= 30 else {"signal": False, "reason": ""} for i in range(ctx.n)], "B": ctx.det_res[s]["B"], "C": ctx.det_res[s]["C"]}
            e = Cfg12(core="B2", core_frac=0.7, pa_add=("A",), exit_mode="structure_reduce", name=f"P_F_swingk{k}"); r = run_with(res_k, e)
            perturb.append({"param": "swing_k(A detector)", "value": k, "group": e.name, "liq_net": r.final["liquidation_net_profit_usdt"], "mdd": float(r.nav.drawdown.min()), "fills": r.final["n_fills"], "adds": r.final["n_opp_adds"]})
        for vr in (0.6, 0.8, 1.0):
            det = PullbackConfirmation(vol_rel_min=vr); res_v = {}
            for s in SYMS:
                res_v[s] = {"A": [det.check(ctx.d[s], ctx.z[s], i, ctx.zones[s][i]) if i >= 30 else {"signal": False, "reason": ""} for i in range(ctx.n)], "B": ctx.det_res[s]["B"], "C": ctx.det_res[s]["C"]}
            e = Cfg12(core="B2", core_frac=0.7, pa_add=("A",), exit_mode="structure_reduce", name=f"P_F_volrel{vr}"); r = run_with(res_v, e)
            perturb.append({"param": "vol_rel_min(A confirmation)", "value": vr, "group": e.name, "liq_net": r.final["liquidation_net_profit_usdt"], "mdd": float(r.nav.drawdown.min()), "fills": r.final["n_fills"], "adds": r.final["n_opp_adds"]})
    res = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest · 开发性分析（2018–2026 已被多阶段使用，非盲测）；日收盘决策、次日开盘成交", "dry_run": dry_run, "latest_complete_day": latest, "windows": windows, "costs_one_side_bps": costs, "cost_note": "单侧 bps；往返 = 2 倍；清仓估值再扣一次卖出成本",
           "core_fracs": list(CORE_FRACS), "risk_pcts": list(RISK_PCTS), "rows": rows, "yearly_independent": yearly, "perturbation": perturb, "decisions_tail": decisions_tail, "preregistration": "docs/brando_pa_validation/phase12_core_pa/preregistration.md", "real_trading": "disabled (no implementation)"}
    atomic_write_json(out / ("phase12_dry_run.json" if dry_run else "phase12_results.json"), res, indent=1, default=str)
    if write_curves:
        atomic_write_json(out / ("curves_dry.json" if dry_run else "curves.json"), curves, default=str)
    if trades_all:
        pd.concat(trades_all, ignore_index=True).to_csv(out / ("trades_dry.csv" if dry_run else "trades.csv"), index=False)
    if adds_all:
        pd.concat(adds_all, ignore_index=True).to_csv(out / ("adds_dry.csv" if dry_run else "adds.csv"), index=False)
    atomic_write_text(out / ("summary_dry.md" if dry_run else "summary.md"), render_summary(res)); return res


def _pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.1f}%"


def render_summary(res: dict) -> str:
    rows = pd.DataFrame(res["rows"]); L = [f"# 第十二阶段汇总 · {res['generated_at_utc'][:19]} UTC · {res['category']}", "", f"数据截至 {res['latest_complete_day']}；初始 10,000 USDT；成本**单侧** bps。{'**DRY-RUN**' if res['dry_run'] else ''}", ""]
    for w in res["windows"]:
        for cost in res["costs_one_side_bps"]:
            sel = rows[(rows.window == w) & (rows.cost_bps == cost)]
            if sel.empty:
                continue
            sel = sel.sort_values("liquidation_net_profit_usdt", ascending=False)
            L += [f"## 窗口 {w}（{res['windows'][w][0]} → {res['windows'][w][1]}）· 单侧 {cost} bps", "", "| 组 | 期末净值 | 清仓净收益 | 清仓 CAGR | 最大回撤 | 最长回撤(日) | 成交 | 总成本 | 核心暴露 | 机会暴露 | 空仓 | 上涨参与 | 牛市捕获 | 最大5笔占比 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for _, r in sel.iterrows():
                L.append(f"| {r['group']} | {r['final_equity']:,.0f} | {r['liquidation_net_profit_usdt']:+,.0f} | {_pct(r.get('cagr_liquidation'))} | {_pct(r['max_drawdown'])} | {r.get('longest_drawdown_days', '—')} | {r['n_fills']} | {r.get('total_cost_usdt', 0):,.0f} | {_pct(r.get('avg_core_exposure'))} | {_pct(r.get('avg_opp_exposure'))} | {_pct(r.get('time_flat'))} | {_pct(r.get('upside_participation_exposure_when_btc_above_sma200'))} | {_pct(r.get('bull_month_capture_vs_btc_bh'))} | {_pct(r.get('top5_share_of_closed_pnl')) if r.get('top5_share_of_closed_pnl') is not None else '—'} |")
            L.append("")
    if res.get("perturbation"):
        L += ["## 参数扰动（全样本 25 bps）", "", "| 参数 | 值 | 组 | 清仓净收益 | 最大回撤 | 成交 | 加仓 |", "|---|---|---|---|---|---|---|"] + [f"| {p['param']} | {p['value']} | {p['group']} | {p['liq_net'] if p['liq_net'] is None else f'{p['liq_net']:+,.0f}'} | {_pct(p['mdd']) if p['mdd'] is not None else '—'} | {p['fills']} | {p['adds']} |" for p in res["perturbation"]]
    return "\n".join(L)
