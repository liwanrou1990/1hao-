"""第二十三阶段运行器：事件统计、完整策略（3 档 × 4 种退出 × 4 档成本）、对照、牛熊与前后半段、排名与事先规定的继续研究标准。只写独立目录。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..pa20 import phase20 as P20
from ..pa20.alloc20 import metrics
from ..pa20.execution20 import attach, final_liquidation_with_impact, liquidity_table
from ..pa22.validate22 import wilson
from . import pullback23 as P

HALF = pd.Timestamp("2021-01-01", tz="UTC")


def ev_stats(e: pd.DataFrame) -> dict:
    out = {"n": int(len(e)), "avg_discount": float(e["discount"].mean()) if len(e) and e["discount"].notna().any() else None}
    for ex in P.EXITS:
        r = e[f"ret_{ex}"].dropna(); m = e[f"mae_{ex}"].dropna(); k = int((r > 0).sum()); lo, hi = wilson(k, len(r))
        out[ex] = {"n": int(len(r)), "win_rate": k / len(r) if len(r) else None, "wilson_lo": lo, "wilson_hi": hi, "mean_ret": float(r.mean()) if len(r) else None, "median_ret": float(r.median()) if len(r) else None,
                   "median_mae": float(m.median()) if len(m) else None, "worst_mae": float(m.min()) if len(m) else None, "median_mae_close": float(e[f"maec_{ex}"].dropna().median()) if len(r) else None,
                   "worst_mae_close": float(e[f"maec_{ex}"].dropna().min()) if len(r) else None, "median_days": float(e[f"days_{ex}"].dropna().median()) if len(r) else None}
    return out


def comparators(ctx) -> dict:
    out = {}
    for start in ("2016-01-01", "2017-01-01"):
        for n in ("BH_BTC", "B1_BTC_SMA200", "ALLOC_25_Y") + (("B2_original",) if start == "2017-01-01" else ()):
            out[f"BTCUSDT|{n}|{start}"] = P20.run_one(ctx, n, 15, start, None)
    eth = P.load("ETHUSDT")[["ts", "open", "high", "low", "close", "volume"]]; liq = {"ETHUSDT": liquidity_table(eth)}
    for n, spec in (("BH_ETH", {"name": "BH_ETH", "assets": ["ETHUSDT"], "mode": "buyhold"}), ("SMA200_ETH", {"name": "SMA200_ETH", "assets": ["ETHUSDT"], "mode": "trend", "risk_exit_dd": None})):
        bt = attach(P20.HoldingBacktester({"ETHUSDT": eth}, spec, 10.0, 5.0, "2017-01-01", None, initial=100_000.0, sma=200), liq); res = bt.run()
        out[f"ETHUSDT|{n}|2017-01-01"] = metrics(res, bt.L, final_liquidation_with_impact(bt.L, {"ETHUSDT": float(res.nav["closes"].iloc[-1]["ETHUSDT"])}, res.nav["ts"].iloc[-1]))
    return out


def run(out_dir: Path | None = None) -> dict:
    out = Path(out_dir or P.OUT_DIR); out.mkdir(parents=True, exist_ok=True); R = {"events": {}, "strategies": {}, "cost_sensitivity": {}, "phases": {}, "forward": {}}; all_ev, all_tr = [], []
    for sym in ("BTCUSDT", "ETHUSDT"):
        f = P.load(sym); sig = P.signals(f, P.START[sym]); ev = P.events(f, sig); ctl = P.control_events(f, P.START[sym])
        lab, piv = P.zigzag_phases(f); ph = dict(zip(f["ts"], lab)); R["phases"][sym] = piv
        for e in (ev, ctl):
            e["phase"] = e["signal_day"].map(ph); e["half"] = np.where(pd.to_datetime(e["signal_day"], utc=True) < HALF, "前半段 至 2020", "后半段 2021 起")
        R["events"][sym] = {}
        for name, e in [(b, ev[ev["bucket"] == b]) for b in P.BUCKETS] + [("对照", ctl)]:
            R["events"][sym][name] = {"all": ev_stats(e), **{f"phase:{p}": ev_stats(e[e["phase"] == p]) for p in ("牛市", "熊市")}, **{f"half:{h}": ev_stats(e[e["half"] == h]) for h in ("前半段 至 2020", "后半段 2021 起")}}
        all_ev.append(ev.assign(symbol=sym)); R["forward"][sym] = {"freeze_day": str(P.FREEZE_DAY.date()), "signals_after_freeze": int((sig["signal"] & (sig["ts"] > P.FREEZE_DAY)).sum()), "latest_data_day": str(f["ts"].iloc[-1].date())}
        R["deep_over_25_confirm_days"] = R.get("deep_over_25_confirm_days", {}) | {sym: int((sig["confirm"] & sig["weekly_ok"] & sig["too_deep"]).sum())}
        starts = [P.START[sym]] + (["2017-01-01"] if sym == "BTCUSDT" else [])
        for st in starts:
            for b in P.BUCKETS:
                for ex in P.EXITS:
                    m, tr = P.strategy(f, sig, sym, b, ex, st); R["strategies"][f"{sym}|{b}|{ex}|{st}"] = m
                    if st == P.START[sym]:
                        all_tr += tr
                        for cn, (fee, slip, imp) in P.COSTS.items():
                            mm, _ = P.strategy(f, sig, sym, b, ex, st, fee, slip, imp); R["cost_sensitivity"][f"{sym}|{b}|{ex}|{cn}"] = {"final_usdt": mm["final_usdt"], "max_drawdown": mm["max_drawdown"], "return_over_mdd": mm["return_over_mdd"]}
    # 事后稳健性：替换单一交易所闪崩影线后的事件统计与 TF 退出策略（不用于主结论）
    R["robust_clean_wicks"] = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        f2 = P.load(sym, clean_wicks=True); sig2 = P.signals(f2, P.START[sym]); ev2 = P.events(f2, sig2)
        R["robust_clean_wicks"][sym] = {b: {"events": ev_stats(ev2[ev2["bucket"] == b]), **{ex: {k: P.strategy(f2, sig2, sym, b, ex, P.START[sym])[0][k] for k in ("final_usdt", "max_drawdown", "return_over_mdd")} for ex in P.EXITS}} for b in P.BUCKETS}
    ctx = P20.Ctx(); R["comparators"] = comparators(ctx)
    # 排名
    rk = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        E = R["events"][sym]; rows = [(b, E[b]["all"]["H90"]) for b in P.BUCKETS]
        r1 = sorted(rows, key=lambda x: -(x[1]["median_mae"] or -9)); r2 = sorted(rows, key=lambda x: -(x[1]["win_rate"] or 0)); r3 = sorted(rows, key=lambda x: -(x[1]["median_ret"] or -9))
        avg = {b: np.mean([[x[0] for x in r].index(b) + 1 for r in (r1, r2, r3)]) for b in P.BUCKETS}
        strat = sorted([(k.split("|")[1] + " · " + k.split("|")[2], v["return_over_mdd"] or -9, v["final_usdt"], v["max_drawdown"]) for k, v in R["strategies"].items() if k.startswith(sym) and k.endswith(P.START[sym])], key=lambda x: -x[1])
        rk[sym] = {"comfort": sorted(avg.items(), key=lambda x: x[1]), "strategy_by_return_over_mdd": strat}
    R["ranking"] = rk
    # 事先规定的继续研究标准
    C = {}
    for b in P.BUCKETS:
        a = {s: (R["events"][s][b]["all"]["H90"]["median_ret"] or -9) - (R["events"][s]["对照"]["all"]["H90"]["median_ret"] or 0) for s in ("BTCUSDT", "ETHUSDT")}
        bb = {"BTCUSDT": (R["strategies"][f"BTCUSDT|{b}|TF|2016-01-01"]["return_over_mdd"] or -9) >= (R["comparators"]["BTCUSDT|B1_BTC_SMA200|2016-01-01"]["return_over_mdd"] or 9),
              "ETHUSDT": (R["strategies"][f"ETHUSDT|{b}|TF|2017-01-01"]["return_over_mdd"] or -9) >= (R["comparators"]["ETHUSDT|SMA200_ETH|2017-01-01"]["return_over_mdd"] or 9)}
        hh = {h: (R["events"]["BTCUSDT"][b][f"half:{h}"]["H90"]["median_ret"] or -9) - (R["events"]["BTCUSDT"]["对照"][f"half:{h}"]["H90"]["median_ret"] or 0) for h in ("前半段 至 2020", "后半段 2021 起")}
        ok = all(v >= 0.03 for v in a.values()) and all(bb.values()) and all(v >= 0.03 for v in hh.values())
        C[b] = {"a_edge_90d_vs_control": a, "b_tf_strategy_beats_sma200_return_over_mdd": bb, "c_btc_halves_edge": hh, "ok": bool(ok)}
    R["continue_research"] = {"per_bucket": C, "verdict": "值得继续研究" if any(v["ok"] for v in C.values()) else "不值得作为独立策略继续，最多作为观察"}
    R |= {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史模拟（2016–2026 全部为已反复研究过的数据，非盲测）", "preregistration": "docs/brando_pa_validation/phase23_pullback/preregistration.md", "real_trading": "disabled (no implementation)"}
    (out / "phase23_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.concat(all_ev).to_csv(out / "events.csv", index=False); pd.DataFrame(all_tr).to_csv(out / "trades.csv", index=False); return R


if __name__ == "__main__":
    r = run(); print(json.dumps({"verdict": r["continue_research"]["verdict"], "comfort": r["ranking"]}, ensure_ascii=False, default=str)[:3000])
