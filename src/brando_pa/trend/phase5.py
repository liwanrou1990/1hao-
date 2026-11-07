"""第五阶段预登记流程：十组对照 × 窗口 × 成本轮次，全部一次性运行并全部报告（无选择）。多进程并行。"""
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
from ..spot.report import plot_equity
from .benchmarks import evaluate, table
from .engine import EXITS, TrendSpotBacktester, sma200_benchmark
from .exposure import analyze, reconstruct
from .regime import TrendRegimeEngine

MODELS_RUN = ["BRANDO_original", "A_trend_following", "B_core_satellite", "C_trend_filtered_brando"]
_CTX: dict = {}


def _init(ctx):
    global _CTX
    _CTX = ctx


def _job(j: dict) -> dict:
    c = j["cfg"]; tf = j["exec_tf"]; ctxs = _CTX[tf]; s = j["symbol"]
    t0 = time.time()
    r = TrendSpotBacktester(j["model"], j["exit"], c, [s], tf, j["start"], j["end"], j["fee"], j["slip"]).run(ctxs)
    out_dir = Path(j["out_dir"]); tag = j["tag"]
    r.equity.to_csv(out_dir / f"{tag}_equity.csv", index=False); r.trades.to_csv(out_dir / f"{tag}_trades.csv", index=False); r.fills.to_csv(out_dir / f"{tag}_fills.csv", index=False); r.decisions.to_csv(out_dir / f"{tag}_decisions.csv", index=False)
    close = ctxs[s].ex.set_index("ts")["close"]
    bh = buy_and_hold(ctxs, [s], j["start"], j["end"], j["initial"], fee_bps=j["fee"])
    expo = analyze(r.equity, r.fills, r.trades, close, s, bh)
    row = evaluate(f"{j['model']}|{j['exit']}", r.equity, r.trades, j["initial"], expo, float(bh["equity"].iloc[-1] / j["initial"] - 1))
    row |= {"counters": r.counters.get(s), "final": r.final, "elapsed_s": round(time.time() - t0, 1), "equity_csv": str(out_dir / f"{tag}_equity.csv")}
    return {"key": tag, "row": row, "equity": r.equity[["ts", "equity"]].to_dict("list") if j.get("keep_curve") else None}


