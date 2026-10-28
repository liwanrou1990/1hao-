"""标签（只在回测/训练中使用未来数据，预测时绝不使用）。

决策时点：第 i 天收盘。
A 立即买入：第 i+1 天开盘价 × (1+市价成本) 买入，持有 H 天（第 i+1 … i+H 天），第 i+H 天收盘 × (1−成本) 卖出。
B 限价等待：限价 = 第 i 天收盘 × (1−d)。第 i+1 … i+W 天内任一天最低价 ≤ 限价即成交（成交价 = min(限价, 当日开盘)，
   跳空低开按开盘价成交），成交日 j 起持有 H 天（第 j … j+H−1 天），第 j+H−1 天收盘卖出。W 天内未触及 → 未成交。
回撤：peak-to-trough（峰值用买入价与持有期收盘价的累计最高，谷值用当日最低价）；另给 mae = 持有期最低价相对买入价的最大浮亏。
注意：这里使用未来最低价只是为了“事后判定是否成交/评估结果”，候选价格网格本身由决策日信息决定，不存在用未来最低价倒推买点。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .config import cfg


def cost_fractions() -> dict[str, float]:
    c = cfg()["costs"]
    return {"market_in": (c["taker_fee_bps"] + c["slippage_bps_market"]) / 1e4,
            "limit_in": (c["maker_fee_bps"] + c["slippage_bps_limit"]) / 1e4,
            "out": (c["taker_fee_bps"] + c["slippage_bps_market"]) / 1e4}


def _hold_stats(entry_px: np.ndarray, k: np.ndarray, c: np.ndarray, l: np.ndarray, H: int, cost_in: float, cost_out: float):
    """从第 k 天起持有 H 天：净收益、峰谷回撤、最大浮亏。k 可为任意索引数组（要求 k+H-1 < n）。"""
    J = k[:, None] + np.arange(H)[None, :]
    cw, lw = c[J], l[J]
    exit_px = cw[:, -1]
    ret = exit_px * (1 - cost_out) / (entry_px * (1 + cost_in)) - 1
    peak = np.maximum.accumulate(cw, axis=1)
    peak_prev = np.concatenate([entry_px[:, None], peak[:, :-1]], axis=1)
    peak_prev = np.maximum(peak_prev, entry_px[:, None])
    mdd = (lw / peak_prev - 1).min(axis=1)
    mae = lw.min(axis=1) / entry_px - 1
    return ret, mdd, mae


def build_labels(bars: pd.DataFrame, H: int | None = None, W: int | None = None, grid_pct: list[float] | None = None,
                 dd_threshold: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (wide_A, long_B)。
    wide_A: 每个决策日一行：ret_A, mdd_A, mae_A, profit_A, dd15_A, ret_after_wait(等待 W 天后收盘相对决策日收盘)。
    long_B: 每个 (决策日, d) 一行：limit_px, filled, fill_offset, fill_px, ret_B, mdd_B, mae_B, profit_B, dd15_B。
    只保留标签完整的决策日（i ≤ n−W−H）。"""
    c0 = cfg()
    H = H or int(c0["horizon_days"]); W = W or int(c0["wait_days"])
    grid_pct = grid_pct or list(c0["limit_order"]["discount_grid_pct"])
    thr = dd_threshold or float(c0["thresholds"]["drawdown_threshold"])
    cost = cost_fractions()
    df = bars.sort_values("ts").reset_index(drop=True)
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    n = len(df)
    n_valid = n - W - H                      # 决策日 i = 0 … n_valid-1 的标签完整
    if n_valid <= 0:
        return pd.DataFrame(), pd.DataFrame()
    i = np.arange(n_valid)
    # ---- A ----
    entry_A = o[i + 1]
    ret_A, mdd_A, mae_A = _hold_stats(entry_A, i + 1, c, l, H, cost["market_in"], cost["out"])
    wide = pd.DataFrame({"ts": df["ts"].iloc[:n_valid].to_numpy(), "symbol": df["symbol"].iloc[0], "close": c[i], "entry_A": entry_A,
                         "ret_A": ret_A, "mdd_A": mdd_A, "mae_A": mae_A, "profit_A": (ret_A > 0).astype(int), "dd15_A": (mdd_A < -thr).astype(int),
                         "ret_after_wait": c[i + W] / c[i] - 1, "min_low_wait": sliding_window_view(l, W)[1:n_valid + 1].min(axis=1) / c[i] - 1})
    for t in (10, 15, 20):
        wide[f"mae{t}_A"] = (mae_A < -t / 100).astype(int)
        wide[f"mdd{t}_A"] = (mdd_A < -t / 100).astype(int)
    # ---- B ----
    lw = sliding_window_view(l, W)[1:n_valid + 1]          # 行 i → 第 i+1 … i+W 天最低价
    frames = []
    for d in grid_pct:
        limit = c[i] * (1 - d / 100)
        mask = lw <= limit[:, None]
        filled = mask.any(axis=1)
        first = mask.argmax(axis=1)
        j = i + 1 + first
        fill_px = np.minimum(limit, o[j])
        ret_B = np.full(n_valid, np.nan); mdd_B = np.full(n_valid, np.nan); mae_B = np.full(n_valid, np.nan)
        ret_A_same_window = np.full(n_valid, np.nan)          # A 从决策日持有到 B 的卖出日（同一日历窗口）的净收益
        if filled.any():
            r, m, a = _hold_stats(fill_px[filled], j[filled], c, l, H, cost["limit_in"], cost["out"])
            ret_B[filled], mdd_B[filled], mae_B[filled] = r, m, a
            exit_idx = j[filled] + H - 1
            ret_A_same_window[filled] = c[exit_idx] * (1 - cost["out"]) / (entry_A[filled] * (1 + cost["market_in"])) - 1
        frames.append(pd.DataFrame({"ts": wide["ts"], "symbol": df["symbol"].iloc[0], "d_pct": float(d), "limit_px": limit,
                                    "filled": filled.astype(int), "fill_offset": np.where(filled, first + 1, -1),
                                    "fill_px": np.where(filled, fill_px, np.nan), "ret_B": ret_B, "mdd_B": mdd_B, "mae_B": mae_B,
                                    "profit_B": np.where(filled, (ret_B > 0).astype(float), np.nan),
                                    "dd15_B": np.where(filled, (mdd_B < -thr).astype(float), np.nan),
                                    "ret_A_counterfactual": ret_A, "ret_A_same_window": np.where(filled, ret_A_same_window, ret_A)}))
    return wide, pd.concat(frames, ignore_index=True)
