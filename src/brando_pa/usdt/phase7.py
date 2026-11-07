"""第七阶段预登记流程：六个固定参考 + 三模型 × 三风险模式 × 窗口 × 成本，一次性全部运行；USDT 排行榜；月度环境诊断；图表。"""
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
from ..trendsys.engine import TrendSystemBacktester
from .engine import AllocationBacktester, liquidation_value
from .evaluation import regime_months, usdt_row

MODELS = ["A_equal_weight", "B_full_single", "C_cash_reserve"]
RISKS = ["original_permanent", "state_machine", "none"]
_CTX: dict = {}


def _init(ctx):
    global _CTX
    _CTX = ctx


def _job(j):
    c = j["cfg"]; ctxs = _CTX[j["exec_tf"]]; t0 = time.time()
    r = AllocationBacktester(j["model"], j["risk"], c, j["start"], j["end"], j["fee"], j["slip"], exec_tf=j["exec_tf"]).run(ctxs)
    out = Path(j["out_dir"]); tag = j["tag"]
    r.equity.to_csv(out / f"{tag}_equity.csv", index=False); r.fills.to_csv(out / f"{tag}_fills.csv", index=False); r.trades.to_csv(out / f"{tag}_trades.csv", index=False); r.alloc_log.to_csv(out / f"{tag}_alloc_log.csv", index=False); r.risk_log.to_csv(out / f"{tag}_risk_log.csv", index=False)
    if r.transitions:
        pd.DataFrame(r.transitions).to_csv(out / f"{tag}_transitions.csv", index=False)
    return {"key": tag, "equity": r.equity[["ts", "equity"] + [c_ for c_ in r.equity.columns if c_.startswith(("w_", "exposure", "risk_state", "cap"))]], "trades": r.trades, "final": r.final, "meta": r.meta | {"elapsed_s": round(time.time() - t0, 1)}, "n_transitions": len(r.transitions), "n_halts": int(sum(1 for t in r.transitions if t["to"] == "EMERGENCY_HALT")) if r.transitions else int(r.risk_log["halt_count"].max()) if ("halt_count" in r.risk_log and not r.risk_log.empty) else 0}


def _ref_sma(ctxs, sym, tf, start, end, initial, fee, slip, c):
    r = TrendSystemBacktester("A_sma200", "none", c, [sym], tf, start, end, fee, slip, initial=initial).run(ctxs)
    px = float(ctxs[sym].ex.set_index("ts")["close"].loc[pd.to_datetime(r.equity["ts"].iloc[-1], utc=True)])
    cash = r.final["cash"]; q = r.final["holdings"].get(sym, 0.0)
    liq = cash + q * px * (1 - slip / 1e4) * (1 - fee / 1e4)
    final = {"liquidation_usdt": liq, "fees_usdt": r.final["fees_paid"], "slippage_usdt": float((r.fills["qty"] * r.fills["price"]).sum()) * slip / 1e4 if not r.fills.empty else 0.0, "n_sells": r.final["n_sells"], "n_fills": r.final["n_fills"],
             "contrib": {sym: {"total": float(r.equity["equity"].iloc[-1]) - initial}}, "turnover_per_year": float((r.fills["qty"] * r.fills["price"]).sum()) / max(r.equity["equity"].mean(), 1) / max((pd.Timestamp(r.equity["ts"].iloc[-1]) - pd.Timestamp(r.equity["ts"].iloc[0])).days, 1) * 365 if not r.fills.empty else 0.0}
    return r.equity, r.trades, final


def _ref_bh(ctxs, syms, start, end, initial, fee, slip):
    eq = buy_and_hold(ctxs, syms, start, end, initial, fee_bps=fee); eq["exposure"] = 1.0
    liq = float(eq["equity"].iloc[-1]) * (1 - slip / 1e4) * (1 - fee / 1e4)
    final = {"liquidation_usdt": liq, "fees_usdt": initial * fee / 1e4 + float(eq["equity"].iloc[-1]) * fee / 1e4, "slippage_usdt": float(eq["equity"].iloc[-1]) * slip / 1e4, "n_sells": 0, "n_fills": len(syms), "contrib": {s: {"total": None} for s in syms}, "turnover_per_year": None}
    return eq, final


