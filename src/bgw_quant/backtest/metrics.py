"""绩效指标（基于每日净收益序列）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def performance_metrics(daily_ret: pd.Series, trades: list[dict] | None = None, capital_usd: float | None = None,
                        total_cost_usd: float | None = None, hindsight_daily: pd.Series | None = None) -> dict:
    r = daily_ret.dropna().astype(float)
    n = len(r)
    out: dict = {"n_days": int(n)}
    if n == 0:
        return out | {"note": "无样本"}
    cum = (1 + r).cumprod()
    out["total_net_return"] = float(cum.iloc[-1] - 1)
    out["mean_daily_net_return"] = float(r.mean())
    out["annualized_return"] = float(cum.iloc[-1] ** (TRADING_DAYS / n) - 1) if n >= 5 else None
    sd = r.std(ddof=1) if n > 1 else np.nan
    out["sharpe"] = float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd and sd > 0 else None
    dn = r[r < 0]
    dsd = np.sqrt((dn ** 2).mean()) if len(dn) else np.nan
    out["sortino"] = float(r.mean() / dsd * np.sqrt(TRADING_DAYS)) if dsd and dsd > 0 else None
    dd = cum / cum.cummax() - 1
    out["max_drawdown"] = float(dd.min())
    out["daily_win_rate"] = float((r > 0).mean())
    out["best_day"], out["worst_day"] = float(r.max()), float(r.min())
    if trades is not None:
        pnl = np.array([t.get("net_pnl_usd", 0.0) for t in trades], dtype=float)
        out["n_trades"] = int(len(pnl))
        out["trades_per_day"] = float(len(pnl) / n)
        if len(pnl):
            wins, losses = pnl[pnl > 0], pnl[pnl < 0]
            out["trade_win_rate"] = float((pnl > 0).mean())
            out["avg_win_usd"] = float(wins.mean()) if len(wins) else 0.0
            out["avg_loss_usd"] = float(losses.mean()) if len(losses) else 0.0
            out["payoff_ratio"] = float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None
            out["profit_factor"] = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else (float("inf") if len(wins) else None)
    if capital_usd and total_cost_usd is not None:
        gross_pnl = capital_usd * out["total_net_return"] + total_cost_usd
        out["total_cost_usd"] = float(total_cost_usd)
        out["cost_share_of_gross"] = float(total_cost_usd / gross_pnl) if gross_pnl > 0 else None
        out["cost_pct_of_capital_per_day"] = float(total_cost_usd / capital_usd / n)
    if hindsight_daily is not None:
        h = hindsight_daily.reindex(r.index).dropna()
        if len(h):
            out["hindsight_mean_daily"] = float(h.mean())
            out["gap_to_hindsight_daily"] = float(h.mean() - r.reindex(h.index).mean())
            out["capture_ratio_vs_hindsight"] = float(r.reindex(h.index).mean() / h.mean()) if h.mean() > 0 else None
    return out
