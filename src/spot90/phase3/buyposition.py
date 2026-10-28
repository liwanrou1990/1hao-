"""第五节：以 90 天现货持有为目标的买入位置研究。所有规则在相同决策日、相同资金口径（每个决策日 1 份资金；现金 = 0 收益；
含手续费、滑点与未成交机会成本）下比较；短周期 AI 信号只用预先指定的 A 组 LightGBM Platt 校准概率，不做事后挑选。

规则（事前固定）：
 R1 立即买入持有 90 天
 R2/R3/R4 14 天内等待 1% / 3% / 5% 回调成交后持有 90 天，未成交 = 现金
 R5 趋势：收盘 > MA200 且 MA200 30 日上行 才立即买入，否则现金
 R6 趋势 + 低波动：R5 且 rv20 低于过去 365 天中位数
 R7 AI-7 天：7 天上涨概率 ≥ 0.5 立即买入，否则等待 3% 回调
 R8 AI-30 天 + 风险：30 天上涨概率 ≥ 0.5 且 90 天浮亏>15% 概率 ≤ 0.5 才立即买入，否则现金
 R9 第一阶段 90 天 AI 决策规则（A 组 LightGBM，等渗校准）"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.dataset import build_dataset, load_bars
from ..config import report_dir
from .analysis import block_ci
from .runner import SELECTION_END

H = 90


def _tranche_equity_events(entries: pd.DataFrame, close: pd.Series) -> dict:
    """entries: DataFrame[entry_ts] —— 每个事件从 entry_ts 起持有 H 天、占 1/H 份资金（总敞口上限 1）。"""
    c = close.sort_index(); lr = np.log(c).diff().fillna(0)
    cnt = pd.Series(0.0, index=c.index)
    if len(entries):
        ev = entries["entry_ts"].dt.floor("D").value_counts()
        cnt = cnt.add(ev.reindex(c.index).fillna(0), fill_value=0)
    exposure = (cnt.rolling(H, min_periods=1).sum() / H).clip(upper=1.0)
    port = exposure * lr
    eq = np.exp(port.cumsum()); n_years = max(len(port) / 365.25, 1e-9)
    return {"ann_return": float(eq.iloc[-1] ** (1 / n_years) - 1), "ann_vol": float(port.std() * np.sqrt(365)),
            "sharpe": float(port.mean() / port.std() * np.sqrt(365)) if port.std() > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()), "avg_exposure": float(exposure.mean())}


def run() -> dict:
    bars = load_bars(); ds = build_dataset(bars)
    wide, long, X = ds["wide"], ds["long"], ds["X"]
    pdir = report_dir() / "phase3" / "preds"
    p7 = pd.read_parquet(pdir / "H7_A_lightgbm.parquet"); p30 = pd.read_parquet(pdir / "H30_A_lightgbm.parquet"); p90 = pd.read_parquet(pdir / "H90_A_lightgbm.parquet")
    for p in (p7, p30, p90):
        p["ts"] = pd.to_datetime(p["ts"], utc=True)
    sig9 = pd.read_parquet(report_dir() / "phase2" / "signals_A_tech_lightgbm.parquet"); sig9["ts"] = pd.to_datetime(sig9["ts"], utc=True)
    lb = long.pivot_table(index=["symbol", "ts"], columns="d_pct", values=["filled", "ret_B", "mae_B", "fill_offset"])
    out = {"rules": {}, "n": {}}
    rules = ["R1_立即买入", "R2_等待1%", "R3_等待3%", "R4_等待5%", "R5_趋势", "R6_趋势+低波动", "R7_AI7天", "R8_AI30天+风险", "R9_阶段一AI规则"]
    for sym in ("BTCUSDT", "ETHUSDT"):
        w = wide[wide["symbol"] == sym].merge(p7[p7.symbol == sym][["ts", "p_profit_cal"]].rename(columns={"p_profit_cal": "p7"}), on="ts", how="inner") \
            .merge(p30[p30.symbol == sym][["ts", "p_profit_cal"]].rename(columns={"p_profit_cal": "p30"}), on="ts", how="inner") \
            .merge(p90[p90.symbol == sym][["ts", "p_mae15_cal", "p_profit_cal"]].rename(columns={"p_mae15_cal": "pm15_90", "p_profit_cal": "p90"}), on="ts", how="inner") \
            .merge(sig9[sig9.symbol == sym][["ts", "realized_ret", "filled", "fill_offset_days", "realized_mae"]].rename(columns={"realized_ret": "r9", "filled": "f9", "fill_offset_days": "off9", "realized_mae": "mae9"}), on="ts", how="left")
        xx = X.loc[wide.index[wide["symbol"] == sym]].set_index("ts").reindex(w["ts"])
        rv_med = xx["rv20"].rolling(365, min_periods=120).median()
        trend = ((xx["ma200_dist"] > 0) & (xx["ma200_slope30"] > 0)).to_numpy()
        lowv = (xx["rv20"] < rv_med).to_numpy()
        L = lb.loc[sym].reindex(w["ts"])
        close = bars[sym]["1d"].set_index("ts")["close"]

        def wait_outcome(d):
            f = L[("filled", float(d))].to_numpy() == 1
            r = np.where(f, L[("ret_B", float(d))].to_numpy(), 0.0); mae = np.where(f, L[("mae_B", float(d))].to_numpy(), np.nan)
            off = np.where(f, L[("fill_offset", float(d))].to_numpy(), np.nan)
            return f, r, mae, off

        ra, maea = w["ret_A"].to_numpy(), w["mae_A"].to_numpy()
        ones = np.ones(len(w), bool)
        res_rules = {}
        f1, f3, f5 = wait_outcome(1), wait_outcome(3), wait_outcome(5)
        spec = {
            "R1_立即买入": (ones, ones, ra, maea, np.ones(len(w))),
            "R2_等待1%": (ones, f1[0], f1[1], f1[2], f1[3]), "R3_等待3%": (ones, f3[0], f3[1], f3[2], f3[3]), "R4_等待5%": (ones, f5[0], f5[1], f5[2], f5[3]),
            "R5_趋势": (trend, trend, np.where(trend, ra, 0), np.where(trend, maea, np.nan), np.where(trend, 1, np.nan)),
            "R6_趋势+低波动": (trend & lowv, trend & lowv, np.where(trend & lowv, ra, 0), np.where(trend & lowv, maea, np.nan), np.where(trend & lowv, 1, np.nan)),
        }
        ai7 = (w["p7"].to_numpy() >= 0.5)
        r7 = np.where(ai7, ra, f3[1]); fl7 = np.where(ai7, True, f3[0]); mae7 = np.where(ai7, maea, f3[2]); off7 = np.where(ai7, 1, f3[3])
        spec["R7_AI7天"] = (ones, fl7, r7, mae7, off7)
        ai8 = (w["p30"].to_numpy() >= 0.5) & (w["pm15_90"].to_numpy() <= 0.5)
        spec["R8_AI30天+风险"] = (ai8, ai8, np.where(ai8, ra, 0), np.where(ai8, maea, np.nan), np.where(ai8, 1, np.nan))
        f9 = w["f9"].fillna(False).to_numpy(bool)
        spec["R9_阶段一AI规则"] = (w["r9"].notna().to_numpy() & (w["f9"].notna().to_numpy()), f9, w["r9"].fillna(0).to_numpy(), np.where(f9, w["mae9"], np.nan), np.where(f9, w["off9"], np.nan))
        for period, pm in (("selection", (w["ts"] < SELECTION_END).to_numpy()), ("holdout", (w["ts"] >= SELECTION_END).to_numpy())):
            out["n"][f"{sym}|{period}"] = int(pm.sum())
            for name, (ordered, filled, r, mae, off) in spec.items():
                o, f, rr, mm = ordered & pm, filled & pm, np.where(pm, r, 0.0), mae
                pos = f
                entries = pd.DataFrame({"entry_ts": pd.to_datetime(w.loc[pos, "ts"].reset_index(drop=True), utc=True) + pd.to_timedelta(np.nan_to_num(off[pos], nan=1).astype(int), unit="D")})
                cl = close[(close.index >= w.loc[pm, "ts"].min()) & (close.index <= w.loc[pm, "ts"].max() + pd.Timedelta(days=H + 15))]
                res_rules.setdefault(name, {})[period] = {
                    "share_ordered": float(o.sum() / pm.sum()), "n_filled": int(pos.sum()), "fill_rate_of_orders": float(pos.sum() / o.sum()) if o.sum() else None,
                    "positions_win_rate": float((rr[pos] > 0).mean()) if pos.any() else None, "positions_mean_ret": float(rr[pos].mean()) if pos.any() else None,
                    "positions_median_ret": float(np.median(rr[pos])) if pos.any() else None, "positions_mean_mae": float(np.nanmean(mm[pos])) if pos.any() else None,
                    "positions_p_mae15": float((mm[pos] < -0.15).mean()) if pos.any() else None,
                    "all_days_mean_ret": float(rr[pm].mean()), "missed_when_unfilled": float(ra[o & ~f].mean()) if (o & ~f).any() else None,
                    "diff_vs_R1": block_ci(rr[pm] - ra[pm], 100), "equity": _tranche_equity_events(entries, cl)}
        out["rules"][sym] = res_rules
    import json
    (report_dir() / "phase3" / "buyposition.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else float(o)))
    return out
