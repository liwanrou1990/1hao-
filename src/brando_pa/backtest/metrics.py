"""绩效统计：按交易（胜率、均盈/均亏、Profit Factor、Expectancy、连亏、手续费影响）与按净值曲线（最大回撤、Sharpe、Sortino、总净收益）。
全部基于真实回测产物，不做平滑、不剔除样本。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {"n_trades": 0}
    pnl = tr["net_pnl"].to_numpy(float); r = tr["r_multiple"].to_numpy(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    streak = cur = 0
    for x in pnl:
        cur = cur + 1 if x < 0 else 0; streak = max(streak, cur)
    gross = float(tr["gross_pnl"].sum()); fees = float(tr["fees"].sum()); fund = float(tr["funding"].sum())
    return {"n_trades": int(len(pnl)), "win_rate": float((pnl > 0).mean()), "avg_win": float(wins.mean()) if len(wins) else 0.0, "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else (float("inf") if len(wins) else None),
            "expectancy_usd": float(pnl.mean()), "expectancy_r": float(np.nanmean(r)), "avg_r_win": float(np.nanmean(r[pnl > 0])) if len(wins) else 0.0, "avg_r_loss": float(np.nanmean(r[pnl < 0])) if len(losses) else 0.0,
            "total_net_pnl": float(pnl.sum()), "gross_pnl": gross, "total_fees": fees, "total_funding": fund, "cost_share_of_gross": float((fees + fund) / gross) if gross > 0 else None,
            "max_consecutive_losses": int(streak), "avg_bars_held": float(tr["bars_held"].mean()), "n_long": int((tr["direction"] == "long").sum()), "n_short": int((tr["direction"] == "short").sum()),
            "exit_reasons": tr["exit_reason"].value_counts().to_dict()}


def equity_stats(eq: pd.DataFrame, periods_per_year: float = 365.0) -> dict:
    if eq is None or eq.empty:
        return {}
    e = eq.set_index("ts")["equity"].astype(float)
    daily = e.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    cum = e / e.iloc[0]
    dd = cum / cum.cummax() - 1
    sd = r.std(ddof=1) if len(r) > 1 else np.nan
    dn = r[r < 0]; dsd = np.sqrt((dn ** 2).mean()) if len(dn) else np.nan
    n_days = max((e.index[-1] - e.index[0]).days, 1)
    return {"total_net_return": float(e.iloc[-1] / e.iloc[0] - 1), "annualized_return": float((e.iloc[-1] / e.iloc[0]) ** (365 / n_days) - 1) if n_days >= 30 else None,
            "max_drawdown": float(dd.min()), "sharpe": float(r.mean() / sd * np.sqrt(periods_per_year)) if sd and sd > 0 else None,
            "sortino": float(r.mean() / dsd * np.sqrt(periods_per_year)) if dsd and dsd > 0 else None, "n_days": int(n_days), "final_equity": float(e.iloc[-1]), "initial_equity": float(e.iloc[0])}


def by_group(tr: pd.DataFrame, col: str, min_trades: int = 1) -> dict:
    out = {}
    if tr is None or tr.empty or col not in tr:
        return out
    for k, g in tr.groupby(col):
        if len(g) >= min_trades:
            out[str(k)] = {"n_trades": int(len(g)), "win_rate": float((g["net_pnl"] > 0).mean()), "net_pnl": float(g["net_pnl"].sum()), "expectancy_r": float(g["r_multiple"].mean()),
                           "profit_factor": _pf(g["net_pnl"])}
    return out


def _pf(pnl: pd.Series):
    w = pnl[pnl > 0].sum(); l = pnl[pnl < 0].sum()
    return float(w / abs(l)) if l != 0 else (float("inf") if w > 0 else None)


def buy_and_hold(contexts: dict, start: pd.Timestamp | None, end: pd.Timestamp | None, initial: float, fee_bps: float = 10.0) -> dict:
    """等权买入持有基准（期初买入、期末卖出，各扣一次手续费）。"""
    out = {}; rets = []
    for s, mc in contexts.items():
        df = mc.ex
        if start is not None:
            df = df[df["ts"] >= start]
        if end is not None:
            df = df[df["ts"] <= end]
        if df.empty:
            continue
        p0, p1 = float(df["open"].iloc[0]), float(df["close"].iloc[-1])
        r = p1 / p0 * (1 - fee_bps / 1e4) ** 2 - 1
        close = df["close"].to_numpy(float); dd = float((close / np.maximum.accumulate(close) - 1).min())
        out[s] = {"return": r, "max_drawdown": dd}
        rets.append(r)
    if rets:
        out["equal_weight"] = {"return": float(np.mean(rets)), "final_equity": initial * (1 + float(np.mean(rets)))}
    return out


def summarize(result, contexts: dict | None = None, min_trades_regime: int = 5) -> dict:
    tr = result.trades
    summ = {"period": result.period, "exec_tf": result.exec_tf, "symbols": result.symbols, "trade_stats": trade_stats(tr), "equity_stats": equity_stats(result.equity),
            "status_counts": result.status_counts, "n_cancelled_entries": len(result.cancelled_entries), "notes": result.notes}
    if tr is not None and not tr.empty:
        summ["by_strategy"] = by_group(tr, "strategy"); summ["by_symbol"] = by_group(tr, "symbol"); summ["by_regime"] = by_group(tr, "regime", min_trades_regime)
        summ["by_direction"] = by_group(tr, "direction"); summ["by_exit_reason"] = by_group(tr, "exit_reason")
        summ["by_year"] = by_group(tr.assign(year=pd.to_datetime(tr["entry_ts"]).dt.year), "year")
    if not result.signals_log.empty:
        sl = result.signals_log
        summ["candidates"] = {"n_candidates": int(len(sl)), "n_accepted": int(sl["accepted"].sum()), "rejection_counts": pd.Series([f for fs in sl["fails"] for f in str(fs).split(",") if f]).value_counts().head(15).to_dict(),
                              "by_strategy": sl.groupby("strategy")["accepted"].agg(["count", "sum"]).rename(columns={"count": "candidates", "sum": "accepted"}).to_dict("index")}
    if contexts:
        s, e = (pd.Timestamp(result.period[0], tz="UTC"), pd.Timestamp(result.period[1], tz="UTC")) if result.period[0] else (None, None)
        summ["buy_and_hold"] = buy_and_hold(contexts, s, e, result.equity["equity"].iloc[0] if not result.equity.empty else 0.0)
    return summ
