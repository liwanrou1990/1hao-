"""公开、固定的趋势状态与风险等级规则（不含任何预测模型）。

趋势状态（决策日收盘可知）：
  强势上涨：close > MA50 > MA200 且 MA200 30 日斜率 > 0
  上涨    ：close > MA200 且 MA200 斜率 > 0（不满足强势条件）
  下跌    ：close < MA200 且 MA200 斜率 < 0（不满足强势条件）
  强势下跌：close < MA50 < MA200 且 MA200 斜率 < 0
  震荡    ：其他
风险评分 0~100（分数越高风险越高），四项各 0~25 分，线性插值：
  ① 波动率：rv20 在过去 365 天 rv20 分布中的分位数（0→0 分，1→25 分）
  ② 趋势：强势上涨 0、上涨 6、震荡 12、下跌 19、强势下跌 25
  ③ 过热/偏离：close/MA200 − 1 ≥ +60% → 25；≤ 0 → 0；中间线性（历史上 MA200 上方 60% 以上时 90 天浮亏>15% 频率显著更高）
  ④ 近期动量极端：|30 日收益| ≥ 40% → 25；0 → 0（大涨大跌后 90 天波动都更大）
等级：<30 低，30~50 中，50~70 高，≥70 极高。
条件历史统计（描述性，不是预测）：当前趋势状态下，历史 90 天买入持有的收益分位数与浮亏>15% 频率。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import add_indicators
from ..labels import build_labels

STATES = ["强势上涨", "上涨", "震荡", "下跌", "强势下跌"]
TREND_SCORE = {"强势上涨": 0, "上涨": 6, "震荡": 12, "下跌": 19, "强势下跌": 25}


def trend_state(close: float, ma50: float, ma200: float, slope: float) -> str:
    if not np.isfinite(ma200) or not np.isfinite(slope):
        return "数据不足"
    if close > ma50 > ma200 and slope > 0:
        return "强势上涨"
    if close < ma50 < ma200 and slope < 0:
        return "强势下跌"
    if close > ma200 and slope > 0:
        return "上涨"
    if close < ma200 and slope < 0:
        return "下跌"
    return "震荡"


def enrich(bars_1d: pd.DataFrame) -> pd.DataFrame:
    df = add_indicators(bars_1d)
    c = df["close"]
    df["ma200_slope30"] = df["ma200"] / df["ma200"].shift(30) - 1
    df["state"] = [trend_state(cl, m50, m200, sl) for cl, m50, m200, sl in zip(c, df["ma50"], df["ma200"], df["ma200_slope30"])]
    df["rv20_pct365"] = df["rv20"].rolling(365, min_periods=120).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    df["ma200_dist"] = c / df["ma200"] - 1
    for n in (7, 30, 90):
        df[f"ret_{n}d"] = c / c.shift(n) - 1
        df[f"high_{n}d"] = df["high"].rolling(n).max(); df[f"low_{n}d"] = df["low"].rolling(n).min()
        df[f"dd_from_{n}d_high"] = c / df[f"high_{n}d"] - 1
        df[f"up_from_{n}d_low"] = c / df[f"low_{n}d"] - 1
        lr = np.log(c).diff()
        df[f"rv{n}_ann"] = lr.rolling(n, min_periods=max(3, n // 2)).std() * np.sqrt(365)
    df["high_365d"] = df["high"].rolling(365, min_periods=90).max(); df["low_365d"] = df["low"].rolling(365, min_periods=90).min()
    df["dd_from_365d_high"] = c / df["high_365d"] - 1
    df["risk_score"] = risk_score_frame(df)
    df["risk_level"] = pd.cut(df["risk_score"], [-1, 30, 50, 70, 101], labels=["低", "中", "高", "极高"]).astype(str)
    return df


def risk_score_frame(df: pd.DataFrame) -> pd.Series:
    vol = (df["rv20_pct365"].clip(0, 1) * 25).fillna(12.5)
    tr = df["state"].map(TREND_SCORE).fillna(12.5)
    heat = (df["ma200_dist"].clip(0, 0.6) / 0.6 * 25).fillna(0)
    mom = (df["ret_30d"].abs().clip(0, 0.4) / 0.4 * 25).fillna(0)
    return (vol + tr + heat + mom).round(1)


def risk_components(row: pd.Series) -> dict:
    return {"波动率分位": float(np.nan_to_num(row["rv20_pct365"], nan=0.5) * 25), "趋势状态": float(TREND_SCORE.get(row["state"], 12.5)),
            "偏离MA200过热": float(np.clip(np.nan_to_num(row["ma200_dist"]), 0, 0.6) / 0.6 * 25), "30日动量极端": float(np.clip(abs(np.nan_to_num(row["ret_30d"])), 0, 0.4) / 0.4 * 25)}


def conditional_history(bars_1d: pd.DataFrame, enriched: pd.DataFrame, H: int = 90) -> pd.DataFrame:
    """按趋势状态统计历史 H 天持有（含成本）的收益分位数、盈利频率、浮亏>10/15/20% 频率。只用已完成窗口。"""
    wide, _ = build_labels(bars_1d, H=H, W=14)
    m = wide.merge(enriched[["ts", "state", "risk_level"]], on="ts", how="left")
    rows = []
    for key, g in list(m.groupby("state")) + [("全部", m)]:
        rows.append({"状态": key, "样本日": len(g), "盈利频率": g["profit_A"].mean(), "收益中位": g["ret_A"].median(), "收益均值": g["ret_A"].mean(),
                     "收益5%分位": g["ret_A"].quantile(0.05), "收益95%分位": g["ret_A"].quantile(0.95),
                     "浮亏>10%": (g["mae_A"] < -0.10).mean(), "浮亏>15%": (g["mae_A"] < -0.15).mean(), "浮亏>20%": (g["mae_A"] < -0.20).mean(), "平均最大浮亏": g["mae_A"].mean()})
    return pd.DataFrame(rows)
