"""因果特征：第 i 天收盘时已知的信息（日线 + 4 小时线 + 市场/周期变量）。不含任何未来数据。"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .indicators import add_indicators

HALVINGS = [date(2012, 11, 28), date(2016, 7, 9), date(2020, 5, 11), date(2024, 4, 20)]
FEATURES = [
    "ret_7d", "ret_14d", "ret_30d", "ret_60d", "ret_90d", "ret_180d", "ret_365d",
    "ma20_dist", "ma50_dist", "ma90_dist", "ma200_dist", "ma50_ma200", "ma200_slope30", "ma50_slope10",
    "rsi14", "rsi14_mean7", "macd_hist_rel", "atr_rel", "rv20", "rv60", "rv90", "rv_ratio",
    "dd_from_365d_high", "dist_365d_low", "days_since_365d_high", "dd_from_ath",
    "vol_ratio_20_90", "qvol_z60", "rv4h_7d", "rv4h_ratio", "halving_phase", "is_eth",
    "mkt_ret_30d", "mkt_ret_90d", "mkt_dd_365", "mkt_ma200_dist",
]


def _halving_phase(ts: pd.Series) -> pd.Series:
    d = ts.dt.date
    out = []
    for x in d:
        prev = max([h for h in HALVINGS if h <= x], default=HALVINGS[0])
        out.append(min((x - prev).days / 1460.0, 1.5))
    return pd.Series(out, index=ts.index)


def _rv4h_features(bars_1d: pd.DataFrame, bars_4h: pd.DataFrame | None) -> pd.DataFrame:
    idx = bars_1d["ts"].dt.floor("D")
    out = pd.DataFrame({"rv4h_7d": np.nan, "rv4h_ratio": np.nan}, index=bars_1d.index)
    if bars_4h is None or bars_4h.empty:
        return out
    b = bars_4h.sort_values("ts").copy()
    b["lr2"] = np.log(b["close"]).diff() ** 2
    b["day"] = (b["ts"] - pd.Timedelta(hours=4)).dt.floor("D")      # 4h 收盘时间 = ts+4h；收盘落在该 UTC 日内才计入
    b["day"] = b["ts"].dt.floor("D")
    daily = b.groupby("day")["lr2"].agg(["sum", "count"])
    daily = daily[daily["count"] >= 3]
    rv7 = np.sqrt(daily["sum"].rolling(7, min_periods=5).sum() / 7 * 365)
    rv30 = np.sqrt(daily["sum"].rolling(30, min_periods=20).sum() / 30 * 365)
    m = pd.DataFrame({"rv4h_7d": rv7, "rv4h_ratio": rv7 / rv30})
    j = pd.DataFrame({"day": idx}).merge(m, left_on="day", right_index=True, how="left")
    out["rv4h_7d"], out["rv4h_ratio"] = j["rv4h_7d"].to_numpy(), j["rv4h_ratio"].to_numpy()
    return out


def build_features(bars_1d: pd.DataFrame, bars_4h: pd.DataFrame | None = None, market_1d: pd.DataFrame | None = None) -> pd.DataFrame:
    """bars_1d: 单一标的日线；market_1d: BTC 日线（作为整体市场变量；对 BTC 自身即本身）。返回 ts + FEATURES。"""
    df = add_indicators(bars_1d)
    c = df["close"]
    f = pd.DataFrame({"ts": df["ts"], "symbol": df["symbol"], "close": c})
    f["ret_7d"], f["ret_30d"], f["ret_90d"], f["ret_180d"], f["ret_365d"] = df["ret_7d"], df["ret_30d"], df["ret_90d"], df["ret_180d"], df["ret_365d"]
    f["ret_14d"] = c / c.shift(14) - 1
    f["ret_60d"] = c / c.shift(60) - 1
    for n in (20, 50, 90, 200):
        f[f"ma{n}_dist"] = c / df[f"ma{n}"] - 1
    f["ma50_ma200"] = df["ma50"] / df["ma200"] - 1
    f["ma200_slope30"] = df["ma200"] / df["ma200"].shift(30) - 1
    f["ma50_slope10"] = df["ma50"] / df["ma50"].shift(10) - 1
    f["rsi14"] = df["rsi14"]
    f["rsi14_mean7"] = df["rsi14"].rolling(7).mean()
    f["macd_hist_rel"] = df["macd_hist"] / c
    f["atr_rel"] = df["atr14"] / c
    f["rv20"], f["rv60"], f["rv90"] = df["rv20"], df["rv60"], df["rv90"]
    f["rv_ratio"] = df["rv20"] / df["rv90"]
    f["dd_from_365d_high"] = df["dd_from_365d_high"]
    f["dist_365d_low"] = c / c.rolling(365, min_periods=90).min() - 1
    roll_argmax = c.rolling(365, min_periods=90).apply(lambda x: len(x) - 1 - int(np.argmax(x)), raw=True)
    f["days_since_365d_high"] = roll_argmax / 365.0
    f["dd_from_ath"] = df["dd_from_ath"]
    v = df["volume"].replace(0, np.nan)
    f["vol_ratio_20_90"] = np.log(v.rolling(20).mean() / v.rolling(90).mean())
    qv = df["quote_volume"].replace(0, np.nan)
    f["qvol_z60"] = (np.log(qv) - np.log(qv).rolling(60).mean()) / np.log(qv).rolling(60).std()
    r4 = _rv4h_features(df, bars_4h)
    f["rv4h_7d"], f["rv4h_ratio"] = r4["rv4h_7d"].to_numpy(), r4["rv4h_ratio"].to_numpy()
    f["halving_phase"] = _halving_phase(df["ts"])
    f["is_eth"] = float(str(df["symbol"].iloc[0]).upper().startswith("ETH"))
    mk = add_indicators(market_1d) if market_1d is not None and not market_1d.empty else df
    m = pd.DataFrame({"ts": mk["ts"], "mkt_ret_30d": mk["ret_30d"], "mkt_ret_90d": mk["ret_90d"], "mkt_dd_365": mk["dd_from_365d_high"],
                      "mkt_ma200_dist": mk["close"] / mk["ma200"] - 1})
    f = f.merge(m, on="ts", how="left")
    return f.replace([np.inf, -np.inf], np.nan)
