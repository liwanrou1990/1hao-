"""交易频率与卖出信号分析（§五）。

对每一次卖出：触发原因、持仓时长、盈亏、之后是否重新买入、间隔、再买价相对卖出价、卖出到再买（或期末）期间标的最低/最高价。
分类（描述性标签，阈值固定）：
  protective  卖出后到再买/期末之前标的曾跌破卖出价 10% 以上（这次退出避开了 ≥10% 的进一步下跌）
  whipsaw     30 日内以高于卖出价的价格重新买入，且期间没有 ≥10% 的下跌（来回磨损）
  neutral     其余
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def analyze_sells(fills: pd.DataFrame, close_by_symbol: dict[str, pd.Series], protect_drop: float = 0.10, whipsaw_days: int = 30) -> pd.DataFrame:
    if fills is None or fills.empty:
        return pd.DataFrame()
    f = fills.copy(); f["ts"] = pd.to_datetime(f["ts"], utc=True); f = f.sort_values("ts").reset_index(drop=True)
    rows = []
    for s, g in f.groupby("symbol"):
        px = close_by_symbol[s].copy(); px.index = pd.to_datetime(px.index, utc=True); px = px.sort_index()
        g = g.reset_index(drop=True); qty = 0.0; cost = 0.0; entry_ts = None
        for k, r in g.iterrows():
            if r["side"] == "BUY":
                if qty <= 1e-12:
                    entry_ts = r["ts"]; cost = 0.0
                cost += r["qty"] * r["price"]; qty += r["qty"]
            else:
                avg = cost / qty if qty > 0 else np.nan
                nxt = g[(g.index > k) & (g["side"] == "BUY")]
                rebuy_ts = nxt["ts"].iloc[0] if len(nxt) else None; rebuy_px = float(nxt["price"].iloc[0]) if len(nxt) else None
                seg = px[(px.index > r["ts"]) & ((px.index <= rebuy_ts) if rebuy_ts is not None else True)]
                lo = float(seg.min() / r["price"] - 1) if len(seg) else 0.0; hi = float(seg.max() / r["price"] - 1) if len(seg) else 0.0
                end_ref = rebuy_px if rebuy_px is not None else (float(px.iloc[-1]) if len(px) else r["price"])
                gap_days = (rebuy_ts - r["ts"]).total_seconds() / 86400 if rebuy_ts is not None else None
                if lo <= -protect_drop:
                    cat = "protective"
                elif rebuy_ts is not None and gap_days <= whipsaw_days and rebuy_px > r["price"]:
                    cat = "whipsaw"
                else:
                    cat = "neutral"
                rows.append({"symbol": s, "sell_ts": r["ts"], "reason": str(r.get("reason", "")), "strategy": str(r.get("strategy", "")), "qty": r["qty"], "sell_price": r["price"], "avg_entry": avg, "pnl_pct": r["price"] / avg - 1 if avg else np.nan, "net_pnl": r.get("net_pnl"),
                             "hold_days": (r["ts"] - entry_ts).total_seconds() / 86400 if entry_ts is not None else np.nan, "full_exit": bool(qty - r["qty"] <= 1e-9), "rebought": rebuy_ts is not None, "days_to_rebuy": gap_days, "rebuy_vs_sell": (rebuy_px / r["price"] - 1) if rebuy_px else None,
                             "asset_min_after_sell": lo, "asset_max_after_sell": hi, "missed_to_ref": end_ref / r["price"] - 1, "category": cat})
                qty -= r["qty"]; cost = avg * qty if qty > 1e-12 else 0.0
                if qty <= 1e-12:
                    entry_ts = None; qty = 0.0
    return pd.DataFrame(rows)


def summarize(sells: pd.DataFrame, years: float | None = None) -> dict:
    if sells is None or sells.empty:
        return {"n_sells": 0}
    out = {"n_sells": int(len(sells)), "per_year": float(len(sells) / years) if years else None, "median_hold_days": float(sells["hold_days"].median()), "mean_hold_days": float(sells["hold_days"].mean()),
           "share_hold_lt_1d": float((sells["hold_days"] < 1).mean()), "share_hold_lt_7d": float((sells["hold_days"] < 7).mean()), "win_share": float((sells["pnl_pct"] > 0).mean()), "rebuy_share": float(sells["rebought"].mean()),
           "median_days_to_rebuy": float(sells.loc[sells["rebought"], "days_to_rebuy"].median()) if sells["rebought"].any() else None, "rebuy_higher_share": float((sells.loc[sells["rebought"], "rebuy_vs_sell"] > 0).mean()) if sells["rebought"].any() else None,
           "missed_sum": float(sells["missed_to_ref"].sum()), "by_category": {k: {"n": int(len(g)), "share": float(len(g) / len(sells)), "net_pnl": float(g["net_pnl"].sum()) if g["net_pnl"].notna().any() else None, "mean_pnl_pct": float(g["pnl_pct"].mean()), "mean_missed": float(g["missed_to_ref"].mean())} for k, g in sells.groupby("category")},
           "by_reason": {k: {"n": int(len(g)), "mean_pnl_pct": float(g["pnl_pct"].mean()), "win_share": float((g["pnl_pct"] > 0).mean()), "mean_hold_days": float(g["hold_days"].mean())} for k, g in sells.groupby("reason")}}
    if "strategy" in sells and sells["strategy"].str.contains(r"\|").any():
        out["by_strategy"] = {k: {"n": int(len(g)), "mean_pnl_pct": float(g["pnl_pct"].mean()), "win_share": float((g["pnl_pct"] > 0).mean())} for k, g in sells.groupby("strategy")}
    return out


def phase5_tactical_report(p5_dir, close_by_symbol: dict[str, pd.Series], window: str = "full_1h", rounds=("round1", "round2")) -> dict:
    """第五阶段模型 B 战术袖子与原 Brando 的上千笔交易：逐笔（trades.csv，按 sleeve/strategy 归属）+ 卖出后再买间隔（fills.csv）。只读，不修改历史产物。"""
    from pathlib import Path
    out = {}
    for rnd in rounds:
        for s in close_by_symbol:
            px = close_by_symbol[s].copy(); px.index = pd.to_datetime(px.index, utc=True)
            for m in ("B_core_satellite", "BRANDO_original"):
                pt = Path(p5_dir) / f"{window}_{rnd}_{s}_{m}_A_structure_stop_trades.csv"; pf = Path(p5_dir) / f"{window}_{rnd}_{s}_{m}_A_structure_stop_fills.csv"
                if not pt.exists() or not pf.exists():
                    continue
                tr = pd.read_csv(pt); f = pd.read_csv(pf)
                if m == "B_core_satellite":
                    tr = tr[tr["sleeve"] == "tactical"]; f = f[f["strategy"].astype(str).str.startswith("tactical") | f["reason"].astype(str).str.contains("stop|C_rejection", na=False)]
                if tr.empty:
                    continue
                tr = tr.copy(); tr["exit_ts"] = pd.to_datetime(tr["exit_ts"], utc=True); tr["entry_ts"] = pd.to_datetime(tr["entry_ts"], utc=True)
                f = f.copy(); f["ts"] = pd.to_datetime(f["ts"], utc=True); f = f.sort_values("ts")
                buys = f[f["side"] == "BUY"]; sells_f = f[f["side"] == "SELL"]
                # 每次卖出后到下一次买入的间隔与价差
                gaps = []; higher = 0; n_re = 0; cats = {"protective": 0, "whipsaw": 0, "neutral": 0}
                bts = buys["ts"].dt.tz_convert(None).to_numpy().astype("datetime64[ns]"); bpx = buys["price"].to_numpy(float)
                for _, r in sells_f.iterrows():
                    rt = r["ts"].tz_convert(None).to_datetime64().astype("datetime64[ns]"); k = np.searchsorted(bts, rt, side="right")
                    if k < len(bts):
                        n_re += 1; gaps.append(float((bts[k] - rt) / np.timedelta64(1, "D"))); higher += bpx[k] > r["price"]
                        seg = px[(px.index > r["ts"]) & (px.index <= pd.Timestamp(bts[k], tz="UTC"))]
                    else:
                        seg = px[px.index > r["ts"]]
                    lo = float(seg.min() / r["price"] - 1) if len(seg) else 0.0
                    if lo <= -0.10:
                        cats["protective"] += 1
                    elif k < len(bts) and gaps and gaps[-1] <= 30 and bpx[k] > r["price"]:
                        cats["whipsaw"] += 1
                    else:
                        cats["neutral"] += 1
                yrs = (tr["exit_ts"].max() - tr["entry_ts"].min()).total_seconds() / 86400 / 365
                out[f"{rnd}_{s}_{m}"] = {"n_trades": int(len(tr)), "per_year": float(len(tr) / yrs) if yrs > 0 else None, "median_hold_days": float(tr["hold_days"].median()), "mean_hold_days": float(tr["hold_days"].mean()), "share_hold_lt_1d": float((tr["hold_days"] < 1).mean()), "share_hold_lt_7d": float((tr["hold_days"] < 7).mean()),
                                            "win_share": float((tr["net_pnl"] > 0).mean()), "net_pnl_sum": float(tr["net_pnl"].sum()), "fees_sum": float(tr["fee"].sum()) if "fee" in tr else None, "mean_ret_pct": float(tr["ret_pct"].mean()), "by_exit": tr["exit_reason"].value_counts().to_dict(), "by_strategy_net_pnl": tr.groupby("strategy")["net_pnl"].agg(["count", "sum"]).round(2).to_dict("index"),
                                            "n_sells": int(len(sells_f)), "rebuy_share": float(n_re / len(sells_f)) if len(sells_f) else None, "median_days_to_rebuy": float(np.median(gaps)) if gaps else None, "rebuy_higher_share": float(higher / n_re) if n_re else None, "by_category": cats}
    return out
