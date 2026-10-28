"""样本外评估：
1. 概率质量：AUC / Brier / Brier 技能(相对基准频率) / ECE / 可靠性表；分位数覆盖率与 pinball。
2. 决策模拟：每个样本外决策日按决策规则选 A/B(d)/C，用真实标签记录“是否成交、90 天真实净收益、最大浮亏/峰谷回撤”。
3. 与“每天都买入持有 90 天”对比；块自助（块长 90 天）置信区间处理重叠样本相关性；非重叠子样本（每 90 天取 1 个）复核。
4. 分年份、分市场状态（决策日可知：牛/熊/震荡）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg
from ..decision import decide_batch
from ..models.calibration import classification_report, quantile_report


def regime_at(X: pd.DataFrame) -> pd.Series:
    """决策日可知的市场状态：close>MA200 且 MA200 上行 = 上涨；close<MA200 且 MA200 下行 = 下跌；其他 = 震荡。"""
    up = (X["ma200_dist"] > 0) & (X["ma200_slope30"] > 0)
    dn = (X["ma200_dist"] < 0) & (X["ma200_slope30"] < 0)
    return pd.Series(np.where(up, "上涨", np.where(dn, "下跌", "震荡")), index=X.index)


def block_bootstrap_ci(x: np.ndarray, block: int = 90, n: int = 500, seed: int = 42, stat=np.mean) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < block * 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(x) / block))
    stats = []
    for _ in range(n):
        starts = rng.integers(0, len(x) - block + 1, size=nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[: len(x)]
        stats.append(stat(x[idx]))
    return (float(np.quantile(stats, 0.025)), float(np.quantile(stats, 0.975)))


def probability_quality(wf: dict, wide: pd.DataFrame, long: pd.DataFrame) -> dict:
    out = {}
    for m, a in wf["predA"].items():
        b = wf["predB"][m]
        yA_p, yA_d = wide.loc[a.index, "profit_A"].to_numpy(), wide.loc[a.index, "dd_A_flag"].to_numpy()
        base_p, base_d = float(yA_p.mean()), float(yA_d.mean())
        filled = long.loc[b.index, "filled"].to_numpy() == 1
        r = {
            "A_profit_raw": classification_report(a["p_profit"].to_numpy(dtype=float), yA_p, base_p),
            "A_profit_cal": classification_report(a["p_profit_cal"].to_numpy(dtype=float), yA_p, base_p),
            "A_dd_raw": classification_report(a["p_dd"].to_numpy(dtype=float), yA_d, base_d),
            "A_dd_cal": classification_report(a["p_dd_cal"].to_numpy(dtype=float), yA_d, base_d),
            "A_quantiles": quantile_report(*(a[f"ret_q{q}"].to_numpy(dtype=float) for q in ("05", "25", "50", "75", "95")), wide.loc[a.index, "ret_A"].to_numpy()),
            "B_fill_raw": classification_report(b["p_fill"].to_numpy(dtype=float), long.loc[b.index, "filled"].to_numpy(dtype=float)),
            "B_fill_cal": classification_report(b["p_fill_cal"].to_numpy(dtype=float), long.loc[b.index, "filled"].to_numpy(dtype=float)),
            "B_profit_given_fill_cal": classification_report(b.loc[filled, "p_profit_cal"].to_numpy(dtype=float), long.loc[b.index[filled], "profit_B"].to_numpy(dtype=float)),
            "B_dd_given_fill_cal": classification_report(b.loc[filled, "p_dd_cal"].to_numpy(dtype=float), long.loc[b.index[filled], "dd_B_flag"].to_numpy(dtype=float)),
        }
        ra = a.loc[:, "ret_mean"].to_numpy(dtype=float); ya = wide.loc[a.index, "ret_A"].to_numpy()
        r["A_ret_mean_mae"] = float(np.nanmean(np.abs(ra - ya)))
        r["A_ret_q50_mae"] = float(np.nanmean(np.abs(a["ret_q50"].to_numpy(dtype=float) - ya)))
        out[m] = r
    return out


def simulate_decisions(wf: dict, ds: dict, model: str, use_cal: bool = True) -> pd.DataFrame:
    """对每个样本外决策日执行决策规则，并用真实标签记录结果。"""
    a = wf["predA"][model].copy(); b = wf["predB"][model].copy()
    wide, long, X = ds["wide"], ds["long"], ds["X"]
    if use_cal:
        for col in ("p_profit", "p_dd"):
            a[col] = a[col + "_cal"]
        for col in ("p_fill", "p_profit", "p_dd"):
            b[col] = b[col + "_cal"]
    keys = a[["symbol", "ts"]]
    dec = decide_batch(a["close"].to_numpy(dtype=float), a, b, keys)
    sig = pd.concat([keys, a[["close", "fold"]], dec], axis=1)
    sig["regime"] = regime_at(X.loc[sig.index])
    w = wide.loc[sig.index]
    sig["ret_A"], sig["mdd_A"], sig["mae_A"] = w["ret_A"].to_numpy(), w["mdd_A"].to_numpy(), w["mae_A"].to_numpy()
    # B 结果查表
    lb = long.set_index(["symbol", "ts", "d_pct"])
    ret = np.zeros(len(sig)); filled = np.zeros(len(sig), dtype=bool); entered = np.zeros(len(sig), dtype=bool)
    mdd = np.full(len(sig), np.nan); mae = np.full(len(sig), np.nan); fill_px = np.full(len(sig), np.nan); fill_off = np.full(len(sig), np.nan)
    for i, (idx, r) in enumerate(sig.iterrows()):
        if r["action"] == "买入":
            ret[i], mdd[i], mae[i], entered[i], filled[i] = r["ret_A"], r["mdd_A"], r["mae_A"], True, True
            fill_px[i] = w.loc[idx, "entry_A"]; fill_off[i] = 1
        elif r["action"] == "等待":
            row = lb.loc[(r["symbol"], r["ts"], float(r["d_pct"]))]
            if int(row["filled"]) == 1:
                ret[i], mdd[i], mae[i], entered[i], filled[i] = row["ret_B"], row["mdd_B"], row["mae_B"], True, True
                fill_px[i], fill_off[i] = row["fill_px"], row["fill_offset"]
            else:
                entered[i] = True       # 下了单但未成交
    sig["signal"] = sig["action"].map({"买入": "A_立即买入", "等待": "B_限价等待", "暂不买入": "C_观望"})
    sig["order_placed"] = entered; sig["filled"] = filled; sig["fill_px"] = fill_px; sig["fill_offset_days"] = fill_off
    sig["realized_ret"] = np.where(filled, ret, 0.0); sig["realized_mdd"] = mdd; sig["realized_mae"] = mae
    sig["missed_ret_A_if_unfilled"] = np.where(entered & ~filled, sig["ret_A"], np.nan)
    return sig


def summarize_signals(sig: pd.DataFrame, thr: float) -> dict:
    c = cfg()["backtest"]
    pos = sig[sig["filled"]]
    bh = sig["ret_A"]
    diff = sig["realized_ret"] - bh
    lo, hi = block_bootstrap_ci(diff.to_numpy(), int(c["block_bootstrap_len"]), int(c["bootstrap_n"]), int(c["seed"]))
    # 非重叠子样本：每 90 天取 1 个（多个偏移取平均）
    non_overlap = []
    for sym, g in sig.groupby("symbol"):
        g = g.sort_values("ts")
        for off in range(0, 90, 30):
            sub = g.iloc[off::90]
            if len(sub) > 5:
                p = sub[sub["filled"]]
                non_overlap.append({"win_rate_positions": p["realized_ret"].gt(0).mean() if len(p) else np.nan, "mean_ret_all_days": sub["realized_ret"].mean(),
                                    "bh_win_rate": sub["ret_A"].gt(0).mean(), "bh_mean_ret": sub["ret_A"].mean()})
    no = pd.DataFrame(non_overlap).mean().to_dict() if non_overlap else {}
    return {
        "n_days": int(len(sig)), "n_buy_now": int((sig["action"] == "买入").sum()), "n_wait": int((sig["action"] == "等待").sum()),
        "n_cash": int((sig["action"] == "暂不买入").sum()), "n_filled": int(sig["filled"].sum()),
        "fill_rate_of_wait": float(sig.loc[sig["action"] == "等待", "filled"].mean()) if (sig["action"] == "等待").any() else None,
        "positions_win_rate": float((pos["realized_ret"] > 0).mean()) if len(pos) else None,
        "positions_mean_ret": float(pos["realized_ret"].mean()) if len(pos) else None,
        "positions_median_ret": float(pos["realized_ret"].median()) if len(pos) else None,
        "positions_mean_mae": float(pos["realized_mae"].mean()) if len(pos) else None,
        "positions_mean_mdd": float(pos["realized_mdd"].mean()) if len(pos) else None,
        "positions_p_mae_gt_thr": float((pos["realized_mae"] < -thr).mean()) if len(pos) else None,
        "positions_p_mdd_gt_thr": float((pos["realized_mdd"] < -thr).mean()) if len(pos) else None,
        "strategy_mean_ret_all_days": float(sig["realized_ret"].mean()),
        "buyhold_win_rate": float((bh > 0).mean()), "buyhold_mean_ret": float(bh.mean()), "buyhold_median_ret": float(bh.median()),
        "buyhold_mean_mae": float(sig["mae_A"].mean()), "buyhold_p_mae_gt_thr": float((sig["mae_A"] < -thr).mean()),
        "buyhold_p_mdd_gt_thr": float((sig["mdd_A"] < -thr).mean()),
        "diff_vs_buyhold_mean": float(diff.mean()), "diff_ci95_blockboot": [lo, hi],
        "missed_mean_ret_when_unfilled": float(sig["missed_ret_A_if_unfilled"].mean()) if sig["missed_ret_A_if_unfilled"].notna().any() else None,
        "non_overlapping_subsample": {k: (None if pd.isna(v) else float(v)) for k, v in no.items()},
    }


def breakdowns(sig: pd.DataFrame, thr: float) -> dict:
    out = {"by_year": {}, "by_regime": {}, "by_symbol": {}}
    sig = sig.copy(); sig["year"] = pd.to_datetime(sig["ts"]).dt.year
    for key, col in (("by_year", "year"), ("by_regime", "regime"), ("by_symbol", "symbol")):
        for v, g in sig.groupby(col):
            pos = g[g["filled"]]
            out[key][str(v)] = {"n_days": int(len(g)), "n_buy": int((g["action"] == "买入").sum()), "n_wait": int((g["action"] == "等待").sum()),
                                "n_filled": int(g["filled"].sum()), "positions_win_rate": float((pos["realized_ret"] > 0).mean()) if len(pos) else None,
                                "positions_mean_ret": float(pos["realized_ret"].mean()) if len(pos) else None,
                                "strategy_mean_ret_all_days": float(g["realized_ret"].mean()), "buyhold_win_rate": float((g["ret_A"] > 0).mean()),
                                "buyhold_mean_ret": float(g["ret_A"].mean()), "buyhold_p_mae_gt_thr": float((g["mae_A"] < -thr).mean())}
    return out


def evaluate_all(wf: dict, ds: dict) -> dict:
    thr = float(cfg()["thresholds"]["drawdown_threshold"])
    res = {"probability_quality": probability_quality(wf, ds["wide"], ds["long"]), "decision": {}, "folds": wf["folds"]}
    signals = {}
    for m in wf["predA"]:
        sig = simulate_decisions(wf, ds, m, use_cal=True)
        signals[m] = sig
        res["decision"][m] = {"summary": summarize_signals(sig, thr), **breakdowns(sig, thr)}
    res["_signals"] = signals
    return res
