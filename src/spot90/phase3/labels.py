"""多周期标签：H ∈ {7, 14, 30, 90}。第 i 天收盘决策，第 i+1 天开盘买入，第 i+H 天收盘卖出（含手续费/滑点）。
每个周期：净收益 ret、最大浮亏 mae、峰谷回撤 mdd、profit、mae10/15/20、mdd10/15/20。全部决策日对齐到 90 天标签可用的最后一天，保证各周期测试日期相同。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..labels import _hold_stats, cost_fractions

HORIZONS = (7, 14, 30, 90)


def build_multi_labels(bars: pd.DataFrame, horizons=HORIZONS, common_cutoff_days: int = 104) -> dict[int, pd.DataFrame]:
    df = bars.sort_values("ts").reset_index(drop=True)
    o, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "low", "close"))
    n = len(df)
    n_valid = n - common_cutoff_days                # 与 90 天 + 14 天等待的标签范围一致
    cost = cost_fractions()
    out = {}
    i = np.arange(n_valid)
    for H in horizons:
        entry = o[i + 1]
        ret, mdd, mae = _hold_stats(entry, i + 1, c, l, H, cost["market_in"], cost["out"])
        w = pd.DataFrame({"ts": df["ts"].iloc[:n_valid].to_numpy(), "symbol": df["symbol"].iloc[0], "close": c[i], "entry": entry,
                          "ret": ret, "mdd": mdd, "mae": mae, "profit": (ret > 0).astype(int)})
        for t in (10, 15, 20):
            w[f"mae{t}"] = (mae < -t / 100).astype(int)
            w[f"mdd{t}"] = (mdd < -t / 100).astype(int)
        out[H] = w
    return out