def run_phase7(c: dict | None = None, out_dir: Path | None = None, workers: int | None = None, windows: list[str] | None = None) -> dict:
    c = c or cfg(); uc = c["usdt"]; out_dir = out_dir or (report_dir(c) / "phase7_usdt"); out_dir.mkdir(parents=True, exist_ok=True)
    initial = float(uc["initial_cash_usdt"]); syms = uc["symbols"]; dd_limit = float(uc["risk_constraint"]["max_drawdown"])
    ctx = {"1h": load_contexts(syms, "1h", c), "4h": load_contexts(syms, "4h", c)}
    t0 = time.time()
    log = {"initial_cash": initial, "preregistration": "docs/brando_pa_validation/phase7_usdt/preregistration.md", "risk_constraint": uc["risk_constraint"], "window_labels": uc.get("window_labels", {}), "windows": {}, "notes": [],
           "data_end": {tf: {s: str(m.ts.iloc[-1] + pd.Timedelta(seconds=m.bar_seconds)) for s, m in ctx[tf].items()} for tf in ctx}}
    wins = {k: v for k, v in uc["windows"].items() if (windows is None or k in windows)}
    jobs = [{"cfg": c, "exec_tf": "4h" if w == "full_4h" else "1h", "model": m, "risk": rk, "start": s_, "end": e_, "fee": float(cost["fee_bps"]), "slip": float(cost["slippage_bps"]), "out_dir": str(out_dir), "tag": f"{w}_{rnd}_{m}_{rk}", "window": w, "round": rnd}
            for w, (s_, e_) in wins.items() for rnd, cost in uc["costs"].items() for m in MODELS for rk in RISKS]
    workers = workers or max(1, min(os.cpu_count() or 1, 4)); res = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(ctx,)) as ex:
        for r in ex.map(_job, jobs):
            res[r["key"]] = r; f = r["final"]
            print(f"[phase7] {r['key']}: eq={f['equity']:.0f} liq={f['liquidation_usdt']:.0f} fills={f['n_fills']} halts={r['n_halts']} ({r['meta']['elapsed_s']}s)", flush=True)
    for w, (start, end) in wins.items():
        tf = "4h" if w == "full_4h" else "1h"; ctxs = ctx[tf]; close = {s: ctxs[s].ex.set_index("ts")["close"] for s in syms}
        log["windows"][w] = {"period": [start, end], "exec_tf": tf, "label": uc.get("window_labels", {}).get(w)}
        for rnd, cost in uc["costs"].items():
            fee, slip = float(cost["fee_bps"]), float(cost["slippage_bps"]); rows = []; curves = {}
            eq_ew, f_ew = _ref_bh(ctxs, syms, start, end, initial, fee, slip); ref_ew = eq_ew.assign(ts=pd.to_datetime(eq_ew["ts"], utc=True)).set_index("ts")["equity"]
            eq_b, tr_b, f_b = _ref_sma(ctxs, "BTCUSDT", tf, start, end, initial, fee, slip, c); ref_btc_liq = f_b["liquidation_usdt"]
            eq_e, tr_e, f_e = _ref_sma(ctxs, "ETHUSDT", tf, start, end, initial, fee, slip, c)
            rows.append(usdt_row("参考_BTC_SMA200", eq_b, tr_b, f_b, initial, ref_ew, ref_btc_liq, dd_limit, "reference")); rows.append(usdt_row("参考_ETH_SMA200", eq_e, tr_e, f_e, initial, ref_ew, ref_btc_liq, dd_limit, "reference"))
            for k, ss in (("BTC", ["BTCUSDT"]), ("ETH", ["ETHUSDT"]), ("等权", syms)):
                eqh, fh = _ref_bh(ctxs, ss, start, end, initial, fee, slip); rows.append(usdt_row(f"参考_买入持有_{k}", eqh, None, fh, initial, ref_ew, ref_btc_liq, dd_limit, "reference")); curves[f"BuyHold {k if k != '等权' else 'EW'}"] = eqh[["ts", "equity"]]
            usdt_eq = pd.DataFrame({"ts": eq_b["ts"], "equity": initial, "exposure": 0.0}); rows.append(usdt_row("参考_全程_USDT", usdt_eq, None, {"liquidation_usdt": initial, "n_sells": 0, "n_fills": 0}, initial, ref_ew, ref_btc_liq, dd_limit, "reference"))
            curves["BTC SMA200"] = eq_b[["ts", "equity"]]; curves["ETH SMA200"] = eq_e[["ts", "equity"]]; curves["USDT"] = usdt_eq[["ts", "equity"]]
            for m in MODELS:
                for rk in RISKS:
                    r = res[f"{w}_{rnd}_{m}_{rk}"]
                    row = usdt_row(f"{m}|{rk}", r["equity"], r["trades"], r["final"], initial, ref_ew, ref_btc_liq, dd_limit) | {"n_halts": r["n_halts"], "meta": r["meta"]}
                    rows.append(row)
                    if rk in ("state_machine", "none") and rnd == "round1":
                        curves[f"{m[:1]} {rk}"] = r["equity"][["ts", "equity"]]
            ok = sorted([x for x in rows if x["meets_risk_constraint"]], key=lambda x: -x["liquidation_usdt"]); bad = sorted([x for x in rows if not x["meets_risk_constraint"]], key=lambda x: -x["liquidation_usdt"])
            diag = regime_months(close, {x["name"]: (res[f"{w}_{rnd}_{x['name'].split('|')[0]}_{x['name'].split('|')[1]}"]["equity"] if "|" in x["name"] else None) for x in rows if "|" in x["name"]} | {"参考_BTC_SMA200": eq_b, "参考_ETH_SMA200": eq_e, "参考_等权买入持有": eq_ew}, float(uc.get("regime_month_threshold", 0.03)))
            chart = plot_usdt(curves, out_dir / f"{w}_{rnd}_usdt_equity.png", f"{w} {rnd} ({tf}): USDT equity, start {initial:.0f}") if rnd == "round1" else None
            log["windows"][w][rnd] = {"leaderboard_ok": ok, "leaderboard_fail": bad, "rows": rows, "regime_months": diag, "chart": chart}
        try:
            r = res[f"{w}_round1_A_equal_weight_state_machine"]
            log["windows"][w]["position_chart"] = plot_positions(r["equity"], out_dir / f"{w}_positions_A_state_machine.png", f"{w}: model A + state machine, BTC/ETH/USDT weights")
            log["windows"][w]["contrib_chart"] = plot_contrib({f"{m}|{rk}": res[f"{w}_round1_{m}_{rk}"]["final"]["contrib"] for m in MODELS for rk in RISKS}, out_dir / f"{w}_contrib.png", f"{w} round1: BTC/ETH USDT contribution")
        except Exception as e:  # noqa: BLE001
            log["notes"].append(f"{w} 图表失败：{e}")
    log["elapsed_s"] = round(time.time() - t0, 1)
    atomic_write_json(out_dir / "phase7_results.json", log, indent=2, default=str); atomic_write_text(out_dir / "phase7_results.md", to_markdown(log))
    return log


