"""第十五阶段因子层（预登记 §3）。全部因子先按原始日值计算，再由 lag_for_decision 施加 1 日滞后：
第 t 日收盘的决策只能看到 ≤ t−1 日的链上 / 矿工 / 供应数据。滚动 z 分数只用滚动历史窗口（不用全样本统计量）。
矿工电力现金成本是假设驱动的区间，不是价格底部；减半阶段只是背景标签。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data15 import HALVINGS, build_panel, halving_phase

EFFICIENCY_ANCHORS = {"2018-01-01": 100.0, "2020-01-01": 60.0, "2022-01-01": 38.0, "2024-01-01": 30.0, "2026-01-01": 22.0}   # J/TH，对数线性插值（预登记）
ELECTRICITY_USD_KWH = (0.04, 0.06, 0.08)                                                                                      # 低 / 中 / 高电价
Z_WINDOW, Z_MIN = 1460, 365


def rolling_z(x: pd.Series, window: int = Z_WINDOW, min_periods: int = Z_MIN) -> pd.Series:
    m = x.rolling(window, min_periods=min_periods).mean(); s = x.rolling(window, min_periods=min_periods).std()
    return (x - m) / s.replace(0, np.nan)


def fleet_efficiency(dates: pd.DatetimeIndex) -> pd.Series:
    """机队效率 J/TH：锚点间对数线性插值，锚点外按端点外推（最早锚点之前取 100，最晚锚点之后取 22 的线性外推斜率延续）。"""
    pts = sorted((pd.Timestamp(k, tz="UTC"), v) for k, v in EFFICIENCY_ANCHORS.items()); xs = np.array([p[0].as_unit("ns").value for p in pts], dtype=float); ys = np.log([p[1] for p in pts])
    t = pd.DatetimeIndex(dates).as_unit("ns").asi8.astype(float)                                 # 统一到纳秒：字符串解析出的索引可能是秒 / 微秒精度
    slope_last = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    v = np.interp(t, xs, ys); v = np.where(t > xs[-1], ys[-1] + slope_last * (t - xs[-1]), v); v = np.where(t < xs[0], ys[0], v)
    return pd.Series(np.exp(v), index=dates)


def miner_cost_per_btc(hashrate_ths: pd.Series, btc_out_per_day: pd.Series, elec_usd_kwh: float) -> pd.Series:
    """电力现金成本 / BTC = 算力(TH/s) × 效率(J/TH) × 86400 s ÷ 3.6e6 (J/kWh) × 电价 ÷ 日产出(BTC)。算力用 7 日均降噪。"""
    h = hashrate_ths.rolling(7, min_periods=3).mean(); eff = fleet_efficiency(hashrate_ths.index)
    kwh = h * eff * 86400.0 / 3.6e6
    return (kwh * elec_usd_kwh) / btc_out_per_day.replace(0, np.nan)


def btc_factors(panel: pd.DataFrame | None = None) -> pd.DataFrame:
    p = panel if panel is not None else build_panel("btc"); f = pd.DataFrame(index=p.index)
    f["price"] = p["PriceUSD"]; f["mvrv"] = p["CapMVRVCur"]; f["realized_price"] = p["PriceUSD"] / p["CapMVRVCur"]
    f["puell"] = p["IssTotUSD"] / p["IssTotUSD"].rolling(365, min_periods=300).mean()
    hr = p["bci_hashrate_ths"].combine_first(p["HashRate"])
    f["hash_sma30"] = hr.rolling(30, min_periods=20).mean(); f["hash_sma60"] = hr.rolling(60, min_periods=40).mean()
    f["hash_capitulation"] = f["hash_sma30"] < f["hash_sma60"]
    prev = f["hash_capitulation"].shift(1).fillna(False).astype(bool)
    f["hash_recovery"] = (~f["hash_capitulation"].astype(bool)) & prev                                # 30 日均上穿 60 日均的那一天
    out_btc = (p["IssTotNtv"] + p["FeeTotNtv"])
    for tag, e in zip(("low", "mid", "high"), ELECTRICITY_USD_KWH):
        f[f"miner_cost_{tag}"] = miner_cost_per_btc(hr, out_btc, e)
    f["miner_cost_ratio"] = p["PriceUSD"] / f["miner_cost_mid"]
    f["halving_phase"] = halving_phase(p.index)
    f["ex_netflow_30d"] = (p["FlowInExNtv"] - p["FlowOutExNtv"]).rolling(30, min_periods=20).sum() / p["SplyCur"]
    f["ex_netflow_z"] = rolling_z(f["ex_netflow_30d"], 365, 180)
    f["ex_supply_chg_90d"] = p["SplyExNtv"].pct_change(90)
    f["mvrv_z"] = rolling_z(f["mvrv"]); f["puell_z"] = rolling_z(f["puell"]); f["cost_ratio_z"] = rolling_z(f["miner_cost_ratio"])
    f["valuation_score"] = f[["mvrv_z", "puell_z", "cost_ratio_z"]].mean(axis=1, skipna=False)           # 三个 z 的均值（高 = 贵）
    return f


def eth_factors(panel: pd.DataFrame | None = None) -> pd.DataFrame:
    p = panel if panel is not None else build_panel("eth"); f = pd.DataFrame(index=p.index)
    f["price"] = p["PriceUSD"]; f["mvrv"] = p["CapMVRVCur"]; f["realized_price"] = p["PriceUSD"] / p["CapMVRVCur"]
    f["ethbtc"] = p["PriceBTC"]; f["ethbtc_sma200"] = p["PriceBTC"].rolling(200, min_periods=150).mean(); f["ethbtc_rs"] = p["PriceBTC"] / f["ethbtc_sma200"] - 1; f["ethbtc_mom_90"] = p["PriceBTC"].pct_change(90)
    net = p["eth_supply"].diff() if "eth_supply" in p else pd.Series(np.nan, index=p.index)
    net = net.where(p.index >= pd.Timestamp("2021-08-07", tz="UTC"), p["IssTotNtv"])                      # 销毁前：净发行 = 总发行
    f["net_issuance_30d"] = net.rolling(30, min_periods=20).sum() / p["SplyCur"]
    f["ex_netflow_30d"] = (p["FlowInExNtv"] - p["FlowOutExNtv"]).rolling(30, min_periods=20).sum() / p["SplyCur"]
    f["ex_netflow_z"] = rolling_z(f["ex_netflow_30d"], 365, 180)
    f["ex_supply_chg_90d"] = p["SplyExNtv"].pct_change(90)
    f["mvrv_z"] = rolling_z(f["mvrv"])
    return f


def lag_for_decision(f: pd.DataFrame, lag_days: int = 1) -> pd.DataFrame:
    """施加发布滞后：第 t 日可用的值 = 原始第 t−lag 日的值。链上事实列（halving_phase）不滞后。"""
    g = f.shift(lag_days, freq="D"); g.index.name = "date"
    if "halving_phase" in f:
        g["halving_phase"] = halving_phase(g.index)
    return g


def align_to_bars(f: pd.DataFrame, bar_ts: pd.Series, max_ffill_days: int = 5) -> pd.DataFrame:
    """对齐到日线交易日（UTC 日）；缺失日最多前向填充 5 日（更久视为数据缺失 → 门控为 False）。"""
    idx = pd.DatetimeIndex(pd.to_datetime(bar_ts, utc=True)).normalize()
    full = pd.date_range(min(f.index.min(), idx.min()), max(f.index.max(), idx.max()), freq="1D", tz="UTC")
    g = f.reindex(full).ffill(limit=max_ffill_days).reindex(idx); g.index = pd.DatetimeIndex(pd.to_datetime(bar_ts, utc=True))
    return g


def all_factors(bars: dict) -> dict:
    """{'BTCUSDT': df, 'ETHUSDT': df}，已施加 1 日决策滞后并对齐到日线索引。"""
    return {"BTCUSDT": align_to_bars(lag_for_decision(btc_factors()), bars["BTCUSDT"]["ts"]), "ETHUSDT": align_to_bars(lag_for_decision(eth_factors()), bars["ETHUSDT"]["ts"])}
