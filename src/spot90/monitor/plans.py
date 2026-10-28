"""三种现货方案的历史比较（相同起始日、相同资金、相同手续费/滑点、相同现金收益假设）：
  P1 一次性买入：起始日次日开盘全仓买入，持有到窗口末。
  P2 分批定投：资金分 n 份，每 k 天在开盘买一份（第一份在起始日次日），未投入现金按年化 cash_yield 计息。
  P3 等待回调：起始日收盘挂 −d% 限价 14 天；成交则全仓买入；未成交则按 fallback：'market'=第 15 天开盘市价买入，'cash'=全程持币。
窗口：从起始日到起始日 + H 天（H 默认 90，可选 180/365），所有方案都在窗口末按收盘卖出计净值（卖出扣费）。
指标：窗口末净收益、窗口内权益最大回撤（含现金）、最大浮亏、投入资金比例；按年份、按起始日趋势状态汇总；与 P1 差值的块自助 CI。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..backtest.evaluate import block_bootstrap_ci
from ..config import cfg, report_dir
from .rules import enrich


def _costs():
    c = cfg()["costs"]
    return (c["taker_fee_bps"] + c["slippage_bps_market"]) / 1e4, (c["maker_fee_bps"] + c["slippage_bps_limit"]) / 1e4


def simulate(bars_1d: pd.DataFrame, H: int = 90, capital: float = 10_000.0, dca_n: int = 6, dca_k: int = 7, wait_d: float = 3.0, wait_days: int = 14,
             fallback: str = "market", cash_yield: float = 0.0) -> pd.DataFrame:
    df = bars_1d.sort_values("ts").reset_index(drop=True)
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(df); mkt, lim = _costs()
    daily_cash = (1 + cash_yield) ** (1 / 365) - 1
    rows = []
    last_start = n - H - 1
    e = enrich(df)
    for i in range(400, last_start):
        win = slice(i + 1, i + H + 1)                                   # 持有日 i+1 … i+H
        cl = c[win]; lo = l[win]
        def path_eq(coins: np.ndarray, cash: np.ndarray):
            eq = coins * cl * (1 - mkt) + cash
            eq_low = coins * lo * (1 - mkt) + cash
            return eq, eq_low
        res = {"ts": df["ts"].iloc[i], "state": e["state"].iloc[i]}
        # P1
        coins = np.full(H, capital * (1 - mkt) / o[i + 1]); cash = np.zeros(H)
        eq, eql = path_eq(coins, cash)
        res.update(_metrics("P1", eq, eql, capital, invested=1.0))
        # P2 DCA
        coins = np.zeros(H); cash = np.zeros(H); held = 0.0; bal = capital; share = capital / dca_n
        for t in range(H):
            day = i + 1 + t
            if t % dca_k == 0 and t // dca_k < dca_n:
                buy = min(share, bal); held += buy * (1 - mkt) / o[day]; bal -= buy
            bal *= (1 + daily_cash)
            coins[t], cash[t] = held, bal
        eq, eql = path_eq(coins, cash)
        res.update(_metrics("P2", eq, eql, capital, invested=1 - cash[-1] / capital if cash[-1] < 1 else 1.0))
        # P3 wait
        limit = c[i] * (1 - wait_d / 100)
        coins = np.zeros(H); cash = np.zeros(H); held = 0.0; bal = capital; filled = False
        for t in range(H):
            day = i + 1 + t
            if not filled and held == 0 and t < wait_days and l[day] <= limit:
                px = min(limit, o[day]); held = bal * (1 - lim) / px; bal = 0.0; filled = True
            elif not filled and held == 0 and t == wait_days and fallback == "market":
                held = bal * (1 - mkt) / o[day]; bal = 0.0
            bal *= (1 + daily_cash)
            coins[t], cash[t] = held, bal
        eq, eql = path_eq(coins, cash)
        m3 = _metrics("P3", eq, eql, capital, invested=1.0 if held > 0 else 0.0); m3["P3_filled"] = int(filled)
        res.update(m3)
        rows.append(res)
    out = pd.DataFrame(rows)
    out["year"] = out["ts"].dt.year
    return out


def _metrics(tag: str, eq: np.ndarray, eq_low: np.ndarray, capital: float, invested: float) -> dict:
    peak = np.maximum.accumulate(np.maximum(eq, capital))
    mdd = float((eq_low / peak - 1).min())
    return {f"{tag}_ret": float(eq[-1] / capital - 1), f"{tag}_mdd": min(mdd, 0.0), f"{tag}_mae": float(eq_low.min() / capital - 1), f"{tag}_invested": float(invested)}


def summarize(sim: pd.DataFrame, H: int) -> dict:
    out = {"n_starts": int(len(sim)), "range": [str(sim["ts"].min().date()), str(sim["ts"].max().date())], "plans": {}, "by_year": {}, "by_state": {}}
    for p in ("P1", "P2", "P3"):
        r, m, a = sim[f"{p}_ret"], sim[f"{p}_mdd"], sim[f"{p}_mae"]
        d = {"mean_ret": float(r.mean()), "median_ret": float(r.median()), "win_rate": float((r > 0).mean()), "p5_ret": float(r.quantile(0.05)), "p95_ret": float(r.quantile(0.95)),
             "mean_mdd": float(m.mean()), "p_mdd_gt_15": float((m < -0.15).mean()), "p_mdd_gt_20": float((m < -0.20).mean()), "mean_mae": float(a.mean()),
             "ret_over_mdd": float(r.mean() / -m.mean()) if m.mean() < 0 else None, "avg_invested": float(sim[f"{p}_invested"].mean())}
        if p != "P1":
            d["diff_vs_P1_mean"] = float((r - sim["P1_ret"]).mean()); d["diff_vs_P1_ci95"] = list(block_bootstrap_ci((r - sim["P1_ret"]).to_numpy(), max(H, 30)))
            d["mdd_diff_vs_P1_mean"] = float((m - sim["P1_mdd"]).mean())
        if p == "P3":
            d["fill_rate"] = float(sim["P3_filled"].mean())
        out["plans"][p] = d
    for key, col in (("by_year", "year"), ("by_state", "state")):
        for g, s in sim.groupby(col):
            out[key][str(g)] = {p: {"mean_ret": float(s[f"{p}_ret"].mean()), "win_rate": float((s[f"{p}_ret"] > 0).mean()), "mean_mdd": float(s[f"{p}_mdd"].mean())} for p in ("P1", "P2", "P3")} | {"n": int(len(s))}
    return out


def run_all(bars: dict, horizons=(90, 180, 365), variants=None) -> dict:
    variants = variants or [{"name": "默认（定投6份×7天，等待3%，未成交第15天市价买，现金0%）", "dca_n": 6, "dca_k": 7, "wait_d": 3.0, "fallback": "market", "cash_yield": 0.0},
                            {"name": "等待5%未成交持币，现金年化4%", "dca_n": 6, "dca_k": 7, "wait_d": 5.0, "fallback": "cash", "cash_yield": 0.04},
                            {"name": "定投12份×7天，等待3%，现金年化4%", "dca_n": 12, "dca_k": 7, "wait_d": 3.0, "fallback": "market", "cash_yield": 0.04}]
    out = {"costs": cfg()["costs"], "results": {}}
    for sym, b in bars.items():
        out["results"][sym] = {}
        for H in horizons:
            for v in variants:
                sim = simulate(b["1d"], H=H, dca_n=v["dca_n"], dca_k=v["dca_k"], wait_d=v["wait_d"], fallback=v["fallback"], cash_yield=v["cash_yield"])
                out["results"][sym][f"H{H}|{v['name']}"] = summarize(sim, H) | {"params": v}
    d = report_dir() / "monitor"; d.mkdir(parents=True, exist_ok=True)
    (d / "plans_comparison.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    return out
