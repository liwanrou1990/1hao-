"""第十九阶段运行器（预登记 docs/brando_pa_validation/phase19_longterm/preregistration.md）：
候选买点事件研究（30/90/180/365 天，独立样本 + Wilson 置信区间）、16 个策略 × 窗口 × 成本、基准、指标、判定。只写输出目录。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, cfg
from ..holding.engine import HoldingBacktester
from ..holding.metrics import drawdown_stats, regime_labels, summarize
from ..pa17 import missed17
from ..pa18 import phase18
from . import states19 as S
from .engine19 import EXITS, LongTermBacktester

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase19_longterm"
COSTS19 = {0: (0.0, 0.0), 15: (10.0, 5.0), 25: (20.0, 5.0), 50: (45.0, 5.0)}
WINDOWS19 = {"train_2014_2018": ("2014-01-01", "2018-12-31"), "valid_2019_2021": ("2019-01-01", "2021-12-31"), "recheck_2022_2026": ("2022-01-01", None), "full_2014": ("2014-01-01", None)}
WINDOW_LABEL = {k: "已看过的研究数据（第十至十八阶段反复使用），非盲测" for k in WINDOWS19}
ENTRY_SETS = ("A", "B", "C", "ABC")
HORIZONS = (30, 90, 180, 365)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    p = k / n; den = 1 + z * z / n; ctr = (p + z * z / (2 * n)) / den; half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, ctr - half), min(1.0, ctr + half)


def event_study(cand: pd.DataFrame, f: pd.DataFrame, cost: float = 0.0015) -> dict:
    o, c = f["open"].to_numpy(float), f["close"].to_numpy(float); tf = f["w_trend_failure"].fillna(False).astype(bool).to_numpy(); wk = f["is_week_close"].to_numpy(bool); n = len(f); out = {}
    for typ in ("A", "B", "C", "ALL"):
        sel = cand if typ == "ALL" else cand[cand["type"] == typ]; rows = []
        for r in sel.to_dict("records"):
            e = r["i"] + 1
            if e >= n:
                continue
            ent = o[e]; row = {"date": str(pd.Timestamp(r["ts"]).date()), "type": r["type"], "entry": ent, "rr_net": r["rr_net"]}
            for H in HORIZONS:
                x = e + H; row[f"ret_{H}"] = (c[x] * (1 - cost)) / (ent * (1 + cost)) - 1 if x < n else None
            x = next((k for k in range(e, n) if wk[k] and tf[k]), None); ex = o[x + 1] if x is not None and x + 1 < n else c[-1]
            row["ret_until_trend_failure"] = (ex * (1 - cost)) / (ent * (1 + cost)) - 1; row["days_until_trend_failure"] = (x - e) if x is not None else None
            row["mae_90"] = float(c[e:min(e + 90, n)].min() / ent - 1); rows.append(row)
        stats = {}
        for H in HORIZONS:
            mat = [r for r in rows if r[f"ret_{H}"] is not None]; ind = []; last = None
            for r in mat:
                d0 = pd.Timestamp(r["date"])
                if last is None or (d0 - last).days >= H:
                    ind.append(r); last = d0
            k = sum(1 for r in ind if r[f"ret_{H}"] > 0); lo, hi = wilson(k, len(ind))
            stats[f"{H}d"] = {"n_raw": len(rows), "n_matured": len(mat), "n_independent": len(ind), "win_independent": (k / len(ind)) if ind else None, "wilson95": [lo, hi], "median_ret_independent": float(np.median([r[f"ret_{H}"] for r in ind])) if ind else None,
                              "win_all_matured_overlapping": float(np.mean([r[f"ret_{H}"] > 0 for r in mat])) if mat else None, "coverage": [mat[0]["date"], mat[-1]["date"]] if mat else None,
                              "evidence": "证据不足（独立样本 < 10）" if len(ind) < 10 else ("显著高于一半" if lo and lo > 0.5 else "不显著")}
        tfr = [r["ret_until_trend_failure"] for r in rows]
        stats["until_trend_failure"] = {"n": len(tfr), "win": float(np.mean([x > 0 for x in tfr])) if tfr else None, "median_ret": float(np.median(tfr)) if tfr else None, "median_days": float(np.median([r["days_until_trend_failure"] for r in rows if r["days_until_trend_failure"] is not None])) if rows else None, "note": "持有至周线趋势失效；样本有重叠，不是独立样本"}
        out[typ] = {"stats": stats, "events": rows}
    return out


def _metrics(res, f, bars_btc) -> dict:
    s = summarize(res); nav = res.nav.copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True); liq = nav.set_index("ts")["liquidation"]; expo = nav.set_index("ts")["exposure"]
    px = bars_btc.set_index(pd.to_datetime(bars_btc["ts"], utc=True))["close"].astype(float).reindex(liq.index); br = px.pct_change(); sr = liq.pct_change(); up, dn = br > 0, br < 0
    lab = regime_labels(px).reindex(liq.index, method="ffill"); dd = drawdown_stats(liq)
    eps = missed17.flat_episodes(res, {"BTCUSDT": bars_btc}) if len(res.fills) else []
    opp = {k: float(sum(e["missed_usdt_if_positive"] for e in eps if e["class"] == k)) for k in ("不必要的长期空仓", "短期来回", "合理避险", "保护有效但回补更贵")}
    rg = {}
    m = liq.resample("1ME").last(); pnl = m.diff(); pnl.iloc[0] = m.iloc[0] - liq.iloc[0]; ml = regime_labels(px).reindex(m.index, method="nearest")
    for k in ("bull", "bear", "sideways"):
        rg[k] = float(pnl[ml == k].sum())
    t = res.trades
    return {"net_profit_usdt": s["liquidation_net_profit_usdt"], "final_usdt": s["liquidation_usdt"], "max_drawdown": float((liq / liq.cummax() - 1).min()), "longest_drawdown_days": dd["longest_drawdown_days"],
            "up_capture": float(sr[up].sum() / br[up].sum()) if up.any() else None, "down_capture": float(sr[dn].sum() / br[dn].sum()) if dn.any() else None,
            "avg_exposure_bull": float(expo[lab == "bull"].mean()) if (lab == "bull").any() else None, "avg_exposure_bear": float(expo[lab == "bear"].mean()) if (lab == "bear").any() else None,
            "time_in_market": s["time_in_market"], "opportunity_cost_usdt": opp, "avg_hold_days": float(t["hold_days"].mean()) if len(t) else None, "n_round_trips": int(len(t)),
            "n_fills": s["n_fills"], "total_cost_usdt": s["fees_usdt"] + s["slippage_usdt"], "regime_pnl": rg,
            "win_rate_round_trips": float((t["net_pnl"] > 0).mean()) if len(t) else None}


def run_phase19(c: dict | None = None, out_dir: Path | None = None, windows: dict | None = None, costs: list[int] | None = None, strategies: list[tuple] | None = None, write: bool = True) -> dict:
    c = c or cfg(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    d = S.load_daily(); cand, f, w = S.candidates(d); latest = str(d["ts"].iloc[-1].date()); bars_btc = d[["ts", "open", "high", "low", "close", "volume"]]
    b2bars = phase18.load_bars(); windows = windows or WINDOWS19; costs = costs or list(COSTS19); strategies = strategies or [(e, x) for e in ENTRY_SETS for x in EXITS]
    R = {}; curves = {}; trades_out = []
    for wn, (a, b) in windows.items():
        b = b or latest
        for cost in costs:
            fee, slip = COSTS19[cost]
            base = {"BH_BTC": HoldingBacktester({"BTCUSDT": bars_btc}, {"name": "BH_BTC", "assets": ["BTCUSDT"], "mode": "buyhold"}, fee, slip, a, b).run(),
                    "B1_BTC_SMA200": HoldingBacktester({"BTCUSDT": bars_btc}, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, slip, a, b, sma=200).run()}
            if pd.Timestamp(a) >= pd.Timestamp("2017-01-01"):
                base["B2_original"] = phase18.run_variant(b2bars, "V0_original", cost, a, b, c)[1] if cost in phase18.COSTS18 else None
            for n_, res in base.items():
                if res is None:
                    continue
                R[(n_, wn, cost)] = _metrics(res, f, bars_btc) | {"kind": "baseline"}
                if cost == 15 and wn == "full_2014":
                    curves[n_] = res.nav[["ts", "liquidation"]].assign(ts=lambda x: x["ts"].astype(str)).to_dict("list")
            for e, x in strategies:
                res = LongTermBacktester(f, cand, e, x, fee, slip, a, b).run(); nm = f"{e}_{x}"
                R[(nm, wn, cost)] = _metrics(res, f, bars_btc) | {"kind": "phase19"}
                if cost == 15 and wn == "full_2014":
                    curves[nm] = res.nav[["ts", "liquidation"]].assign(ts=lambda x_: x_["ts"].astype(str)).to_dict("list")
                    if len(res.trades):
                        t = res.trades.copy(); t.insert(0, "strategy", nm); trades_out.append(t)
    J = judge(R) if all((n_, wn, cc) in R for n_ in ("BH_BTC", "B1_BTC_SMA200") for wn in WINDOWS19 for cc in (15, 25)) else {}
    ev = event_study(cand, f)
    resd = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；全部为已看过的研究数据，非盲测）", "latest_complete_day": latest, "windows": {k: (a, b or latest) for k, (a, b) in windows.items()}, "window_labels": WINDOW_LABEL,
            "costs_one_side_bps": costs, "preregistration": "docs/brando_pa_validation/phase19_longterm/preregistration.md", "candidates": cand.drop(columns=["i"]).assign(ts=lambda x: x["ts"].astype(str)).to_dict("records") if len(cand) else [],
            "weekly_state_share_since_2014": w[w["week_end"] >= "2014-01-01"]["state"].value_counts(normalize=True).to_dict(), "event_study": {k: v["stats"] for k, v in ev.items()}, "event_rows": {k: v["events"] for k, v in ev.items() if k != "ALL"},
            "results": {f"{n_}|{wn}|{cc}": m for (n_, wn, cc), m in R.items()}, "verdicts": J, "orderflow_note": S.ORDERFLOW_NOTE, "real_trading": "disabled (no implementation)"}
    if write:
        (out / "phase19_results.json").write_text(json.dumps(resd, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out / "phase19_curves_15bps.json").write_text(json.dumps(curves, ensure_ascii=False, default=str), encoding="utf-8")
        if trades_out:
            t = pd.concat(trades_out, ignore_index=True); t["plan"] = t["plan"].apply(lambda p: json.dumps(p, ensure_ascii=False, default=str)); t.to_csv(out / "phase19_trades_full_15bps.csv", index=False)
    return resd


def judge(R: dict) -> dict:
    out = {}
    for nm in sorted({k[0] for k, v in R.items() if v.get("kind") == "phase19"}):
        checks = {}; ok1 = True
        for wn in ("valid_2019_2021", "recheck_2022_2026"):
            for cc in (15, 25):
                m, bh, b1, b2 = R[(nm, wn, cc)], R[("BH_BTC", wn, cc)], R[("B1_BTC_SMA200", wn, cc)], R.get(("B2_original", wn, cc))
                a_ = m["net_profit_usdt"] > bh["net_profit_usdt"]
                b_ = abs(m["max_drawdown"]) <= 0.6 * abs(bh["max_drawdown"]) and (m["up_capture"] or 0) >= 0.6 and m["net_profit_usdt"] > b1["net_profit_usdt"] and (b2 is None or m["net_profit_usdt"] > b2["net_profit_usdt"])
                checks[f"{wn}|{cc}"] = {"net": m["net_profit_usdt"], "bh": bh["net_profit_usdt"], "b1": b1["net_profit_usdt"], "b2": None if b2 is None else b2["net_profit_usdt"], "mdd": m["max_drawdown"], "bh_mdd": bh["max_drawdown"], "up_capture": m["up_capture"], "beats_bh": a_, "risk_adjusted_ok": b_}
                ok1 = ok1 and (a_ or b_)
        tr = R[(nm, "train_2014_2018", 15)]["net_profit_usdt"] >= R[("B1_BTC_SMA200", "train_2014_2018", 15)]["net_profit_usdt"]
        fm, fb = R[(nm, "full_2014", 15)], R[("BH_BTC", "full_2014", 15)]
        reg = fm["regime_pnl"]["bull"] >= 0.6 * fb["regime_pnl"]["bull"] and fm["regime_pnl"]["bear"] >= 0.5 * fb["regime_pnl"]["bear"]
        enough = fm["n_round_trips"] >= 10; adopt = bool(ok1 and tr and reg and enough)
        why = [k for k, v in (("验证 / 重复检验段未超过买入持有且风险调整条件不满足", not ok1), ("训练段低于原 SMA200", not tr), ("牛熊环境条件不满足", not reg), (f"全样本闭合交易 {fm['n_round_trips']} < 10（证据不足）", not enough)) if v]
        out[nm] = {"checks": checks, "train_ok": bool(tr), "regime_ok": bool(reg), "round_trips_full": fm["n_round_trips"], "adopt_forward_observation": adopt, "verdict": "可进入独立前瞻观察（只记录，不交易）" if adopt else "不采用", "reasons": why}
    return out


if __name__ == "__main__":
    r = run_phase19()
    print(json.dumps({"n_results": len(r["results"]), "adopt": [k for k, v in r["verdicts"].items() if v["adopt_forward_observation"]], "n_candidates": len(r["candidates"])}, ensure_ascii=False))
