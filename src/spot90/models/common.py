"""模型统一输出格式。
A（立即买入）：p_profit, ret_mean, ret_q05, ret_q25, ret_q50, ret_q75, ret_q95, p_dd
B（限价 d%）：p_fill, p_profit, ret_mean, ret_q50, p_dd
p_dd 的回撤口径由 config.thresholds.drawdown_metric 决定（mae=从买入价起算最大浮亏；mdd=峰谷回撤）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg

A_COLS = ["p_profit", "ret_mean", "ret_q05", "ret_q25", "ret_q50", "ret_q75", "ret_q95", "p_dd"]
B_COLS = ["p_fill", "p_profit", "ret_mean", "ret_q50", "p_dd"]
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def dd_metric() -> str:
    return str(cfg()["thresholds"].get("drawdown_metric", "mae"))


def dd_label_cols() -> tuple[str, str]:
    """返回 (A 回撤标签列, B 回撤标签列)。"""
    thr = float(cfg()["thresholds"]["drawdown_threshold"])
    return ("dd_A_flag", "dd_B_flag")


def add_dd_flags(wide: pd.DataFrame, long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    thr = float(cfg()["thresholds"]["drawdown_threshold"])
    m = dd_metric()
    wide = wide.copy(); long = long.copy()
    wide["dd_A_flag"] = ((wide["mae_A"] if m == "mae" else wide["mdd_A"]) < -thr).astype(int)
    src = long["mae_B"] if m == "mae" else long["mdd_B"]
    long["dd_B_flag"] = np.where(long["filled"] == 1, (src < -thr).astype(float), np.nan)
    return wide, long


def empty_predA(index) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=index, columns=A_COLS)