def run_phase5(c: dict | None = None, out_dir: Path | None = None, workers: int | None = None, windows: list[str] | None = None) -> dict:
    c = c or cfg(); tc = c["trend"]
    out_dir = out_dir or (report_dir(c) / "phase5_spot"); out_dir.mkdir(parents=True, exist_ok=True)
    initial = float(tc["initial_cash_usdt"]); syms = tc["symbols"]
    ctx = {"1h": load_contexts(syms, "1h", c), "4h": load_contexts(syms, "4h", c)}
    log = {"initial_cash": initial, "windows": {}, "notes": [], "regime_summary": {}}
    for s in syms:
        d = TrendRegimeEngine(ctx["1h"][s], tc["regime"]).daily
        log["regime_summary"][s] = {"state_counts": d["state"].value_counts().to_dict(), "transitions": int((d["state"] != d["state"].shift()).sum()), "bull_share_by_year": {str(k): float(v) for k, v in d.groupby(d["ts"].dt.year)["state"].apply(lambda x: (x == "BULL").mean()).items()}}
        d.to_csv(out_dir / f"regime_daily_{s}.csv", index=False)
    wins = {k: v for k, v in tc["windows"].items() if (windows is None or k in windows)}
    jobs = []
    for w, (start, end) in wins.items():
        tf = "4h" if w == "full_4h" else "1h"
        for rnd, cost in tc["costs"].items():
            for s in syms:
                for m in MODELS_RUN:
                    for x in EXITS:
                        jobs.append({"cfg": c, "exec_tf": tf, "symbol": s, "model": m, "exit": x, "start": start, "end": end, "fee": float(cost["fee_bps"]), "slip": float(cost["slippage_bps"]), "initial": initial, "out_dir": str(out_dir), "tag": f"{w}_{rnd}_{s}_{m}_{x}", "window": w, "round": rnd})
    workers = workers or max(1, min(os.cpu_count() or 1, 4))
    t0 = time.time(); results = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(ctx,)) as ex:
        for res in ex.map(_job, jobs):
            results[res["key"]] = res["row"]
            r = res["row"]; print(f"[phase5] {res['key']}: ret={r['total_return']:.3f} dd={r['max_drawdown']:.3f} n={r['n_trades']} exp={r.get('avg_exposure')} bull_tim={r.get('bull_time_in_market_share')} ({r['elapsed_s']}s)", flush=True)
    # 组装每个窗口 × 轮次：基准 + 模型
    for w, (start, end) in wins.items():
        tf = "4h" if w == "full_4h" else "1h"; ctxs = ctx[tf]
        log["windows"][w] = {"period": [start, end], "exec_tf": tf}
        for rnd, cost in tc["costs"].items():
            fee, slip = float(cost["fee_bps"]), float(cost["slippage_bps"])
            rows = []; curves = {}
            bh_btc = buy_and_hold(ctxs, ["BTCUSDT"], start, end, initial, fee_bps=fee); bh_eth = buy_and_hold(ctxs, ["ETHUSDT"], start, end, initial, fee_bps=fee); bh_eq = buy_and_hold(ctxs, ["BTCUSDT", "ETHUSDT"], start, end, initial, fee_bps=fee)
            usdt = pd.DataFrame({"ts": bh_btc["ts"], "equity": initial})
            rows.append(evaluate("1_买入持有_BTC", bh_btc, None, initial)); rows.append(evaluate("2_买入持有_ETH", bh_eth, None, initial)); rows.append(evaluate("3_等权_BTC+ETH", bh_eq, None, initial)); rows.append(evaluate("4_全程_USDT", usdt, None, initial))
            curves.update({"BuyHold BTC": bh_btc, "BuyHold ETH": bh_eth, "USDT": usdt})
            per_sym = {}
            for s in syms:
                bh = bh_btc if s == "BTCUSDT" else bh_eth; bh_ret = float(bh["equity"].iloc[-1] / initial - 1)
                f5 = bh.copy(); f5["equity"] = initial * 0.5 + 0.5 * bh["equity"]; f5["exposure"] = 0.5 * bh["equity"] / f5["equity"]
                rows.append(evaluate(f"5_固定50%现货50%USDT_{s[:3]}", f5, None, initial, bench_return=bh_ret))
                eqb, fb = sma200_benchmark(ctxs[s], start, end, initial, int(tc["benchmarks"]["sma200"]["sma"]), fee, slip)
                close = ctxs[s].ex.set_index("ts")["close"]
                fb2 = fb.assign(symbol=s) if not fb.empty else fb
                rows.append(evaluate(f"6_SMA200趋势_{s[:3]}", eqb, None, initial, analyze(eqb.assign(**{f"state_{s}": None}), fb2, None, close, s, bh), bh_ret) | {"n_trades": int((fb["side"] == "BUY").sum()) if not fb.empty else 0})
                if s == "BTCUSDT":
                    curves["SMA200 BTC"] = eqb[["ts", "equity"]]
                for m in MODELS_RUN:
                    for x in EXITS:
                        key = f"{w}_{rnd}_{s}_{m}_{x}"; r = dict(results[key]); r["name"] = f"{'7_原Brando' if m == 'BRANDO_original' else {'A_trend_following': '8_模型A趋势持仓', 'B_core_satellite': '9_模型B核心+战术', 'C_trend_filtered_brando': '10_模型C趋势过滤Brando'}[m]}_{s[:3]}|{x}"
                        rows.append(r)
                        if s == "BTCUSDT" and x == "B_trend_failure" and m != "BRANDO_original":
                            eqc = pd.read_csv(r["equity_csv"]); eqc["ts"] = pd.to_datetime(eqc["ts"], utc=True); curves[f"{m} BTC (trend exit)"] = eqc[["ts", "equity"]]
                        if s == "BTCUSDT" and m == "BRANDO_original" and x == "A_structure_stop":
                            eqc = pd.read_csv(r["equity_csv"]); eqc["ts"] = pd.to_datetime(eqc["ts"], utc=True); curves["Brando original BTC (structure)"] = eqc[["ts", "equity"]]
            chart = plot_equity(curves, out_dir / f"{w}_{rnd}_equity_BTC.png", f"{w} {rnd} ({tf}): BTC models vs benchmarks, start {initial:.0f} USDT") if rnd == "round1" else None
            log["windows"][w][rnd] = {"rows": rows, "chart": chart}
    # 第四阶段“为什么 BTC +73% 时只有 +7.9%”的逐笔还原（用第四阶段 valid 窗口 BTC 产物）
    p4 = report_dir(c) / "phase4_spot"
    try:
        fills = pd.read_csv(p4 / "valid_zero_C_策略_BTC_fills.csv"); trades = pd.read_csv(p4 / "valid_zero_C_策略_BTC_trades.csv"); eq4 = pd.read_csv(p4 / "valid_zero_C_策略_BTC_equity.csv")
        close = ctx["1h"]["BTCUSDT"].ex.set_index("ts")["close"]
        bh = buy_and_hold(ctx["1h"], ["BTCUSDT"], "2024-07-01", "2025-06-30", initial)
        reg = TrendRegimeEngine(ctx["1h"]["BTCUSDT"], tc["regime"]).daily.set_index("ts")["state"]
        eq4["ts"] = pd.to_datetime(eq4["ts"], utc=True); eq4["state_BTCUSDT"] = reg.reindex(eq4["ts"] - pd.Timedelta(days=1), method="ffill").to_numpy()
        log["phase4_btc_valid_reconstruction"] = {"exposure": analyze(eq4, fills, trades, close, "BTCUSDT", bh), "reconstruction": reconstruct(fills, trades, close, "BTCUSDT", "2024-07-01", "2025-06-30"),
                                                  "trades_by_exit": trades["exit_reason"].value_counts().to_dict(), "trades_by_strategy": trades.groupby("strategy")["net_pnl"].agg(["count", "sum"]).to_dict("index")}
    except Exception as e:  # noqa: BLE001
        log["notes"].append(f"第四阶段还原失败：{e}")
    log["elapsed_s"] = round(time.time() - t0, 1)
    atomic_write_json(out_dir / "phase5_results.json", log, indent=2, default=str)
    atomic_write_text(out_dir / "phase5_results.md", to_markdown(log))
    return log