def plot_usdt(curves, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(13, 6))
    for k, e in curves.items():
        ax.plot(pd.to_datetime(e["ts"], utc=True), e["equity"], lw=1.6 if ("SMA200" in k or "none" in k) else 1.0, label=k, alpha=0.9)
    ax.set_ylabel("USDT"); ax.set_title(title); ax.grid(alpha=0.3); ax.legend(fontsize=8, ncol=2); fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig); return str(path)


def plot_positions(eq, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    e = eq.copy(); t = pd.to_datetime(e["ts"], utc=True)
    fig, ax = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    ax[0].stackplot(t, e["w_BTCUSDT"].clip(0, 1), e["w_ETHUSDT"].clip(0, 1), e["w_USDT"].clip(0, 1), labels=["BTC", "ETH", "USDT"], colors=["#f7931a", "#627eea", "#26a17b"], alpha=0.85)
    ax[0].set_ylim(0, 1); ax[0].set_ylabel("weight"); ax[0].legend(loc="upper left", fontsize=8); ax[0].set_title(title)
    ax[1].plot(t, e["equity"], color="#1f4e79"); ax[1].set_ylabel("USDT"); ax[1].grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig); return str(path)


def plot_contrib(contribs: dict, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names = list(contribs); b = [contribs[n]["BTCUSDT"]["total"] for n in names]; e = [contribs[n]["ETHUSDT"]["total"] for n in names]
    fig, ax = plt.subplots(figsize=(13, 5)); x = np.arange(len(names))
    ax.bar(x - 0.2, b, 0.4, label="BTC", color="#f7931a"); ax.bar(x + 0.2, e, 0.4, label="ETH", color="#627eea")
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=30, ha="right", fontsize=8); ax.set_ylabel("USDT"); ax.set_title(title); ax.legend(); ax.grid(alpha=0.3, axis="y"); fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig); return str(path)


