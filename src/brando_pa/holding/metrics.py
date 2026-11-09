"""第十阶段指标：CAGR、最大回撤与最长回撤期、年度收益、交易统计、成本、胜率/盈亏比、环境分类、超额收益。全部基于 HoldResult（不读账本文件）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cagr(initial: float, final: float, days: float) -> float:
    if initial <= 0 or final <= 0 or days <= 0:
        return float("nan")
    return float((final / initial) ** (365.25 / days) - 1)


def drawdown_stats(eq: pd.Series) -> dict:
    peak = eq.cummax(); dd = eq / peak - 1; mdd = float(dd.min()) if len(dd) else 0.0
    # 最长回撤期：从创新高到再创新高之间的最长天数（未恢复则计到期末）
    longest = 0; cur_start = None
    for t, v in dd.items():                                     # 回撤期 = 首个低于前高的日期 → 重新创高的日期；未恢复则计到期末（含）
        if v < -1e-12:
            cur_start = cur_start or t
        elif cur_start is not None:
            longest = max(longest, (t - cur_start).days); cur_start = None
    if cur_start is not None and len(dd):
        longest = max(longest, (dd.index[-1] - cur_start).days + 1)
    return {"max_drawdown": mdd, "max_drawdown_usdt": float((eq - peak).min()) if len(eq) else 0.0, "longest_drawdown_days": int(longest), "trough_date": str(dd.idxmin().date()) if len(dd) else None}


def annual_returns(nav: pd.DataFrame, initial: float) -> dict:
    s = nav.set_index("ts")["equity"]; out = {}
    for y, g in s.groupby(s.index.year):
        prev = s[s.index < g.index[0]]; base = float(prev.iloc[-1]) if len(prev) else initial
        out[str(y)] = float(g.iloc[-1] / base - 1)
    return out


def regime_labels(btc_close: pd.Series, lookback: int = 90, thr: float = 0.20) -> pd.Series:
    """预登记：按月，BTC 过去 90 日收益 > +20% 牛市、< −20% 熊市、其余震荡（用月末值）。"""
    r = btc_close.pct_change(lookback); m = r.resample("1ME").last()
    return m.apply(lambda x: "bull" if x > thr else ("bear" if x < -thr else "sideways") if pd.notna(x) else "sideways")


def regime_performance(nav: pd.DataFrame, btc_close: pd.Series) -> dict:
    s = nav.set_index("ts")["equity"]; monthly = s.resample("1ME").last(); pnl = monthly.diff(); pnl.iloc[0] = monthly.iloc[0] - s.iloc[0]
    lab = regime_labels(btc_close).reindex(monthly.index, method="nearest"); out = {}
    for k in ("bull", "bear", "sideways"):
        sel = pnl[lab == k]; out[k] = {"months": int(len(sel)), "pnl_usdt": float(sel.sum()) if len(sel) else 0.0, "avg_month_usdt": float(sel.mean()) if len(sel) else 0.0, "share_positive": float((sel > 0).mean()) if len(sel) else None}
    return out


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"n_round_trips": 0, "avg_hold_days": None, "win_rate": None, "profit_factor": None, "avg_net_pnl": None}
    w = trades[trades["net_pnl"] > 0]["net_pnl"].sum(); l = -trades[trades["net_pnl"] <= 0]["net_pnl"].sum()
    return {"n_round_trips": int(len(trades)), "avg_hold_days": float(trades["hold_days"].mean()), "median_hold_days": float(trades["hold_days"].median()), "win_rate": float((trades["net_pnl"] > 0).mean()), "profit_factor": float(w / l) if l > 0 else (float("inf") if w > 0 else None),
            "avg_net_pnl": float(trades["net_pnl"].mean()), "exit_reasons": trades["reason"].value_counts().to_dict()}


def summarize(res, btc_close: pd.Series | None = None) -> dict:
    nav = res.nav; eq = nav.set_index("ts")["equity"]; days = (nav["ts"].iloc[-1] - nav["ts"].iloc[0]).days + 1
    f = res.final; dd = drawdown_stats(eq); liq_curve = nav.set_index("ts")["liquidation"]
    out = {"name": res.name, "fee_bps": res.fee_bps, "slippage_bps": res.slip_bps, "one_side_cost_bps": res.fee_bps + res.slip_bps, "start": str(nav["ts"].iloc[0].date()), "end": str(nav["ts"].iloc[-1].date()), "days": int(days),
           "final_equity": f["equity"], "equity_profit_usdt": f["equity_profit_usdt"], "liquidation_usdt": f["liquidation_usdt"], "liquidation_net_profit_usdt": f["liquidation_net_profit_usdt"], "realized_net_usdt": f["realized_net_usdt"], "unrealized_usdt": f["unrealized_usdt"], "hypothetical_exit_cost_usdt": f["hypothetical_exit_cost_usdt"],
           "cagr_equity": cagr(res.initial, f["equity"], days), "cagr_liquidation": cagr(res.initial, f["liquidation_usdt"], days), "fees_usdt": f["fees_usdt"], "slippage_usdt": f["slippage_usdt"], "total_cost_usdt": f["fees_usdt"] + f["slippage_usdt"],
           "n_fills": f["n_fills"], "open_positions_at_end": f["open_positions"], **dd, "liquidation_max_drawdown": float((liq_curve / liq_curve.cummax() - 1).min()), "annual_returns": annual_returns(nav, res.initial), "trades": trade_stats(res.trades),
           "time_in_market": float((nav["exposure"] > 0.01).mean())}
    if btc_close is not None:
        out["regimes"] = regime_performance(nav, btc_close)
    return out
