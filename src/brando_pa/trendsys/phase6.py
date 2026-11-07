"""第六阶段预登记流程：策略 {A, B} × 熔断 {原规则, 状态机, 无} × 标的 {BTC, ETH, 等权} × 窗口 × 成本，一次性全部运行并全部报告；
SMA200 基准审计；交易频率分析（含第五阶段模型 B 战术袖子）；预登记验收判据；±1 日敏感性只报告。"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..backtest.runner import load_contexts
from ..config import cfg, report_dir
from ..spot.engine import buy_and_hold
from ..spot.report import equity_stats, plot_equity, utilization, yearly
from ..trend.exposure import analyze
from .audit import audit_symbol
from .engine import TrendSystemBacktester
from .frequency import analyze_sells, phase5_tactical_report, summarize

ASSET_CONFIGS = {"BTC": ["BTCUSDT"], "ETH": ["ETHUSDT"], "EW": ["BTCUSDT", "ETHUSDT"]}
STRATS = ["A_sma200", "B_sma200_confirm3"]
RISKS = ["original_permanent", "state_machine", "none"]
_CTX: dict = {}


def _init(ctx):
    global _CTX
    _CTX = ctx


def _halt_episodes(eq: pd.DataFrame) -> dict:
    """禁买（cap = 0）连续时长：每段天数、最长、合计占比；状态机下每段是否以恢复买入资格结束。"""
    cap0 = (eq["cap"] <= 0).to_numpy(); ts = pd.to_datetime(eq["ts"], utc=True).to_numpy()
    eps = []; start = None
    for k, v in enumerate(cap0):
        if v and start is None:
            start = k
        if (not v) and start is not None:
            eps.append({"from": str(ts[start])[:16], "to": str(ts[k])[:16], "days": float((ts[k] - ts[start]) / np.timedelta64(1, "D")), "ended": True}); start = None
    if start is not None:
        eps.append({"from": str(ts[start])[:16], "to": str(ts[-1])[:16], "days": float((ts[-1] - ts[start]) / np.timedelta64(1, "D")), "ended": False})
    return {"n": len(eps), "longest_days": max([e["days"] for e in eps], default=0.0), "share": float(cap0.mean()), "open_at_end": bool(eps and not eps[-1]["ended"]), "episodes": eps[:20]}


def _row(name: str, r, initial: float, close_ref: pd.Series, bh: pd.DataFrame, sym_for_state: str | None) -> dict:
    eq = r.equity.copy()
    if sym_for_state:
        eq[f"state_{sym_for_state}"] = np.where(eq[f"trend_{sym_for_state}"], "BULL", "OFF")
    expo = analyze(eq, r.fills, r.trades, close_ref, sym_for_state or "EW", bh)
    es = equity_stats(eq, initial); ut = utilization(eq)
    sells = analyze_sells(r.fills, {s: close_ref for s in r.meta["symbols"]}) if len(r.meta["symbols"]) == 1 else pd.DataFrame()
    yrs = max((pd.Timestamp(eq["ts"].iloc[-1]) - pd.Timestamp(eq["ts"].iloc[0])).days, 1) / 365
    st = eq["risk_state"].value_counts(normalize=True).to_dict()
    row = {"name": name, "total_return": es.get("total_return"), "cagr": es.get("cagr"), "max_drawdown": es.get("max_drawdown"), "sharpe": es.get("sharpe_daily"), "calmar": es.get("calmar"), "n_trades": int(r.final["n_sells"]), "n_buys": int((r.fills["side"] == "BUY").sum()) if not r.fills.empty else 0,
           "trades_per_year": float(r.final["n_sells"] / yrs), "avg_exposure": ut.get("avg_exposure"), "time_in_market": ut.get("time_in_market_share"), "yearly": yearly(eq), "excess_vs_bh": es.get("total_return") - float(bh["equity"].iloc[-1] / initial - 1),
           "risk_state_share": {k: round(v, 4) for k, v in st.items()}, "n_halts": int(len([t for t in r.transitions if t["to"] == "EMERGENCY_HALT"])) if r.transitions else int(r.risk_log["halt_count"].max()) if ("halt_count" in r.risk_log and not r.risk_log.empty) else 0,
           "halt_episodes": _halt_episodes(eq), "fees_paid": r.final["fees_paid"], "min_cash": float(eq["cash"].min()), "max_exposure": float(eq["exposure"].max()), "min_qty": float(min(eq[c].min() for c in eq.columns if c.startswith("qty_"))),
           "sell_summary": summarize(sells, yrs) if not sells.empty else {"n_sells": int(r.final["n_sells"])}, "exit_reasons": r.trades["exit_reason"].value_counts().to_dict() if not r.trades.empty else {}, "meta": r.meta}
    row |= {k: expo.get(k) for k in ("bull_time_in_market_share", "up_month_capture", "down_month_capture", "idle_share", "idle_longest_days", "missed_after_sell_sum", "reentries_after_stop", "avg_hold_days", "gap_vs_bh_final", "gap_vs_bh_min")}
    return row


def _job(j: dict) -> dict:
    c = j["cfg"]; ctxs = _CTX[j["exec_tf"]]; syms = j["symbols"]; t0 = time.time()
    r = TrendSystemBacktester(j["strategy"], j["risk"], c, syms, j["exec_tf"], j["start"], j["end"], j["fee"], j["slip"], confirm_days_override=j.get("confirm")).run(ctxs)
    out = Path(j["out_dir"]); tag = j["tag"]
    r.equity.to_csv(out / f"{tag}_equity.csv", index=False); r.fills.to_csv(out / f"{tag}_fills.csv", index=False); r.trades.to_csv(out / f"{tag}_trades.csv", index=False); r.decisions.to_csv(out / f"{tag}_decisions.csv", index=False); r.risk_log.to_csv(out / f"{tag}_risk_log.csv", index=False)
    if r.transitions:
        pd.DataFrame(r.transitions).to_csv(out / f"{tag}_transitions.csv", index=False)
    bh = buy_and_hold(ctxs, syms, j["start"], j["end"], j["initial"], fee_bps=j["fee"])
    if len(syms) == 1:
        close_ref = ctxs[syms[0]].ex.set_index("ts")["close"]; sym = syms[0]
    else:
        close_ref = bh.set_index("ts")["equity"]; sym = None          # 等权：以等权买入持有曲线为“标的”
    row = _row(j["name"], r, j["initial"], close_ref, bh, sym) | {"elapsed_s": round(time.time() - t0, 1), "equity_csv": str(out / f"{tag}_equity.csv")}
    return {"key": tag, "row": row}


def run_phase6(c: dict | None = None, out_dir: Path | None = None, workers: int | None = None, windows: list[str] | None = None) -> dict:
    c = c or cfg(); tc = c["trendsys"]
    out_dir = out_dir or (report_dir(c) / "phase6_spot"); out_dir.mkdir(parents=True, exist_ok=True)
    initial = float(tc["initial_cash_usdt"]); syms = tc["symbols"]
    ctx = {"1h": load_contexts(syms, "1h", c), "4h": load_contexts(syms, "4h", c)}
    t0 = time.time()
    log = {"initial_cash": initial, "preregistration": "docs/brando_pa_validation/phase6_spot/preregistration.md", "windows": {}, "window_labels": tc.get("window_labels", {}), "notes": [],
           "data_end": {tf: {s: str(m.ts.iloc[-1] + pd.Timedelta(seconds=m.bar_seconds)) for s, m in ctx[tf].items()} for tf in ctx}}
    # ---- 1. SMA200 基准审计（full_1h，零成本与含成本）----
    p5 = Path("docs/brando_pa_validation/phase5_spot/phase5_results.json")
    exp = {}
    if p5.exists():
        rows5 = json.loads(p5.read_text())["windows"]["full_1h"]["round1"]["rows"]; exp = {r["name"][-3:]: r["total_return"] for r in rows5 if r["name"].startswith("6_")}
    log["audit"] = {}
    cutoff = tc.get("audit", {}).get("phase5_data_cutoff") or tc["windows"]["full_1h"][1]
    log["audit_window"] = [tc["windows"]["full_1h"][0], cutoff]
    for s in syms:
        a = audit_symbol(ctx["1h"][s], c, tc["windows"]["full_1h"][0], cutoff, initial, expected_return=exp.get(s[:3]))
        a2 = audit_symbol(ctx["1h"][s], c, tc["windows"]["full_1h"][0], cutoff, initial, 10, 5)
        a["fills_phase5"].to_csv(out_dir / f"audit_sma200_fills_{s}.csv", index=False)
        pd.DataFrame(a.get("hold_segments", {}).get("segments", [])).to_csv(out_dir / f"audit_sma200_hold_segments_{s}.csv", index=False)
        log["audit"][s] = {k: v for k, v in a.items() if k not in ("equity_phase5", "fills_phase5", "trendsys_result")} | {"with_cost_10_5": {k: a2[k] for k in ("phase5_benchmark", "independent_reimpl", "trendsys_A_none", "max_abs_rel_diff_phase5_vs_independent", "fees")}}
        if "segments" in log["audit"][s].get("hold_segments", {}):
            log["audit"][s]["hold_segments"] = {k: v for k, v in log["audit"][s]["hold_segments"].items() if k != "segments"}
        print(f"[phase6] audit {s}: phase5 {a['phase5_benchmark']['total_return']:.4f} indep {a['independent_reimpl']['total_return']:.4f} trendsys {a['trendsys_A_none']['total_return']:.4f} reproduced={a.get('reproduced')}", flush=True)
    # ---- 2. 配置矩阵 ----
    wins = {k: v for k, v in tc["windows"].items() if (windows is None or k in windows)}
    jobs = []
    for w, (start, end) in wins.items():
        tf = "4h" if w == "full_4h" else "1h"
        for rnd, cost in tc["costs"].items():
            for cfg_name, ss in ASSET_CONFIGS.items():
                for st in STRATS:
                    for rk in RISKS:
                        jobs.append({"cfg": c, "exec_tf": tf, "symbols": ss, "strategy": st, "risk": rk, "start": start, "end": end, "fee": float(cost["fee_bps"]), "slip": float(cost["slippage_bps"]), "initial": initial, "out_dir": str(out_dir),
                                     "tag": f"{w}_{rnd}_{cfg_name}_{st}_{rk}", "name": f"{cfg_name}|{st}|{rk}", "window": w, "round": rnd, "asset": cfg_name})
    # 敏感性（只报告）：策略 B 的 confirm_days ±1，full_1h 零成本，无熔断
    for cd in tc["trend"].get("sensitivity_confirm_days", []):
        for cfg_name in ("BTC", "ETH"):
            jobs.append({"cfg": c, "exec_tf": "1h", "symbols": ASSET_CONFIGS[cfg_name], "strategy": "B_sma200_confirm3", "risk": "none", "start": tc["windows"]["full_1h"][0], "end": tc["windows"]["full_1h"][1], "fee": 0.0, "slip": 0.0, "initial": initial, "out_dir": str(out_dir),
                         "tag": f"sens_confirm{cd}_{cfg_name}", "name": f"{cfg_name}|B_confirm{cd}|none", "window": "sensitivity", "round": "round1", "asset": cfg_name, "confirm": int(cd)})
    workers = workers or max(1, min(os.cpu_count() or 1, 4)); results = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(ctx,)) as ex:
        for res in ex.map(_job, jobs):
            results[res["key"]] = res["row"]; r = res["row"]
            print(f"[phase6] {res['key']}: ret={r['total_return']:.3f} dd={r['max_drawdown']:.3f} sells={r['n_trades']} exp={r['avg_exposure']:.2f} halts={r['n_halts']} cap0={r['halt_episodes']['share']:.2f} ({r['elapsed_s']}s)", flush=True)
    for w, (start, end) in wins.items():
        tf = "4h" if w == "full_4h" else "1h"; ctxs = ctx[tf]
        log["windows"][w] = {"period": [start, end], "exec_tf": tf, "label": tc.get("window_labels", {}).get(w)}
        for rnd, cost in tc["costs"].items():
            fee = float(cost["fee_bps"]); rows = []; curves = {}
            bh = {k: buy_and_hold(ctxs, ss, start, end, initial, fee_bps=fee) for k, ss in ASSET_CONFIGS.items()}
            usdt = pd.DataFrame({"ts": bh["BTC"]["ts"], "equity": initial})
            for k, nm in (("BTC", "买入持有_BTC"), ("ETH", "买入持有_ETH"), ("EW", "等权买入持有_BTC+ETH")):
                es = equity_stats(bh[k], initial); rows.append({"name": nm, "total_return": es["total_return"], "cagr": es.get("cagr"), "max_drawdown": es["max_drawdown"], "sharpe": es.get("sharpe_daily"), "calmar": es.get("calmar"), "n_trades": 0, "avg_exposure": 1.0, "time_in_market": 1.0, "yearly": yearly(bh[k]), "excess_vs_bh": 0.0, "n_halts": 0, "halt_episodes": {"share": 0.0, "longest_days": 0.0}})
                curves[f"BuyHold {k}"] = bh[k]
            rows.append({"name": "全程_USDT", "total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": None, "calmar": None, "n_trades": 0, "avg_exposure": 0.0, "time_in_market": 0.0, "yearly": {}, "excess_vs_bh": -float(bh["BTC"]["equity"].iloc[-1] / initial - 1), "n_halts": 0, "halt_episodes": {"share": 0.0, "longest_days": 0.0}})
            curves["USDT"] = usdt
            for cfg_name in ASSET_CONFIGS:
                for st in STRATS:
                    for rk in RISKS:
                        key = f"{w}_{rnd}_{cfg_name}_{st}_{rk}"; rows.append(results[key])
                        if cfg_name == "BTC" and rnd == "round1" and (rk != "original_permanent" or st == "A_sma200"):
                            e = pd.read_csv(results[key]["equity_csv"]); e["ts"] = pd.to_datetime(e["ts"], utc=True); curves[f"{st[:1]} {rk}"] = e[["ts", "equity"]]
            chart = plot_equity(curves, out_dir / f"{w}_{rnd}_equity_BTC.png", f"{w} {rnd} ({tf}): BTC trend system A/B x risk modes vs benchmarks, start {initial:.0f} USDT") if rnd == "round1" else None
            log["windows"][w][rnd] = {"rows": rows, "chart": chart}
        # 状态机时间线图（BTC、策略 A、零成本）
        try:
            e = pd.read_csv(results[f"{w}_round1_BTC_A_sma200_state_machine"]["equity_csv"]); e["ts"] = pd.to_datetime(e["ts"], utc=True)
            log["windows"][w]["state_chart"] = plot_states(e, out_dir / f"{w}_state_timeline_BTC_A.png", f"{w}: BTC strategy A with circuit-breaker state machine")
        except Exception as ex_:  # noqa: BLE001
            log["notes"].append(f"状态图失败 {w}: {ex_}")
    log["sensitivity"] = {k: {kk: v[kk] for kk in ("name", "total_return", "max_drawdown", "n_trades", "up_month_capture", "avg_exposure")} for k, v in results.items() if k.startswith("sens_")}
    # ---- 3. 交易频率：第五阶段模型 B 战术袖子 / 原 Brando ----
    close = {s: ctx["1h"][s].ex.set_index("ts")["close"] for s in syms}
    try:
        log["phase5_tactical_frequency"] = phase5_tactical_report(report_dir(c) / "phase5_spot", close)
    except Exception as ex_:  # noqa: BLE001
        log["notes"].append(f"第五阶段战术交易分析失败：{ex_}")
    # ---- 4. 验收判据 ----
    log["acceptance"] = acceptance(log, results, tc)
    log["elapsed_s"] = round(time.time() - t0, 1)
    atomic_write_json(out_dir / "phase6_results.json", log, indent=2, default=str)
    atomic_write_text(out_dir / "phase6_results.md", to_markdown(log))
    return log


def plot_states(e: pd.DataFrame, path: Path, title: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"NORMAL": "#ffffff", "RISK_REDUCED": "#fff3b0", "EMERGENCY_HALT": "#ff8a80", "COOLDOWN": "#ffcdd2", "RECOVERY": "#c8e6c9", "HALTED": "#ffcdd2"}
    fig, ax = plt.subplots(figsize=(13, 5)); ax.plot(e["ts"], e["equity"], color="#1f4e79", lw=1.2, label="equity")
    st = e["risk_state"].to_numpy(); ts = e["ts"].to_numpy(); start = 0
    for k in range(1, len(st) + 1):
        if k == len(st) or st[k] != st[start]:
            if st[start] != "NORMAL":
                ax.axvspan(ts[start], ts[k - 1], color=colors.get(st[start], "#eeeeee"), alpha=0.6, lw=0)
            start = k
    ax2 = ax.twinx(); ax2.plot(e["ts"], e["exposure"], color="#888888", lw=0.6, alpha=0.7, label="exposure"); ax2.set_ylim(0, 1.05); ax2.set_ylabel("exposure")
    ax.set_title(title + "  (yellow=RISK_REDUCED, red=HALT, pink=COOLDOWN, green=RECOVERY)"); ax.set_ylabel("equity USDT"); ax.grid(alpha=0.3); fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    return str(path)


def acceptance(log: dict, results: dict, tc: dict) -> dict:
    """预登记 §4 判据，逐项 pass/fail；不调整。"""
    def g(w, rnd, a, st, rk):
        return results.get(f"{w}_{rnd}_{a}_{st}_{rk}")
    out = {"criteria": {}, "final": {}}
    A = {a: g("full_1h", "round1", a, "A_sma200", "none") for a in ("BTC", "ETH")}; B = {a: g("full_1h", "round1", a, "B_sma200_confirm3", "none") for a in ("BTC", "ETH")}
    if not all(A.values()) or not all(B.values()):
        out["note"] = "缺少 full_1h 结果，无法判定"; return out
    c1 = {a: {"ret_A": A[a]["total_return"], "ret_B": B[a]["total_return"], "dd_A": A[a]["max_drawdown"], "dd_B": B[a]["max_drawdown"], "pass": bool(B[a]["total_return"] >= A[a]["total_return"] and B[a]["max_drawdown"] > A[a]["max_drawdown"])} for a in ("BTC", "ETH")}
    out["criteria"]["1_strategy_B_improves"] = {"detail": c1, "pass": all(v["pass"] for v in c1.values())}
    trend_final = "B_sma200_confirm3" if out["criteria"]["1_strategy_B_improves"]["pass"] else "A_sma200"
    c2 = {}
    for a in ("BTC", "ETH"):
        ii = g("full_1h", "round1", a, trend_final, "state_machine"); i_ = g("full_1h", "round1", a, trend_final, "original_permanent"); iii = g("full_1h", "round1", a, trend_final, "none")
        ep = ii["halt_episodes"]; finite = bool(ep["longest_days"] <= 91 + 1 and not ep["open_at_end"]) if ep["n"] else True
        c2[a] = {"a_finite_halts": finite, "longest_no_buy_days_ii": ep["longest_days"], "longest_no_buy_days_i": i_["halt_episodes"]["longest_days"], "open_at_end_i": i_["halt_episodes"]["open_at_end"], "b_dd_not_worse_than_none": bool(ii["max_drawdown"] >= iii["max_drawdown"]), "dd_ii": ii["max_drawdown"], "dd_iii": iii["max_drawdown"],
                 "c_ret_ge_original": bool(ii["total_return"] >= i_["total_return"]), "ret_ii": ii["total_return"], "ret_i": i_["total_return"], "ret_iii": iii["total_return"]}
        c2[a]["pass"] = bool(finite and c2[a]["b_dd_not_worse_than_none"] and c2[a]["c_ret_ge_original"])
    out["criteria"]["2_breaker_state_machine_acceptable"] = {"detail": c2, "pass": all(v["pass"] for v in c2.values())}
    # 任务硬约束：不得仅因无熔断收益更高而取消保护。受保护的默认 = 趋势层 + 状态机（即便未通过判据也只能标注“未达标”，不能改成无熔断）；
    # 同时报告“仅趋势退出（无熔断）”作为收益最高但无额外保护的变体，供使用者自行权衡。
    cands = {"protected_default": (trend_final, "state_machine"), "trend_exit_only": (trend_final, "none")}
    def c345(st, rk):
        F = {a: g("full_1h", "round1", a, st, rk) for a in ("BTC", "ETH")}
        c3 = {a: {"up_capture": F[a]["up_month_capture"], "up_capture_A": A[a]["up_month_capture"], "pass": bool((F[a]["up_month_capture"] or 0) >= 0.9 * (A[a]["up_month_capture"] or 0))} for a in ("BTC", "ETH")}
        c4 = {a: {"n": F[a]["n_trades"], "n_A": A[a]["n_trades"], "pass": bool(F[a]["n_trades"] <= 2 * max(A[a]["n_trades"], 1))} for a in ("BTC", "ETH")}
        c5 = {}
        for a in ("BTC", "ETH"):
            f2 = g("full_1h", "round2", a, st, rk); a2 = g("full_1h", "round2", a, "A_sma200", "none")
            rank_same = (f2["total_return"] >= a2["total_return"]) == (F[a]["total_return"] >= A[a]["total_return"])
            c5[a] = {"ret_cost": f2["total_return"], "ret_A_cost": a2["total_return"], "beats_usdt": bool(f2["total_return"] > 0), "rank_vs_A_unchanged": bool(rank_same), "pass": bool(f2["total_return"] > 0 and rank_same)}
        return c3, c4, c5, {a: {k: F[a][k] for k in ("total_return", "max_drawdown", "n_trades", "up_month_capture", "avg_exposure")} for a in ("BTC", "ETH")}
    res = {k: c345(*v) for k, v in cands.items()}
    out["criteria"]["3_upside_capture_kept"] = {"detail": {k: v[0] for k, v in res.items()}, "pass": all(x["pass"] for x in res["protected_default"][0].values()), "note": "pass 针对受保护默认（趋势层 + 状态机）"}
    out["criteria"]["4_trade_count_reasonable"] = {"detail": {k: v[1] for k, v in res.items()}, "pass": all(x["pass"] for x in res["protected_default"][1].values()), "note": "pass 针对受保护默认"}
    out["criteria"]["5_value_after_costs"] = {"detail": {k: v[2] for k, v in res.items()}, "pass": all(x["pass"] for x in res["protected_default"][2].values()), "note": "pass 针对受保护默认"}
    c6 = {}
    for a in ("BTC", "ETH"):
        signs = {}
        for w in ("train", "valid", "holdout", "full_4h"):
            ra = g(w, "round1", a, "A_sma200", "none"); rb = g(w, "round1", a, "B_sma200_confirm3", "none")
            if ra and rb:
                signs[w] = {"ret_diff": rb["total_return"] - ra["total_return"], "dd_diff": rb["max_drawdown"] - ra["max_drawdown"]}
        rs = [np.sign(v["ret_diff"]) for v in signs.values()]; ds = [np.sign(v["dd_diff"]) for v in signs.values()]
        c6[a] = {"windows": signs, "ret_sign_consistent": bool(rs and len(set(rs)) == 1), "dd_sign_consistent": bool(ds and len(set(ds)) == 1)}
        c6[a]["pass"] = c6[a]["ret_sign_consistent"] and c6[a]["dd_sign_consistent"]
    out["criteria"]["6_stability_B_minus_A"] = {"detail": c6, "sensitivity": log.get("sensitivity"), "pass": all(v["pass"] for v in c6.values())}
    au = log.get("audit", {})
    c7 = {s: {"truncation_identical": au[s]["lookahead"]["truncation_identical"], "reproduced": au[s].get("reproduced"), "cash_min": au[s]["ledger"]["cash_min"], "qty_min": au[s]["ledger"]["qty_min"], "exposure_max": au[s]["ledger"]["exposure_max"]} for s in au}
    ledger_ok = all(v["min_cash"] >= -1e-6 and v["min_qty"] >= -1e-12 and v["max_exposure"] <= 1 + 1e-6 for v in results.values())
    out["criteria"]["7_no_lookahead_no_new_risk"] = {"detail": c7, "all_runs_ledger_ok": ledger_ok, "pass": bool(ledger_ok and all(v["truncation_identical"] and (v["reproduced"] is not False) for v in c7.values()))}
    sm_pass = out["criteria"]["2_breaker_state_machine_acceptable"]["pass"]
    out["final"] = {"trend_layer": trend_final, "keep_raw_sma200": trend_final == "A_sma200",
                    "risk_layer_default": "state_machine", "risk_layer_default_passed_criteria": bool(sm_pass and out["criteria"]["3_upside_capture_kept"]["pass"]),
                    "highest_return_variant": f"{trend_final} + none（仅趋势退出 + 现货硬约束，无额外熔断）",
                    "summary": {k: v[3] for k, v in res.items()},
                    "statement": ("原始 SMA200（策略 A）仍是最佳趋势层，明确保留；" if trend_final == "A_sma200" else "策略 B 按判据改善，采用；")
                    + ("熔断状态机通过全部判据，作为风险层。" if (sm_pass and out["criteria"]["3_upside_capture_kept"]["pass"]) else
                       "熔断状态机修复了永久停机、降低了最大回撤，但未通过预登记判据（见 2/3 明细）；按任务硬约束不取消保护，它仍是带“未达标”标注的受保护默认，不得宣称改善；“仅趋势退出”变体收益最高但只有趋势退出与现货硬约束，两者全部报告，由使用者权衡。")}
    return out


def _f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


COLS = [("总收益", "total_return", True), ("年化", "cagr", True), ("最大回撤", "max_drawdown", True), ("Sharpe", "sharpe", False), ("Calmar", "calmar", False), ("卖出次数", "n_trades", None), ("平均仓位", "avg_exposure", True), ("持仓时间", "time_in_market", True),
        ("上涨月捕获", "up_month_capture", True), ("下跌月捕获", "down_month_capture", True), ("熔断次数", "n_halts", None), ("禁买时间占比", ("halt_episodes", "share"), True), ("最长禁买天", ("halt_episodes", "longest_days"), False), ("超额 vs B&H", "excess_vs_bh", True)]


def table(rows: list[dict]) -> str:
    L = ["| 组合 | " + " | ".join(c[0] for c in COLS) + " |", "|---|" + "---|" * len(COLS)]
    for r in rows:
        vals = []
        for _, k, pct in COLS:
            v = r.get(k[0], {}).get(k[1]) if isinstance(k, tuple) else r.get(k)
            vals.append(str(v) if pct is None or v is None or isinstance(v, bool) else _f(v, pct))
        L.append(f"| {r['name']} | " + " | ".join(vals) + " |")
    return "\n".join(L)


def to_markdown(log: dict) -> str:
    L = [f"# 第六阶段：三层现货趋势系统（初始 {log['initial_cash']} USDT）", "", "## 验收判据", "", "```json", json.dumps(log.get("acceptance"), ensure_ascii=False, indent=1, default=str), "```", ""]
    for w, d in log["windows"].items():
        for rnd in ("round1", "round2"):
            if rnd not in d:
                continue
            L += [f"## {w}（{d.get('label')}）· {rnd}（{'零成本' if rnd == 'round1' else '10 bps + 5 bps'}）· {d['period'][0]} → {d['period'][1] or '最新'} · {d['exec_tf']}", "", table(d[rnd]["rows"]), ""]
    L += ["## SMA200 基准审计", "", "```json", json.dumps(log.get("audit"), ensure_ascii=False, indent=1, default=str), "```", "", "## 第五阶段战术交易频率", "", "```json", json.dumps(log.get("phase5_tactical_frequency"), ensure_ascii=False, indent=1, default=str), "```", ""]
    return "\n".join(L)


def latest_trendsys_signal(c: dict | None = None) -> dict:
    """最新已收盘日线下策略 A / B 的趋势状态与建议动作（前瞻记录，只读）。"""
    c = c or cfg(); tc = c["trendsys"]; ctxs = load_contexts(tc["symbols"], tc["exec_tf"], c); out = {}
    from .trend_layer import trend_signal
    for s, mc in ctxs.items():
        d1 = mc.bars["1d"]; row = {}
        for st, p in tc["trend"]["strategies"].items():
            t = trend_signal(d1, int(tc["trend"]["sma"]), int(p["confirm_days"])).iloc[-1]
            row[st] = {"on": bool(t["on"]), "above_sma": bool(t["above"]), "signal_day": str(t["ts"])[:10]}
        out[s] = {"data_last_close": str(mc.ts.iloc[-1] + pd.Timedelta(seconds=mc.bar_seconds)), "daily_close": float(d1["close"].iloc[-1]), "sma200": float(d1["close"].rolling(int(tc["trend"]["sma"])).mean().iloc[-1]), "strategies": row,
                  "action_if_flat": {st: ("BUY" if v["on"] else "HOLD_CASH") for st, v in row.items()}, "action_if_holding": {st: ("HOLD" if v["on"] else "SELL") for st, v in row.items()}, "brando_signals_enabled": bool(tc.get("brando_signals", {}).get("enabled", False))}
    od = report_dir(c) / "phase6_spot"; od.mkdir(parents=True, exist_ok=True)
    atomic_write_json(od / "latest_trendsys_signal.json", out, indent=2, default=str)
    return out
