"""纯现货绩效统计与对照（§七）：总收益、年化、最大回撤、交易次数、胜率、平均每笔、PF、持仓时间占比、资金利用率、相对买入持有的超额、年度与环境表现；图表（PNG + HTML）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def equity_stats(eq: pd.DataFrame, initial: float) -> dict:
    if eq is None or eq.empty:
        return {}
    e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); e = e.set_index("ts")["equity"].astype(float)
    days = max((e.index[-1] - e.index[0]).days, 1)
    total = float(e.iloc[-1] / initial - 1)
    dd = (e / e.cummax() - 1)
    daily = e.resample("1D").last().dropna().pct_change().dropna()
    sd = daily.std(ddof=1)
    return {"final_equity": float(e.iloc[-1]), "total_return": total, "cagr": float((e.iloc[-1] / initial) ** (365 / days) - 1) if days >= 30 else None, "max_drawdown": float(dd.min()),
            "sharpe_daily": float(daily.mean() / sd * np.sqrt(365)) if sd and sd > 0 else None, "n_days": int(days), "calmar": float(((e.iloc[-1] / initial) ** (365 / days) - 1) / abs(dd.min())) if days >= 30 and dd.min() < 0 else None}


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {"n_trades": 0}
    pnl = tr["net_pnl"].to_numpy(float); w = pnl[pnl > 0]; l = pnl[pnl < 0]
    r = tr["r_multiple"].to_numpy(float)
    streak = cur = 0
    for x in pnl:
        cur = cur + 1 if x < 0 else 0; streak = max(streak, cur)
    return {"n_trades": int(len(pnl)), "win_rate": float((pnl > 0).mean()), "avg_pnl": float(pnl.mean()), "avg_win": float(w.mean()) if len(w) else 0.0, "avg_loss": float(l.mean()) if len(l) else 0.0,
            "profit_factor": float(w.sum() / abs(l.sum())) if len(l) and l.sum() != 0 else (float("inf") if len(w) else None), "expectancy_r": float(np.nanmean(r)) if np.isfinite(r).any() else None, "avg_ret_pct": float(tr["ret_pct"].mean()),
            "max_consecutive_losses": int(streak), "total_net_pnl": float(pnl.sum()), "by_strategy": {k: {"n": int(len(g)), "win_rate": float((g["net_pnl"] > 0).mean()), "net_pnl": float(g["net_pnl"].sum()), "expectancy_r": float(g["r_multiple"].mean())} for k, g in tr.groupby("strategy")},
            "by_exit": tr["exit_reason"].value_counts().to_dict(), "by_regime4": ({k: {"n": int(len(g)), "net_pnl": float(g["net_pnl"].sum()), "expectancy_r": float(g["r_multiple"].mean()), "win_rate": float((g["net_pnl"] > 0).mean())} for k, g in tr.groupby("regime4")} if "regime4" in tr else {}),
            "by_state_at_exit": ({k: {"n": int(len(g)), "net_pnl": float(g["net_pnl"].sum())} for k, g in tr.groupby("state_at_exit")} if "state_at_exit" in tr else {})}


def utilization(eq: pd.DataFrame) -> dict:
    if eq is None or eq.empty:
        return {}
    return {"time_in_market_share": float((eq["exposure"] > 0.001).mean()), "avg_exposure": float(eq["exposure"].mean()), "avg_exposure_when_in": float(eq.loc[eq["exposure"] > 0.001, "exposure"].mean()) if (eq["exposure"] > 0.001).any() else 0.0, "halted_share": float(eq["halted"].mean()) if "halted" in eq else 0.0}


def yearly(eq: pd.DataFrame) -> dict:
    if eq is None or eq.empty:
        return {}
    e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); e = e.set_index("ts")["equity"].astype(float)
    out = {}
    for y, g in e.groupby(e.index.year):
        prev = e[e.index < g.index[0]]
        base = float(prev.iloc[-1]) if len(prev) else float(g.iloc[0])
        out[str(y)] = float(g.iloc[-1] / base - 1)
    return out


def regime_returns(eq: pd.DataFrame, regime_series: pd.Series) -> dict:
    """按市场环境（日线四分类，按 BTC 日线对齐到执行 K 线）分段统计策略权益收益与买入持有对照需由调用方提供两条曲线。"""
    if eq is None or eq.empty or regime_series is None:
        return {}
    e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); e = e.set_index("ts")["equity"].astype(float)
    r = e.pct_change().fillna(0.0)
    rs = regime_series.copy(); rs.index = pd.to_datetime(rs.index, utc=True)
    reg = rs.reindex(e.index, method="ffill")
    out = {}
    for k, g in r.groupby(reg):
        out[str(k)] = {"bars": int(len(g)), "cum_return": float((1 + g).prod() - 1)}
    return out


def compare_table(rows: dict) -> str:
    cols = ["总收益", "年化", "最大回撤", "Sharpe", "交易次数", "胜率", "平均每笔", "PF", "ExpR", "持仓时间占比", "资金利用率", "超额(vs 对应B&H)"]
    L = ["| 组合 | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    def f(x, pct=False, d=2):
        if x is None or (isinstance(x, float) and x != x):
            return "—"
        if isinstance(x, float) and x == float("inf"):
            return "∞"
        return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"
    for name, r in rows.items():
        es, ts, ut = r.get("equity", {}), r.get("trades", {}), r.get("util", {})
        L.append(f"| {name} | {f(es.get('total_return'), True)} | {f(es.get('cagr'), True)} | {f(es.get('max_drawdown'), True)} | {f(es.get('sharpe_daily'))} | {ts.get('n_trades', 0)} | {f(ts.get('win_rate'), True, 1)} | {f(ts.get('avg_pnl'))} | {f(ts.get('profit_factor'))} | {f(ts.get('expectancy_r'), d=3)} | {f(ut.get('time_in_market_share'), True, 0)} | {f(ut.get('avg_exposure'), True, 0)} | {f(r.get('excess_vs_bh'), True)} |")
    return "\n".join(L)


def plot_equity(curves: dict[str, pd.DataFrame], path: Path, title: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"]          # 容器无中文字体：图内标签用英文，中文说明在报告/看板
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for name, df in curves.items():
        if df is None or df.empty:
            continue
        ax.plot(pd.to_datetime(df["ts"]), df["equity"], label=name, lw=1.2)
    ax.set_title(title); ax.legend(fontsize=8); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    return str(path)


def plot_trades_html(mc, trades: pd.DataFrame, fills: pd.DataFrame, path: Path, symbol: str, start, end, candle_tf: str = "4h") -> str:
    import plotly.graph_objects as go
    bars = mc.bars.get(candle_tf, mc.ex)
    if start is not None:
        bars = bars[bars["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        bars = bars[bars["ts"] <= pd.Timestamp(end, tz="UTC")]
    fig = go.Figure(go.Candlestick(x=bars["ts"], open=bars["open"], high=bars["high"], low=bars["low"], close=bars["close"], name=symbol, showlegend=False))
    if fills is not None and not fills.empty:
        f_ = fills[fills["symbol"] == symbol]
        b = f_[f_["side"] == "BUY"]; s_ = f_[f_["side"] == "SELL"]
        fig.add_trace(go.Scatter(x=pd.to_datetime(b["ts"]), y=b["price"], mode="markers", marker=dict(symbol="triangle-up", size=11, color="#2e8b57"), name="BUY", text=b["strategy"]))
        fig.add_trace(go.Scatter(x=pd.to_datetime(s_["ts"]), y=s_["price"], mode="markers", marker=dict(symbol="triangle-down", size=11, color="#b22222"), name="SELL", text=s_["reason"]))
    fig.update_layout(title=f"{symbol} 现货买卖标记（{candle_tf} K 线）", height=520, xaxis_rangeslider_visible=False, margin=dict(l=40, r=20, t=40, b=30))
    fig.write_html(str(path), include_plotlyjs="cdn")
    return str(path)
