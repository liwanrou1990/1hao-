"""统一 USDT 评价：排行榜行、风险指标（USDT 与百分比）、月度环境诊断。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def max_consecutive_losses(trades: pd.DataFrame) -> int:
    if trades is None or trades.empty:
        return 0
    cur = best = 0
    for x in trades.sort_values("exit_ts")["net_pnl"].to_numpy(float):
        cur = cur + 1 if x < 0 else 0; best = max(best, cur)
    return int(best)


def longest_run_days(mask: np.ndarray, ts: pd.Series) -> float:
    best = run = 0; start = None; t = pd.to_datetime(ts, utc=True).to_numpy()
    for k, v in enumerate(mask):
        if v:
            if start is None:
                start = k
            run = (t[k] - t[start]) / np.timedelta64(1, "D"); best = max(best, run)
        else:
            start = None
    return float(best)


def month_capture(eq: pd.Series, ref: pd.Series) -> dict:
    e = eq.resample("1ME").last().pct_change().dropna(); r = ref.resample("1ME").last().pct_change().dropna()
    j = pd.concat([e.rename("s"), r.rename("a")], axis=1).dropna(); up = j[j["a"] > 0]; dn = j[j["a"] < 0]
    return {"up_month_capture": float(up["s"].sum() / up["a"].sum()) if len(up) and up["a"].sum() else None, "down_month_capture": float(dn["s"].sum() / dn["a"].sum()) if len(dn) and dn["a"].sum() else None}


def usdt_row(name: str, eq: pd.DataFrame, trades: pd.DataFrame | None, final: dict, initial: float, ref_ew: pd.Series, ref_btc_liq: float | None, dd_limit: float, kind: str = "model") -> dict:
    e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); e = e.set_index("ts"); eqs = e["equity"].astype(float)
    peak = eqs.cummax(); dd = eqs / peak - 1; dd_usdt = eqs - peak
    days = max((eqs.index[-1] - eqs.index[0]).days, 1); daily = eqs.resample("1D").last().dropna().pct_change().dropna(); sd = daily.std(ddof=1)
    liq = float(final.get("liquidation_usdt", eqs.iloc[-1]))
    expo = e["exposure"] if "exposure" in e else pd.Series(1.0, index=e.index)
    row = {"name": name, "kind": kind, "initial_usdt": initial, "final_equity_usdt": float(eqs.iloc[-1]), "liquidation_usdt": liq, "net_profit_usdt": float(eqs.iloc[-1] - initial), "realizable_profit_usdt": liq - initial, "total_return": float(eqs.iloc[-1] / initial - 1),
           "cagr": float((eqs.iloc[-1] / initial) ** (365 / days) - 1) if days >= 30 else None, "max_drawdown": float(dd.min()), "max_drawdown_usdt": float(dd_usdt.min()), "sharpe": float(daily.mean() / sd * np.sqrt(365)) if sd and sd > 0 else None,
           "n_trades": int(final.get("n_sells", 0)), "n_fills": int(final.get("n_fills", 0)), "avg_exposure": float(expo.mean()), "max_exposure": float(expo.max()), "time_in_market": float((expo > 0.01).mean()),
           "longest_all_cash_days": longest_run_days((expo <= 0.01).to_numpy(), e.index.to_series()), "max_consecutive_losses": max_consecutive_losses(trades),
           "contrib_btc_usdt": (final.get("contrib") or {}).get("BTCUSDT", {}).get("total"), "contrib_eth_usdt": (final.get("contrib") or {}).get("ETHUSDT", {}).get("total"),
           "fees_usdt": float(final.get("fees_usdt", final.get("fees_paid", 0.0)) or 0.0), "slippage_usdt": float(final.get("slippage_usdt", 0.0) or 0.0), "turnover_per_year": final.get("turnover_per_year"),
           "excess_vs_btc_sma200_usdt": (liq - ref_btc_liq) if ref_btc_liq is not None else None, "meets_risk_constraint": bool(dd.min() >= -dd_limit), "independently_validated": False,
           "yearly_profit_usdt": {str(y): float(g.iloc[-1] - (eqs[eqs.index < g.index[0]].iloc[-1] if (eqs.index < g.index[0]).any() else initial)) for y, g in eqs.groupby(eqs.index.year)}}
    row["cost_usdt"] = row["fees_usdt"] + row["slippage_usdt"]
    if ref_ew is not None:
        row |= month_capture(eqs, ref_ew)
    if "risk_state" in e:
        row["risk_state_share"] = {k: round(v, 4) for k, v in e["risk_state"].value_counts(normalize=True).to_dict().items()}
        cap0 = (e["cap"] <= 0).to_numpy() if "cap" in e else np.zeros(len(e), bool)
        row["longest_no_buy_days"] = longest_run_days(cap0, e.index.to_series()); row["no_buy_share"] = float(cap0.mean())
    for s in ("BTCUSDT", "ETHUSDT"):
        if f"w_{s}" in e:
            row[f"avg_w_{s}"] = float(e[f"w_{s}"].mean())
    if "w_USDT" in e:
        row["avg_w_USDT"] = float(e["w_USDT"].mean())
    return row


def regime_months(close: dict, curves: dict, thr: float = 0.03) -> dict:
    """月度环境分类（BTC/ETH 月收益 > +thr 上涨、< −thr 下跌、其余横盘）× 各曲线当月 USDT 盈亏之和与月数。"""
    px = {s: c.copy() for s, c in close.items()}
    for s in px:
        px[s].index = pd.to_datetime(px[s].index, utc=True)
    m = pd.concat([px["BTCUSDT"].resample("1ME").last().pct_change().rename("btc"), px["ETHUSDT"].resample("1ME").last().pct_change().rename("eth")], axis=1).dropna()
    def cls(r):
        b = "up" if r["btc"] > thr else "down" if r["btc"] < -thr else "flat"; e = "up" if r["eth"] > thr else "down" if r["eth"] < -thr else "flat"
        if b == "up" and e == "up": return "both_up"
        if b == "down" and e == "down": return "both_down"
        if b == "up" and e == "down": return "btc_up_eth_down"
        if e == "up" and b == "down": return "eth_up_btc_down"
        if e == "up" and b == "flat": return "eth_up_btc_flat"
        if b == "up" and e == "flat": return "btc_up_eth_flat"
        return "other"
    # 只统计曲线覆盖的月份（窗口内）
    spans = [pd.to_datetime(eq["ts"], utc=True) for eq in curves.values() if eq is not None and len(eq)]
    if spans:
        lo = min(x.min() for x in spans); hi = max(x.max() for x in spans)
        m = m[(m.index >= lo) & (m.index <= hi)]
    m["cat"] = m.apply(cls, axis=1)
    out = {"n_months": m["cat"].value_counts().to_dict(), "pnl_usdt": {}, "avg_btc_ret": m.groupby("cat")["btc"].mean().round(4).to_dict(), "avg_eth_ret": m.groupby("cat")["eth"].mean().round(4).to_dict()}
    for name, eq in curves.items():
        if eq is None:
            continue
        e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); me = e.set_index("ts")["equity"].resample("1ME").last(); pnl = me.diff(); pnl.iloc[0] = me.iloc[0] - float(e["equity"].iloc[0])
        j = pd.concat([pnl.rename("p"), m["cat"]], axis=1).dropna()
        out["pnl_usdt"][name] = j.groupby("cat")["p"].sum().round(0).to_dict()
    return out