def to_markdown(log: dict) -> str:
    L = [f"# 第五阶段：十组对照（初始 {log['initial_cash']} USDT）", ""]
    for w, d in log["windows"].items():
        for rnd in ("round1", "round2"):
            if rnd not in d:
                continue
            L += [f"## {w} · {rnd}（{'零成本' if rnd == 'round1' else '10 bps + 5 bps'}）· {d['period'][0]} → {d['period'][1] or '最新'} · {d['exec_tf']}", "", table(d[rnd]["rows"]), ""]
    rec = log.get("phase4_btc_valid_reconstruction")
    if rec:
        L += ["## 第四阶段 BTC 验证窗逐笔还原", "", json.dumps(rec["exposure"], ensure_ascii=False, indent=1, default=str), "", json.dumps(rec["reconstruction"]["summary"], ensure_ascii=False, indent=1, default=str), ""]
    return "\n".join(L)


def latest_trend_signal(c: dict | None = None) -> dict:
    c = c or cfg(); tc = c["trend"]; ctxs = load_contexts(tc["symbols"], tc["exec_tf"], c); out = {}
    for s, mc in ctxs.items():
        reg = TrendRegimeEngine(mc, tc["regime"]); r = reg.at(mc.n - 1)
        start = str(mc.ts.iloc[max(1, mc.n - 600)])
        dec = {}
        for m in ("A_trend_following", "C_trend_filtered_brando"):
            res = TrendSpotBacktester(m, "B_trend_failure", c, [s], tc["exec_tf"], start, None).run({s: mc})
            d = res.decisions.iloc[-1].to_dict() if not res.decisions.empty else {}
            dec[m] = {"decision": d.get("decision"), "reasons": d.get("reasons"), "holding_qty_sim": d.get("holding_qty")}
        out[s] = {"ts": str(mc.ts.iloc[-1]), "data_last_close": str(mc.ts.iloc[-1] + pd.Timedelta(seconds=mc.bar_seconds)), "price": float(mc.cl[-1]), "state": r["state"], "sma50": r.get("sma50"), "sma200": r.get("sma200"), "trend_4h": r.get("trend_4h"), "models": dec}
    od = report_dir(c) / "phase5_spot"; od.mkdir(parents=True, exist_ok=True)
    atomic_write_json(od / "latest_trend_signal.json", out, indent=2, default=str)
    return out
