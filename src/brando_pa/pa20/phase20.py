"""第二十阶段运行器（预登记 docs/brando_pa_validation/phase20_capital100k/preregistration.md）。
100,000 USDT 研究本金；固定配置 × 维护方式；对照策略；窗口、滚动起点、压力区间；真实重算 vs 简单 ×10；判定。只写独立输出目录。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, cfg
from ..holding.engine import HoldingBacktester
from ..pa18 import phase18
from ..pa19 import states19 as S
from ..pa19.engine19 import LongTermBacktester
from . import RESEARCH_INITIAL_USDT
from .alloc20 import MODES, WEIGHTS, AllocBacktester, metrics
from .execution20 import attach, final_liquidation_with_impact, liquidity_table

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase20_capital100k"
COSTS20 = {15: (10.0, 5.0), 25: (20.0, 5.0)}
WINDOWS20 = {"full_2014": ("2014-01-01", None), "since_2017": ("2017-01-01", None), "train_2014_2018": ("2014-01-01", "2018-12-31"), "valid_2019_2021": ("2019-01-01", "2021-12-31"), "recheck_2022_2026": ("2022-01-01", None)}
P19 = (("A", "X4"), ("ABC", "X2"), ("A", "X2"))


def stress_windows(d: pd.DataFrame) -> dict:
    c = d.set_index("ts")["close"]; y25 = c["2025-01-01":"2025-12-31"]; pk = y25.idxmax(); tr = c[pk:].idxmin()
    return {"S1_2014_bear": ("2014-01-01", "2015-01-14"), "S2_2018_bear": ("2017-12-17", "2018-12-15"), "S3_2020_covid": ("2020-02-13", "2020-03-12"), "S4_2022_bear": ("2021-11-10", "2022-11-21"), "S5_2025_2026": (str(pk.date()), str(tr.date()))}


class Ctx:
    def __init__(self, c=None, initial: float = RESEARCH_INITIAL_USDT):
        self.c = c or cfg(); self.initial = float(initial); self.d = S.load_daily(); self.cand, self.f, _ = S.candidates(self.d)
        self.btc = self.d[["ts", "open", "high", "low", "close", "volume"]].copy(); self.b2bars = phase18.load_bars()
        self.liq_btc = {"BTCUSDT": liquidity_table(self.btc)}
        bb = {k: v for k, v in self.b2bars.items() if not k.startswith("_")}; self.liq_b2 = {s: liquidity_table(v) for s, v in bb.items()}; self.latest = str(self.d["ts"].iloc[-1].date())


def run_one(ctx: Ctx, name: str, cost: int, a: str, b: str | None, initial: float | None = None) -> dict | None:
    fee, slip = COSTS20[cost]; init = ctx.initial if initial is None else initial; b = b or ctx.latest
    if name.startswith("ALLOC_"):
        _, w, m = name.split("_"); bt = AllocBacktester(ctx.btc, int(w) / 100, m, fee, slip, a, b, init, ctx.liq_btc); res = bt.run()
        return metrics(res, bt.L, res.final["liquidation_usdt"]) | {"name": name, "kind": "allocation"}
    if name == "B2_original":
        if pd.Timestamp(a) < pd.Timestamp("2017-01-01"):
            return None
        bt, res = phase18.run_variant(ctx.b2bars, "V0_original", cost, a, b, ctx.c, initial=init, hook=lambda x: attach(x, ctx.liq_b2))
        px = {s: float(res.nav["closes"].iloc[-1][s]) for s in ("BTCUSDT", "ETHUSDT")}
        return metrics(res, bt.L, final_liquidation_with_impact(bt.L, px, res.nav["ts"].iloc[-1])) | {"name": name, "kind": "baseline"}
    if name in ("BH_BTC", "B1_BTC_SMA200"):
        spec = {"name": name, "assets": ["BTCUSDT"], "mode": "buyhold"} if name == "BH_BTC" else {"name": name, "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}
        bt = attach(HoldingBacktester({"BTCUSDT": ctx.btc}, spec, fee, slip, a, b, initial=init, sma=200), ctx.liq_btc); res = bt.run()
    else:
        e, x = name.split("_"); bt = attach(LongTermBacktester(ctx.f, ctx.cand, e, x, fee, slip, a, b, initial=init), ctx.liq_btc); res = bt.run()
    px = {"BTCUSDT": float(res.nav["closes"].iloc[-1]["BTCUSDT"])}
    return metrics(res, bt.L, final_liquidation_with_impact(bt.L, px, res.nav["ts"].iloc[-1])) | {"name": name, "kind": "baseline" if name in ("BH_BTC", "B1_BTC_SMA200") else "phase19_not_adopted"}


def all_names() -> list[str]:
    return [f"ALLOC_{int(w * 100)}_{m}" for w in WEIGHTS for m in MODES if not (w in (0.0, 1.0) and m != "N")] + ["BH_BTC", "B1_BTC_SMA200", "B2_original"] + [f"{e}_{x}" for e, x in P19]


def rolling_starts(ctx: Ctx, names: list[str], years: int = 4, cost: int = 15) -> dict:
    last = pd.Timestamp(ctx.latest, tz="UTC"); starts = [t for t in pd.date_range("2014-01-01", last - pd.DateOffset(years=years), freq="QS", tz="UTC")]; out = {n: [] for n in names}
    for t in starts:
        a, b = str(t.date()), str((t + pd.DateOffset(years=years) - pd.Timedelta(days=1)).date())
        for n in names:
            m = run_one(ctx, n, cost, a, b)
            if m is not None:
                out[n].append({"start": a, "end": b, "final_usdt": m["final_usdt"], "max_drawdown": m["max_drawdown"]})
    return {"years": years, "n_starts": len(starts), "runs": out}


def judge(R: dict, roll: dict, stress: dict) -> dict:
    allocs = sorted({k[0] for k in R if k[0].startswith("ALLOC_")}); out = {}
    bh_roll = {r["start"]: r["final_usdt"] for r in roll["runs"]["BH_BTC"]}
    for n in allocs + ["B1_BTC_SMA200", "B2_original"]:
        full = R.get((n, "full_2014", 15)) or R.get((n, "since_2017", 15))
        if full is None:
            continue
        st_ok = all((stress[s].get(n) or {"max_drawdown": 0})["max_drawdown"] >= -0.50 for s in stress)
        rr = roll["runs"].get(n, []); roll_ok = all(r["max_drawdown"] >= -0.50 for r in rr)
        within = bool(full["max_drawdown"] >= -0.50 and st_ok and roll_ok)
        beats = []
        for wn in ("train_2014_2018", "valid_2019_2021", "recheck_2022_2026"):
            ms = [R.get((n, wn, cc)) for cc in (15, 25)]; bhs = [R.get(("BH_BTC", wn, cc)) for cc in (15, 25)]
            if all(ms) and all(bhs):
                beats.append(all(m["final_usdt"] >= bh["final_usdt"] for m, bh in zip(ms, bhs)))
        share = float(np.mean([r["final_usdt"] >= bh_roll[r["start"]] for r in rr if r["start"] in bh_roll])) if rr else None
        better_bh = bool(len(beats) >= 2 and sum(beats) >= 2 and share is not None and share >= 0.5)
        out[n] = {"full_window": "full_2014" if (n, "full_2014", 15) in R else "since_2017", "full_mdd": full["max_drawdown"], "return_over_mdd": full["return_over_mdd"], "final_usdt": full["final_usdt"],
                  "stress_within_50": st_ok, "rolling_within_50": roll_ok, "within_budget": within, "windows_beating_bh": beats, "rolling_share_final_ge_bh": share, "beats_bh_by_final_usdt": better_bh}
    cands = [(n, v) for n, v in out.items() if v["within_budget"] and n.startswith("ALLOC_") and v["return_over_mdd"] is not None]
    best = max(cands, key=lambda kv: (round(kv[1]["return_over_mdd"], 6), kv[1]["final_usdt"]))[0] if cands else None
    return {"per_config": out, "best_within_budget_by_return_over_mdd": best}


def x10_check(ctx: Ctx) -> list[dict]:
    rows = []
    for n in ("BH_BTC", "B1_BTC_SMA200", "ALLOC_50_Y", "A_X4"):
        m100 = run_one(ctx, n, 15, "2014-01-01", None, initial=100_000.0); m10 = run_one(ctx, n, 15, "2014-01-01", None, initial=10_000.0)
        rows.append({"name": n, "final_100k_real": m100["final_usdt"], "final_10k_x10": m10["final_usdt"] * 10, "diff_usdt": m100["final_usdt"] - m10["final_usdt"] * 10, "diff_pct": m100["final_usdt"] / (m10["final_usdt"] * 10) - 1,
                     "impact_100k": m100["impact_usdt"], "impact_10k_x10": m10["impact_usdt"] * 10})
    return rows


def run_phase20(c: dict | None = None, out_dir: Path | None = None, names: list[str] | None = None, windows: dict | None = None, rolling: bool = True, write: bool = True) -> dict:
    ctx = Ctx(c); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); names = names or all_names(); windows = windows or WINDOWS20; R = {}
    for wn, (a, b) in windows.items():
        for cost in COSTS20:
            for n in names:
                m = run_one(ctx, n, cost, a, b)
                if m is not None:
                    R[(n, wn, cost)] = m
    stress = {}
    for sn, (a, b) in stress_windows(ctx.d).items():
        stress[sn] = {"window": [a, b]}
        for n in names:
            m = run_one(ctx, n, 15, a, b)
            if m is not None:
                stress[sn][n] = {"final_usdt": m["final_usdt"], "max_drawdown": m["max_drawdown"]}
    roll = rolling_starts(ctx, [n for n in names if n.startswith("ALLOC_") or n in ("BH_BTC", "B1_BTC_SMA200", "B2_original")]) if rolling else {"runs": {}}
    J = judge(R, roll, {k: v for k, v in stress.items()}) if rolling else {}
    resd = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "research_initial_usdt": ctx.initial, "mac_accounts_initial_usdt_unchanged": 10_000.0, "category": "backtest（历史回测；100,000 USDT 研究本金真实重算；全部为已看过的数据，非盲测）",
            "latest_complete_day": ctx.latest, "windows": {k: (a, b or ctx.latest) for k, (a, b) in windows.items()}, "costs_one_side_bps": list(COSTS20), "preregistration": "docs/brando_pa_validation/phase20_capital100k/preregistration.md",
            "results": {f"{n}|{w}|{cc}": m for (n, w, cc), m in R.items()}, "stress": stress, "rolling": roll, "verdict": J, "x10_check": x10_check(ctx) if rolling else None, "real_trading": "disabled (no implementation)"}
    if write:
        (out / "phase20_results.json").write_text(json.dumps(resd, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return resd


if __name__ == "__main__":
    r = run_phase20()
    print(json.dumps({"n_results": len(r["results"]), "best": r["verdict"]["best_within_budget_by_return_over_mdd"]}, ensure_ascii=False))
