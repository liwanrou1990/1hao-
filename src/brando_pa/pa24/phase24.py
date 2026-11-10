"""第二十四阶段：第二十三阶段回调买点原样保留，比较 V1 原始满仓 / V2 周线 UPTREND 才买 / V3 固定 25%、50% 仓位（预登记 docs/brando_pa_validation/phase24_sizing/preregistration.md）。
只做历史模拟；全部为已反复研究过的历史样本。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..pa20 import phase20 as P20
from ..pa20.alloc20 import metrics
from ..pa20.execution20 import attach, final_liquidation_with_impact, liquidity_table
from ..pa23 import pullback23 as P

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase24_sizing"
VARIANTS = {"V1 原始满仓": ("v1", 1.0), "V2 周线 UPTREND 才买": ("v2", 1.0), "V3a 固定 25%": ("v1", 0.25), "V3b 固定 50%": ("v1", 0.50)}
EXITS = ("TF", "H90", "H180")


def bear_segments(f: pd.DataFrame) -> list[tuple]:
    lab, _ = P.zigzag_phases(f); ts = f["ts"]; out = []; cur = None
    for t, l in zip(ts, lab):
        if l == "熊市" and cur is None:
            cur = t
        elif l != "熊市" and cur is not None:
            out.append((cur, prev)); cur = None
        prev = t
    if cur is not None:
        out.append((cur, prev))
    return out


def bear_perf(nav: pd.DataFrame, segs: list[tuple]) -> list[dict]:
    s = nav.set_index(pd.to_datetime(nav["ts"], utc=True))["liquidation"].astype(float); out = []
    for a, b in segs:
        x = s[(s.index >= a) & (s.index <= b)]
        if len(x) < 20:
            continue
        out.append({"from": str(a.date()), "to": str(b.date()), "return": float(x.iloc[-1] / x.iloc[0] - 1), "max_drawdown": float((x / x.cummax() - 1).min())})
    return out


def winner_dependence(trades: list[dict], final: float, initial: float, w: float) -> dict:
    closed = [t for t in trades if t.get("exit_day")]; fac = [1 + w * t["ret"] for t in closed]; prod = float(np.prod(fac)) if fac else 1.0
    rets = [t["ret"] for t in closed]; open_ = [t for t in trades if not t.get("exit_day")]
    if open_:
        fo = final / (initial * prod); rets.append((fo - 1) / w); fac.append(fo)
    order = np.argsort(rets)[::-1]; out = {"n_trades": len(rets), "top_trades": [{"ret": float(rets[k]), "entry_day": (closed + open_)[k]["entry_day"]} for k in order[:3]]}
    for k in (1, 3):
        keep = [fac[j] for j in range(len(fac)) if j not in set(order[:k])]; out[f"final_share_without_top{k}"] = float(np.prod(keep) / np.prod(fac)) if fac else None
    return out


def comparators_2019(ctx) -> dict:
    out = {f"BTCUSDT|{n}": P20.run_one(ctx, n, 15, "2019-01-01", None) for n in ("BH_BTC", "B1_BTC_SMA200", "ALLOC_25_Y")}
    eth = P.load("ETHUSDT")[["ts", "open", "high", "low", "close", "volume"]]; liq = {"ETHUSDT": liquidity_table(eth)}
    for n, spec in (("BH_ETH", {"name": "BH_ETH", "assets": ["ETHUSDT"], "mode": "buyhold"}), ("SMA200_ETH", {"name": "SMA200_ETH", "assets": ["ETHUSDT"], "mode": "trend", "risk_exit_dd": None})):
        bt = attach(P20.HoldingBacktester({"ETHUSDT": eth}, spec, 10.0, 5.0, "2019-01-01", None, initial=100_000.0, sma=200), liq); res = bt.run()
        out[f"ETHUSDT|{n}"] = metrics(res, bt.L, final_liquidation_with_impact(bt.L, {"ETHUSDT": float(res.nav["closes"].iloc[-1]["ETHUSDT"])}, res.nav["ts"].iloc[-1]))
    return out


def run(out_dir: Path | None = None) -> dict:
    out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); R = {"strategies": {}, "events": {}, "bear_segments": {}}; rows = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        f = P.load(sym); sig1 = P.signals(f, P.START[sym]); st = f["w_state"].to_numpy(); sig2 = sig1.copy(); sig2["signal"] = sig1["signal"] & (st[sig1["i"].to_numpy()] == "UPTREND"); S = {"v1": sig1, "v2": sig2}
        segs = bear_segments(f); R["bear_segments"][sym] = [(str(a.date()), str(b.date())) for a, b in segs]
        ev = {k: P.events(f, s) for k, s in S.items()}
        for b in P.BUCKETS:
            for vn, (sk, w) in VARIANTS.items():
                e = ev[sk][ev[sk]["bucket"] == b]; key = f"{sym}|{b}|{vn}"
                R["events"][key] = {"n": int(len(e)), **{h: {"median": float((w * e[f"ret_{h}"]).median()) if len(e) else None, "win_rate": float((e[f"ret_{h}"] > 0).mean()) if len(e) else None, "median_mae": float((w * e[f"mae_{h}"]).median()) if len(e) else None} for h in ("H90", "H180")}}
                for start in (P.START[sym], "2019-01-01"):
                    for ex in EXITS:
                        m, tr, nav = P.strategy(f, S[sk], sym, b, ex, start, weight=w, return_nav=True)
                        r = {k: m[k] for k in ("final_usdt", "cagr", "max_drawdown", "return_over_mdd", "n_trades", "time_in_market", "avg_btc_exposure")} | {"bear": bear_perf(nav, segs), "winners": winner_dependence(tr, m["final_usdt"], 100_000.0, w)}
                        r["worst_bear_return"] = min([x["return"] for x in r["bear"]], default=None); R["strategies"][f"{key}|{ex}|{start}"] = r
                        rows += [t | {"variant": vn, "start": start} for t in tr]
    p23 = json.loads((PROJECT_ROOT / "reports_out/brando_pa/phase23_pullback/phase23_results.json").read_text(encoding="utf-8"))["comparators"]
    R["comparators"] = {k: {kk: p23[k][kk] for kk in ("final_usdt", "cagr", "max_drawdown", "return_over_mdd")} for k in p23} | {f"{k}|2019-01-01": {kk: v[kk] for kk in ("final_usdt", "cagr", "max_drawdown", "return_over_mdd")} for k, v in comparators_2019(P20.Ctx()).items()}
    # 事先规定的判定（主退出 TF；同档同退出相对 V1）
    J = {}
    for vn in list(VARIANTS)[1:]:
        for b in P.BUCKETS:
            res = {}
            for sym in ("BTCUSDT", "ETHUSDT"):
                for start in (P.START[sym], "2019-01-01"):
                    v = R["strategies"][f"{sym}|{b}|{vn}|TF|{start}"]; o = R["strategies"][f"{sym}|{b}|V1 原始满仓|TF|{start}"]
                    a25 = R["comparators"].get(f"{sym}|ALLOC_25_Y|{start}" if start == "2019-01-01" else f"{sym}|ALLOC_25_Y|2016-01-01")
                    res[f"{sym}|{start}"] = {"mdd_improve_pp": (v["max_drawdown"] - o["max_drawdown"]) * 100, "cagr_ratio": (v["cagr"] / o["cagr"]) if o["cagr"] > 0 else None, "rom_v": v["return_over_mdd"], "rom_v1": o["return_over_mdd"],
                                             "ok": bool(v["max_drawdown"] - o["max_drawdown"] >= 0.10 and o["cagr"] > 0 and v["cagr"] >= 2 / 3 * o["cagr"] and (v["return_over_mdd"] or -9) >= (o["return_over_mdd"] or 9)),
                                             "within_50pct_budget": v["max_drawdown"] >= -0.50, "beats_25pct_annual_rom": None if a25 is None or sym == "ETHUSDT" else (v["return_over_mdd"] or -9) > a25["return_over_mdd"]}
            J[f"{vn}|{b}"] = {"checks": res, "ok": all(x["ok"] for x in res.values())}
    R["judgement"] = J; R["verdict"] = "存在改善回撤且不严重牺牲净收益的方案" if any(v["ok"] for v in J.values()) else "不存在（按事先规定的标准）"
    R |= {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史模拟（全部为已反复研究过的历史样本，非盲测）", "real_trading": "disabled (no implementation)"}
    (out / "phase24_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8"); pd.DataFrame(rows).to_csv(out / "trades.csv", index=False); return R


if __name__ == "__main__":
    r = run(); print(r["verdict"])
