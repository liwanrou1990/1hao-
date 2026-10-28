"""可信度验证：按 BTC / ETH 分别统计真实样本外结果，与随机 / 买入持有 / 均线三个基准比较，
并把“预测盈利概率”与“同期市场真实盈利率”分开，避免把市场自身上涨概率当成 AI 能力。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import cfg, report_dir
from ..decision import decide_batch
from ..models.calibration import classification_report
from .evaluate import block_bootstrap_ci, regime_at, simulate_decisions, summarize_signals


def _max_consecutive(x: np.ndarray) -> int:
    best = cur = 0
    for v in x:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def _nonoverlap_runs(sig: pd.DataFrame, step: int = 90) -> dict:
    """每 90 天取一个信号（多个偏移），统计最大连续亏损与胜率（避免重叠样本夸大连败）。"""
    outs = []
    g = sig.sort_values("ts")
    for off in range(0, step, 15):
        sub = g.iloc[off::step]
        pos = sub[sub["filled"]]
        if len(pos) >= 3:
            outs.append({"max_consec_losses": _max_consecutive((pos["realized_ret"] <= 0).to_numpy()), "n_positions": len(pos),
                         "win_rate": float((pos["realized_ret"] > 0).mean()), "bh_max_consec_losses": _max_consecutive((sub["ret_A"] <= 0).to_numpy())})
    if not outs:
        return {}
    d = pd.DataFrame(outs)
    return {"max_consec_losses_median": float(d["max_consec_losses"].median()), "max_consec_losses_max": int(d["max_consec_losses"].max()),
            "n_positions_median": float(d["n_positions"].median()), "win_rate_mean": float(d["win_rate"].mean()),
            "bh_max_consec_losses_median": float(d["bh_max_consec_losses"].median())}


def direction_and_calibration(a: pd.DataFrame, wide: pd.DataFrame, prob_col: str = "p_profit_cal") -> dict:
    y = wide.loc[a.index, "profit_A"].to_numpy(float)
    p = a[prob_col].to_numpy(float)
    rep = classification_report(p, y, base_rate=float(y.mean()))
    pred_up = p >= 0.5
    acc = float((pred_up == (y == 1)).mean())
    # 按预测概率分桶：预测均值 / 真实盈利率 / 同期（同折）市场盈利率 / 提升
    fold_base = wide.loc[a.index].assign(fold=a["fold"].to_numpy()).groupby("fold")["profit_A"].transform("mean").to_numpy()
    bins = pd.cut(p, [0, 0.35, 0.5, 0.65, 0.8, 1.0001], right=False, labels=["<35%", "35-50%", "50-65%", "65-80%", "≥80%"])
    tab = pd.DataFrame({"bin": bins, "p": p, "y": y, "base": fold_base, "ret": wide.loc[a.index, "ret_A"].to_numpy()}).groupby("bin", observed=False).agg(
        n=("y", "size"), pred_mean=("p", "mean"), realized_win_rate=("y", "mean"), same_period_market_win_rate=("base", "mean"), mean_ret_A=("ret", "mean"))
    tab["prob_error"] = tab["pred_mean"] - tab["realized_win_rate"]
    tab["lift_vs_market"] = tab["realized_win_rate"] - tab["same_period_market_win_rate"]
    hi = p >= 0.65
    hi_stats = {"n_days": int(hi.sum()), "pred_mean": float(p[hi].mean()) if hi.any() else None, "realized_win_rate": float(y[hi].mean()) if hi.any() else None,
                "same_period_market_win_rate": float(fold_base[hi].mean()) if hi.any() else None,
                "mean_ret_A": float(wide.loc[a.index[hi], "ret_A"].mean()) if hi.any() else None,
                "realized_win_rate_ci95_blockboot": list(block_bootstrap_ci(y[hi])) if hi.sum() > 180 else None,
                "share_of_days": float(hi.mean())}
    return {"direction_accuracy": acc, "always_up_accuracy": float(y.mean()), "n": int(len(y)), "report": rep,
            "bins": tab.reset_index().to_dict(orient="records"), "ge65": hi_stats}


def benchmarks(wide_s: pd.DataFrame, X_s: pd.DataFrame, oos_index, thr: float, seed: int = 7) -> dict:
    """同一样本外日期上的三个基准：随机（50% 买入）、每天买入持有、均线过滤（MA200 / MA50）。"""
    w = wide_s.loc[oos_index]; x = X_s.loc[oos_index]
    rng = np.random.default_rng(seed)
    res = {}

    def _stat(mask: np.ndarray, name: str):
        r = w["ret_A"].to_numpy(); m = w["mae_A"].to_numpy()
        pos = mask.astype(bool)
        res[name] = {"n_positions": int(pos.sum()), "share_days_invested": float(pos.mean()),
                     "positions_win_rate": float((r[pos] > 0).mean()) if pos.any() else None,
                     "positions_mean_ret": float(r[pos].mean()) if pos.any() else None,
                     "positions_median_ret": float(np.median(r[pos])) if pos.any() else None,
                     "positions_p_mae_gt_thr": float((m[pos] < -thr).mean()) if pos.any() else None,
                     "mean_ret_all_days": float(np.where(pos, r, 0).mean()),
                     "max_consec_losses_overlapping": _max_consecutive((r[pos] <= 0)) if pos.any() else 0}
    _stat(np.ones(len(w)), "buy_and_hold_every_day")
    accs = []; rets = []
    for k in range(20):
        rm = rng.random(len(w)) < 0.5
        accs.append(float(((rng.random(len(w)) < 0.5) == (w["profit_A"].to_numpy() == 1)).mean()))
        _stat(rm, f"_rand{k}"); rets.append(res.pop(f"_rand{k}"))
    rd = pd.DataFrame(rets)
    res["random_50pct"] = {k: float(rd[k].mean()) for k in rd.columns}
    res["random_50pct"]["direction_accuracy_random"] = float(np.mean(accs))
    _stat((x["ma200_dist"] > 0).to_numpy(), "ma200_filter")
    _stat(((x["ma200_dist"] > 0) & (x["ma200_slope30"] > 0)).to_numpy(), "ma200_up_trend")
    _stat((x["ma50_dist"] > 0).to_numpy(), "ma50_filter")
    # 波动率过滤：rv20 低于过去 365 天 rv20 中位数（决策日可知）才买
    rv_med = x["rv20"].rolling(365, min_periods=120).median()
    _stat((x["rv20"] < rv_med).to_numpy(), "low_vol_filter")
    _stat(((x["rv20"] < rv_med) & (x["ma200_dist"] > 0)).to_numpy(), "low_vol_and_ma200")
    return res


def limit_order_analysis(b: pd.DataFrame, long: pd.DataFrame, sig: pd.DataFrame, wide: pd.DataFrame, thr: float) -> dict:
    """限价单：预测成交概率 vs 真实成交率、成交后收益预测 vs 真实；固定折价基准 vs 模型选价；未成交机会成本。"""
    L = long.loc[b.index]
    tab = pd.DataFrame({"d_pct": L["d_pct"].to_numpy(), "p_fill": b["p_fill_cal"].to_numpy(float), "filled": L["filled"].to_numpy(float),
                        "p_profit": b["p_profit_cal"].to_numpy(float), "profit": L["profit_B"].to_numpy(float), "pred_ret": b["ret_q50"].to_numpy(float),
                        "ret": L["ret_B"].to_numpy(float), "ret_A": L["ret_A_counterfactual"].to_numpy(float), "mae": L["mae_B"].to_numpy(float)})
    g = tab.groupby("d_pct")
    fixed = pd.DataFrame({"pred_fill": g["p_fill"].mean(), "real_fill": g["filled"].mean(), "pred_profit_given_fill": g.apply(lambda x: x.loc[x.filled == 1, "p_profit"].mean()),
                          "real_profit_given_fill": g["profit"].mean(), "pred_ret_median_given_fill": g.apply(lambda x: x.loc[x.filled == 1, "pred_ret"].median()),
                          "real_ret_mean_given_fill": g["ret"].mean(), "real_ret_median_given_fill": g["ret"].median(),
                          "real_p_mae_gt_thr_given_fill": g.apply(lambda x: (x.loc[x.filled == 1, "mae"] < -thr).mean()),
                          "ev_all_days_fill_inclusive": g.apply(lambda x: np.where(x.filled == 1, x.ret, 0).mean()),
                          "buy_now_mean_ret_same_days": g["ret_A"].mean(),
                          "missed_ret_A_when_unfilled": g.apply(lambda x: x.loc[x.filled == 0, "ret_A"].mean())}).reset_index()
    # 模型选择 B 的那些日子：成交含未成交的真实收益 vs 当天立即买入
    wait = sig[sig["action"] == "等待"]
    buy = sig[sig["action"] == "买入"]
    comp = {"n_wait_days": int(len(wait)), "wait_fill_rate": float(wait["filled"].mean()) if len(wait) else None,
            "wait_fill_inclusive_mean_ret": float(wait["realized_ret"].mean()) if len(wait) else None,
            "wait_buy_now_mean_ret_same_days": float(wait["ret_A"].mean()) if len(wait) else None,
            "wait_filled_positions_mean_ret": float(wait.loc[wait["filled"], "realized_ret"].mean()) if wait["filled"].any() else None,
            "wait_filled_positions_win_rate": float((wait.loc[wait["filled"], "realized_ret"] > 0).mean()) if wait["filled"].any() else None,
            "wait_missed_ret_when_unfilled": float(wait.loc[~wait["filled"], "ret_A"].mean()) if (~wait["filled"]).any() else None,
            "wait_avg_discount_pct": float(wait["d_pct"].mean()) if len(wait) else None,
            "wait_avg_fill_px_vs_close": float((wait.loc[wait["filled"], "fill_px"] / wait.loc[wait["filled"], "close"] - 1).mean()) if wait["filled"].any() else None,
            "n_buy_now_days": int(len(buy)), "buy_now_mean_ret": float(buy["ret_A"].mean()) if len(buy) else None,
            "buy_now_win_rate": float((buy["ret_A"] > 0).mean()) if len(buy) else None,
            "diff_wait_vs_buynow_same_days_ci95": list(block_bootstrap_ci((wait["realized_ret"] - wait["ret_A"]).to_numpy())) if len(wait) > 180 else None}
    return {"fixed_discount_table": fixed.to_dict(orient="records"), "model_choice": comp}


def per_symbol(symbol: str, ds: dict, wf_predA: dict, wf_predB: dict, signals: dict, models: list[str], thr: float) -> dict:
    wide, long, X = ds["wide"], ds["long"], ds["X"]
    out = {"symbol": symbol, "models": {}}
    first = True
    for m in models:
        a = wf_predA[m]; a = a[a["symbol"] == symbol]
        b = wf_predB[m]; b = b[b["symbol"] == symbol]
        sig = signals[m]; sig = sig[sig["symbol"] == symbol]
        r = {"direction_calibration": direction_and_calibration(a, wide), "strategy": summarize_signals(sig, thr),
             "nonoverlap_runs": _nonoverlap_runs(sig), "max_consec_losses_overlapping": _max_consecutive((sig.loc[sig["filled"], "realized_ret"] <= 0).to_numpy()),
             "limit_orders": limit_order_analysis(b, long, sig, wide, thr)}
        sg = sig.copy(); sg["year"] = pd.to_datetime(sg["ts"]).dt.year
        r["by_regime"] = {k: _grp(v, thr) for k, v in sg.groupby("regime")}
        r["by_year"] = {str(k): _grp(v, thr) for k, v in sg.groupby("year")}
        out["models"][m] = r
        if first:
            out["benchmarks"] = benchmarks(wide, X, a.index, thr)
            out["oos_days"] = int(len(a)); out["oos_range"] = [str(a["ts"].min().date()), str(a["ts"].max().date())]
            first = False
    return out


def _grp(g: pd.DataFrame, thr: float) -> dict:
    pos = g[g["filled"]]
    return {"n_days": int(len(g)), "n_buy": int((g["action"] == "买入").sum()), "n_wait": int((g["action"] == "等待").sum()), "n_cash": int((g["action"] == "暂不买入").sum()),
            "n_filled": int(len(pos)), "positions_win_rate": float((pos["realized_ret"] > 0).mean()) if len(pos) else None,
            "positions_mean_ret": float(pos["realized_ret"].mean()) if len(pos) else None, "strategy_mean_ret_all_days": float(g["realized_ret"].mean()),
            "buyhold_win_rate": float((g["ret_A"] > 0).mean()), "buyhold_mean_ret": float(g["ret_A"].mean()), "buyhold_p_mae_gt_thr": float((g["mae_A"] < -thr).mean()),
            "positions_p_mae_gt_thr": float((pos["realized_mae"] < -thr).mean()) if len(pos) else None}


def signal_table(symbol: str, sig: pd.DataFrame, a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """每个信号：预测概率 / 预测收益 / 真实结果 / 概率误差。"""
    s = sig[sig["symbol"] == symbol].copy()
    a = a[a["symbol"] == symbol]
    s["pred_p_profit_A"] = a.loc[s.index, "p_profit_cal"].to_numpy(); s["pred_ret_q50_A"] = a.loc[s.index, "ret_q50"].to_numpy()
    s["pred_p_dd_A"] = a.loc[s.index, "p_dd_cal"].to_numpy()
    bb = b[b["symbol"] == symbol].set_index(["ts", "d_pct"])
    pp, pf, pr = [], [], []
    for _, r in s.iterrows():
        if r["action"] == "等待":
            row = bb.loc[(r["ts"], float(r["d_pct"]))]
            pp.append(row["p_profit_cal"]); pf.append(row["p_fill_cal"]); pr.append(row["ret_q50"])
        else:
            pp.append(r["pred_p_profit_A"] if r["action"] == "买入" else np.nan); pf.append(1.0 if r["action"] == "买入" else np.nan); pr.append(r["pred_ret_q50_A"] if r["action"] == "买入" else np.nan)
    s["pred_p_profit_chosen"], s["pred_p_fill_chosen"], s["pred_ret_q50_chosen"] = pp, pf, pr
    s["actual_profit_A"] = (s["ret_A"] > 0).astype(int)
    s["prob_error_A"] = s["pred_p_profit_A"] - s["actual_profit_A"]
    s["brier_A"] = s["prob_error_A"] ** 2
    cols = ["ts", "symbol", "fold", "regime", "close", "action", "d_pct", "pred_p_fill_chosen", "pred_p_profit_chosen", "pred_ret_q50_chosen", "filled", "fill_px",
            "fill_offset_days", "realized_ret", "realized_mae", "realized_mdd", "ret_A", "mae_A", "actual_profit_A", "pred_p_profit_A", "pred_ret_q50_A", "pred_p_dd_A", "prob_error_A", "brier_A"]
    return s[cols]
