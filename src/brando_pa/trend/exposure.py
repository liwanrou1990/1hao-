"""ExposureAnalyzer：上涨行情参与率与持仓行为指标（§五）。

输入：策略逐根权益（含 exposure、state_<symbol>）、成交记录 fills、平仓记录 trades、标的 1h 收盘序列。
指标：
  bull_time_in_market_share    日线 BULL 期间持仓（敞口 > 1%）的时间占比
  avg_exposure                  全周期平均现货仓位
  idle_share / idle_longest_days 空仓等待时间占比 / 最长连续空仓天数
  up_month_capture              上涨月份：策略月收益之和 / 标的月收益之和（标的上涨的月份）
  down_month_capture            下跌月份：同上（越小越好；负值表示下跌月反而上涨）
  missed_after_sell             每次卖出后到下一次买入（或期末）的标的涨幅；均值与合计（正 = 错过的行情）
  reentries_after_stop          止损/跟踪止损退出后重新买入的次数
  avg_hold_days                 每笔平仓平均持仓天数
  gap_vs_bh                     策略权益 / 买入持有权益 − 1 的期末值与最低值
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def analyze(eq: pd.DataFrame, fills: pd.DataFrame, trades: pd.DataFrame, close: pd.Series, symbol: str, bh_equity: pd.DataFrame | None = None) -> dict:
    out = {}
    if eq is None or eq.empty:
        return out
    e = eq.copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); e = e.set_index("ts")
    inmkt = e["exposure"] > 0.01
    st = e.get(f"state_{symbol}")
    out["avg_exposure"] = float(e["exposure"].mean())
    out["time_in_market_share"] = float(inmkt.mean())
    if st is not None:
        bull = st == "BULL"
        out["bull_share_of_time"] = float(bull.mean())
        out["bull_time_in_market_share"] = float(inmkt[bull].mean()) if bull.any() else None
        out["bull_avg_exposure"] = float(e.loc[bull, "exposure"].mean()) if bull.any() else None
    out["idle_share"] = float((~inmkt).mean())
    # 最长连续空仓
    run = 0; longest = 0; bar_h = (e.index[1] - e.index[0]).total_seconds() / 3600 if len(e) > 1 else 1
    for v in (~inmkt).to_numpy():
        run = run + 1 if v else 0; longest = max(longest, run)
    out["idle_longest_days"] = float(longest * bar_h / 24)
    # 月度捕获
    px = close.copy(); px.index = pd.to_datetime(px.index, utc=True)
    m_eq = e["equity"].resample("1ME").last().pct_change().dropna()
    m_px = px.resample("1ME").last().pct_change().dropna()
    j = pd.concat([m_eq.rename("s"), m_px.rename("a")], axis=1).dropna()
    up = j[j["a"] > 0]; dn = j[j["a"] < 0]
    out["up_month_capture"] = float(up["s"].sum() / up["a"].sum()) if len(up) and up["a"].sum() != 0 else None
    out["down_month_capture"] = float(dn["s"].sum() / dn["a"].sum()) if len(dn) and dn["a"].sum() != 0 else None
    out["n_up_months"], out["n_down_months"] = int(len(up)), int(len(dn))
    # 卖出后错过的行情 / 止损后再入场
    missed = []; reentry_after_stop = 0
    if fills is not None and not fills.empty:
        f = fills[fills["symbol"] == symbol].copy() if "symbol" in fills else fills.copy()
        f["ts"] = pd.to_datetime(f["ts"], utc=True); f = f.sort_values("ts").reset_index(drop=True)
        last_reason = None
        for k, row in f.iterrows():
            if row["side"] == "SELL":
                nxt = f[(f.index > k) & (f["side"] == "BUY")]
                ref = float(nxt["price"].iloc[0]) if len(nxt) else float(px.iloc[-1])
                missed.append(ref / float(row["price"]) - 1)
                last_reason = str(row.get("reason", ""))
            elif row["side"] == "BUY" and last_reason is not None:
                if any(k_ in last_reason for k_ in ("stop", "trail")):
                    reentry_after_stop += 1
                last_reason = None
    out["missed_after_sell_mean"] = float(np.mean(missed)) if missed else None
    out["missed_after_sell_sum"] = float(np.sum(missed)) if missed else None
    out["missed_after_sell_n"] = int(len(missed))
    out["reentries_after_stop"] = int(reentry_after_stop)
    if trades is not None and not trades.empty and "hold_days" in trades:
        out["avg_hold_days"] = float(trades["hold_days"].mean()); out["median_hold_days"] = float(trades["hold_days"].median())
    elif trades is not None and not trades.empty:
        hd = (pd.to_datetime(trades["exit_ts"], utc=True) - pd.to_datetime(trades["entry_ts"], utc=True)).dt.total_seconds() / 86400
        out["avg_hold_days"] = float(hd.mean()); out["median_hold_days"] = float(hd.median())
    if bh_equity is not None and not bh_equity.empty:
        b = bh_equity.copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b.set_index("ts")["equity"].reindex(e.index, method="ffill")
        gap = e["equity"] / b - 1
        out["gap_vs_bh_final"] = float(gap.iloc[-1]); out["gap_vs_bh_min"] = float(gap.min()); out["gap_vs_bh_max"] = float(gap.max())
    return out


def reconstruct(fills: pd.DataFrame, trades: pd.DataFrame, close: pd.Series, symbol: str, start, end) -> dict:
    """逐笔还原：买入/卖出/止损/空仓/再入场，并把标的在每段空仓期间的涨跌记录下来（解释“为什么标的大涨而策略没赚到”）。"""
    px = close.copy(); px.index = pd.to_datetime(px.index, utc=True)
    px = px[(px.index >= pd.Timestamp(start, tz="UTC")) & (px.index <= pd.Timestamp(end, tz="UTC"))] if start else px
    f = fills[fills["symbol"] == symbol].copy() if (fills is not None and not fills.empty and "symbol" in fills) else (fills.copy() if fills is not None else pd.DataFrame())
    segs = []; flat_from = px.index[0]; flat_px = float(px.iloc[0]); hold_from = None; hold_px = None
    if not f.empty:
        f["ts"] = pd.to_datetime(f["ts"], utc=True); f = f.sort_values("ts")
        qty = 0.0
        for _, row in f.iterrows():
            if row["side"] == "BUY" and qty <= 1e-12:
                segs.append({"kind": "flat", "from": str(flat_from)[:16], "to": str(row["ts"])[:16], "asset_move": float(row["price"]) / flat_px - 1, "days": (row["ts"] - flat_from).total_seconds() / 86400})
                hold_from, hold_px = row["ts"], float(row["price"])
            qty += row["qty"] if row["side"] == "BUY" else -row["qty"]
            if row["side"] == "SELL" and qty <= 1e-9:
                segs.append({"kind": "hold", "from": str(hold_from)[:16], "to": str(row["ts"])[:16], "asset_move": float(row["price"]) / hold_px - 1, "days": (row["ts"] - hold_from).total_seconds() / 86400, "exit": str(row.get("reason", ""))})
                flat_from, flat_px = row["ts"], float(row["price"]); qty = 0.0
        if qty > 1e-9:
            segs.append({"kind": "hold", "from": str(hold_from)[:16], "to": str(px.index[-1])[:16], "asset_move": float(px.iloc[-1]) / hold_px - 1, "days": (px.index[-1] - hold_from).total_seconds() / 86400, "exit": "open_at_end"})
        else:
            segs.append({"kind": "flat", "from": str(flat_from)[:16], "to": str(px.index[-1])[:16], "asset_move": float(px.iloc[-1]) / flat_px - 1, "days": (px.index[-1] - flat_from).total_seconds() / 86400})
    sg = pd.DataFrame(segs)
    summary = {}
    if not sg.empty:
        for kind in ("flat", "hold"):
            g = sg[sg["kind"] == kind]
            summary[kind] = {"segments": int(len(g)), "days": float(g["days"].sum()), "asset_move_compounded": float((1 + g["asset_move"]).prod() - 1), "asset_move_sum": float(g["asset_move"].sum()), "largest_move": float(g["asset_move"].max()) if len(g) else None}
        summary["asset_total_move"] = float(px.iloc[-1] / px.iloc[0] - 1)
    if trades is not None and not trades.empty:
        summary["exits"] = trades["exit_reason"].value_counts().to_dict()
        summary["stopped_then_asset_rose_10pct_within_30d"] = None
    return {"segments": segs, "summary": summary}