COLS = [("初始USDT", "initial_usdt", "u"), ("期末权益USDT", "final_equity_usdt", "u"), ("期末清仓USDT", "liquidation_usdt", "u"), ("净利润USDT", "net_profit_usdt", "u"), ("可实现利润USDT", "realizable_profit_usdt", "u"), ("总收益率", "total_return", "p"), ("最大回撤", "max_drawdown", "p"), ("最大回撤USDT", "max_drawdown_usdt", "u"),
        ("交易次数", "n_trades", "i"), ("平均持仓比例", "avg_exposure", "p"), ("BTC贡献USDT", "contrib_btc_usdt", "u"), ("ETH贡献USDT", "contrib_eth_usdt", "u"), ("交易成本USDT", "cost_usdt", "u"), ("相对BTC SMA200超额USDT", "excess_vs_btc_sma200_usdt", "u"), ("满足风险约束", "meets_risk_constraint", "b"), ("独立验证", "independently_validated", "b"),
        ("最长空仓天", "longest_all_cash_days", "f"), ("最大连亏", "max_consecutive_losses", "i"), ("上涨月捕获", "up_month_capture", "p"), ("熔断", "n_halts", "i"), ("最长禁买天", "longest_no_buy_days", "f")]


def _fmt(v, k):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if k == "u": return f"{v:,.0f}"
    if k == "p": return f"{v*100:.1f}%"
    if k == "b": return "是" if v else "否"
    if k == "i": return str(int(v))
    return f"{v:.1f}"


def table(rows):
    L = ["| 策略 | " + " | ".join(c[0] for c in COLS) + " |", "|---|" + "---|" * len(COLS)]
    for r in rows:
        L.append(f"| {r['name']} | " + " | ".join(_fmt(r.get(k), kind) for _, k, kind in COLS) + " |")
    return "\n".join(L)


def to_markdown(log):
    rc = log["risk_constraint"]
    L = [f"# 第七阶段：USDT 利润排行榜（初始 {log['initial_cash']:.0f} USDT；风险约束占位：最大回撤 ≤ {rc['max_drawdown']:.0%}，用户批准 = {rc['approved_by_user']}）", ""]
    for w, d in log["windows"].items():
        for rnd in ("round1", "round2"):
            if rnd not in d:
                continue
            L += [f"## {w}（{d.get('label')}）· {rnd}（{'零成本' if rnd == 'round1' else '10 bps + 5 bps'}）· {d['period'][0]} → {d['period'][1] or '最新'} · {d['exec_tf']}", "", "### 满足风险约束（按期末清仓 USDT 排序）", "", table(d[rnd]["leaderboard_ok"]), "", "### 不满足风险约束（单独展示，不隐藏）", "", table(d[rnd]["leaderboard_fail"]) if d[rnd]["leaderboard_fail"] else "（无）", "", "### 月度环境诊断（USDT 盈亏之和）", "", "```json", json.dumps(d[rnd]["regime_months"], ensure_ascii=False, default=str), "```", ""]
    return "\n".join(L)


def latest_allocation(c: dict | None = None) -> dict:
    """最新已收盘日线下三模型的目标比例（前瞻记录）。"""
    c = c or cfg(); uc = c["usdt"]; ctxs = load_contexts(uc["symbols"], uc["exec_tf"], c)
    from ..trendsys.trend_layer import trend_signal
    from .allocator import PortfolioAllocator
    on = {s: bool(trend_signal(mc.bars["1d"], int(uc["trend"]["sma"]), int(uc["trend"]["confirm_days"]))["on"].iloc[-1]) for s, mc in ctxs.items()}
    class _R: max_exposure = 1.0; force_exit = False; state = "NORMAL"
    out = {"data_last_close": {s: str(mc.ts.iloc[-1] + pd.Timedelta(seconds=mc.bar_seconds)) for s, mc in ctxs.items()}, "trend_on": on, "models": {}}
    for m in MODELS:
        w, key = PortfolioAllocator(uc["models"][m], uc["symbols"], uc["allocator"]).model_targets(on); out["models"][m] = {"key": key, "target": w | {"USDT": 1 - sum(w.values())}}
    od = report_dir(c) / "phase7_usdt"; od.mkdir(parents=True, exist_ok=True); atomic_write_json(od / "latest_allocation.json", out, indent=2, default=str)
    return out
