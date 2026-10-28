"""链上 / 资金面 / 宏观特征（第二阶段）。全部因果，并按可用性滞后平移：日 t 决策只能看到 t−lag 及之前的链上值。

特征组（group → pit_safe）：
  chain_activity   True   活跃地址、交易数、手续费、算力(BTC)、现货成交额（成交量，区别于链上转账）
  chain_valuation  True   MVRV 水平/变化/z 分数
  stablecoin       True   USDT+USDC 供应增速、供应/市值比
  eth_staking      True   Lido 质押 ETH 增速（ETH 行；BTC 行为市场变量）
  exchange_flows   False  交易所流入/流出/净流/储备（地址标签事后修订）
  defi_tvl         False  以太坊 TVL(ETH 计价)、链上手续费（历史可被重算）
  macro            True   月度 10 年期美债收益率、标普 500、黄金（月末后 40 天可用）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import cfg
from .data.onchain import load_table

GROUPS: dict[str, dict] = {
    "chain_activity": {"pit_safe": True, "cols": []}, "chain_valuation": {"pit_safe": True, "cols": []},
    "stablecoin": {"pit_safe": True, "cols": []}, "eth_staking": {"pit_safe": True, "cols": []},
    "exchange_flows": {"pit_safe": False, "cols": []}, "defi_tvl": {"pit_safe": False, "cols": []},
    "macro": {"pit_safe": True, "cols": []},
}


def _reg(group: str, *cols: str) -> None:
    for c in cols:
        if c not in GROUPS[group]["cols"]:
            GROUPS[group]["cols"].append(c)


def _logratio(s: pd.Series, short: int, long: int) -> pd.Series:
    return np.log(s.rolling(short, min_periods=short).mean() / s.rolling(long, min_periods=long).mean())


def _z(s: pd.Series, n: int) -> pd.Series:
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def _chg(s: pd.Series, n: int) -> pd.Series:
    return s / s.shift(n) - 1


def _shift_days(df: pd.DataFrame, lag: int) -> pd.DataFrame:
    """把“日 t 的指标”标到“日 t+lag 可见”。"""
    d = df.copy()
    d["time"] = d["time"] + pd.Timedelta(days=lag)
    return d


def chain_features(asset: str, lag: int) -> pd.DataFrame:
    cm = load_table(f"cm_{asset}")
    if cm.empty:
        return pd.DataFrame(columns=["time"])
    cm = cm.sort_values("time").set_index("time").asfreq("D")           # 缺失日保留 NaN，不填充
    f = pd.DataFrame(index=cm.index)
    px = cm["PriceUSD"]
    # 活跃度
    f["oc_adract_lr_7_90"] = _logratio(cm["AdrActCnt"], 7, 90); f["oc_adract_lr_30_365"] = _logratio(cm["AdrActCnt"], 30, 365)
    f["oc_txcnt_lr_7_90"] = _logratio(cm["TxCnt"], 7, 90); f["oc_txtfr_lr_30_365"] = _logratio(cm["TxTfrCnt"], 30, 365)
    fees_usd = cm["FeeTotNtv"] * px
    f["oc_fees_lr_30_365"] = _logratio(fees_usd, 30, 365)
    f["oc_spotvol_lr_7_90"] = _logratio(cm["volume_reported_spot_usd_1d"], 7, 90)
    f["oc_adrbal_chg_90"] = _chg(cm["AdrBalCnt"], 90)
    _reg("chain_activity", "oc_adract_lr_7_90", "oc_adract_lr_30_365", "oc_txcnt_lr_7_90", "oc_txtfr_lr_30_365", "oc_fees_lr_30_365", "oc_spotvol_lr_7_90", "oc_adrbal_chg_90")
    if asset == "btc":
        hr = cm["HashRate"]
        f["oc_hashrate_chg_30"] = _chg(hr.rolling(7).mean(), 30)
        f["oc_hash_ribbon"] = hr.rolling(30).mean() / hr.rolling(60).mean() - 1
        _reg("chain_activity", "oc_hashrate_chg_30", "oc_hash_ribbon")
    # 估值
    f["oc_mvrv"] = cm["CapMVRVCur"]; f["oc_mvrv_z365"] = _z(cm["CapMVRVCur"], 365); f["oc_mvrv_chg_30"] = _chg(cm["CapMVRVCur"], 30)
    f["oc_mcap_per_adract_z365"] = _z(np.log(cm["CapMrktCurUSD"] / cm["AdrActCnt"].rolling(30).mean()), 365)
    _reg("chain_valuation", "oc_mvrv", "oc_mvrv_z365", "oc_mvrv_chg_30", "oc_mcap_per_adract_z365")
    # 交易所流量（非 PIT）
    net = cm["FlowInExNtv"] - cm["FlowOutExNtv"]
    res = cm["SplyExNtv"]
    f["ex_netflow_7_over_reserve"] = net.rolling(7).sum() / res; f["ex_netflow_30_over_reserve"] = net.rolling(30).sum() / res
    f["ex_reserve_chg_30"] = _chg(res, 30); f["ex_reserve_chg_90"] = _chg(res, 90)
    f["ex_reserve_share_z365"] = _z(res / cm["SplyCur"], 365)
    f["ex_inflow_lr_7_90"] = _logratio(cm["FlowInExNtv"], 7, 90)
    f["ex_in_out_ratio_30"] = np.log(cm["FlowInExNtv"].rolling(30).sum() / cm["FlowOutExNtv"].rolling(30).sum())
    _reg("exchange_flows", "ex_netflow_7_over_reserve", "ex_netflow_30_over_reserve", "ex_reserve_chg_30", "ex_reserve_chg_90", "ex_reserve_share_z365", "ex_inflow_lr_7_90", "ex_in_out_ratio_30")
    out = f.reset_index().rename(columns={"index": "time"})
    return _shift_days(out, lag)


def market_features(lag: int, macro_lag: int) -> pd.DataFrame:
    """市场级（对 BTC、ETH 行相同）：稳定币供应、Lido 质押、ETH TVL/手续费、宏观。"""
    frames = []
    usdt, usdc = load_table("cm_usdt"), load_table("cm_usdc")
    btc, eth = load_table("cm_btc"), load_table("cm_eth")
    if not usdt.empty:
        s = usdt.set_index("time")["SplyCur"].asfreq("D")
        if not usdc.empty:
            s = s.add(usdc.set_index("time")["SplyCur"].asfreq("D").reindex(s.index).fillna(0), fill_value=0)
        f = pd.DataFrame(index=s.index)
        f["sc_supply_chg_7"] = _chg(s, 7); f["sc_supply_chg_30"] = _chg(s, 30); f["sc_supply_chg_90"] = _chg(s, 90)
        mcap = (btc.set_index("time")["CapMrktCurUSD"].asfreq("D").reindex(s.index) + eth.set_index("time")["CapMrktCurUSD"].asfreq("D").reindex(s.index))
        ratio = s / mcap
        f["sc_supply_over_mcap"] = ratio; f["sc_supply_over_mcap_z365"] = _z(ratio, 365)
        f["sc_usdt_tx_lr_7_90"] = _logratio(usdt.set_index("time")["TxCnt"].asfreq("D").reindex(s.index), 7, 90)
        _reg("stablecoin", "sc_supply_chg_7", "sc_supply_chg_30", "sc_supply_chg_90", "sc_supply_over_mcap", "sc_supply_over_mcap_z365", "sc_usdt_tx_lr_7_90")
        frames.append(_shift_days(f.reset_index().rename(columns={"index": "time"}), lag))
    lido = load_table("lido")
    if not lido.empty:
        s = lido.set_index("time")["lido_staked_eth"].asfreq("D").ffill(limit=3)
        f = pd.DataFrame({"st_lido_chg_30": _chg(s, 30), "st_lido_chg_90": _chg(s, 90)}, index=s.index)
        _reg("eth_staking", "st_lido_chg_30", "st_lido_chg_90")
        frames.append(_shift_days(f.reset_index().rename(columns={"index": "time"}), lag))
    tvl, fees = load_table("eth_chain_tvl"), load_table("eth_fees")
    if not tvl.empty and not eth.empty:
        t = tvl.set_index("time")["eth_tvl_usd"].asfreq("D").ffill(limit=3)
        px = eth.set_index("time")["PriceUSD"].asfreq("D").reindex(t.index).ffill(limit=5)
        tvl_eth = t / px
        f = pd.DataFrame(index=t.index)
        f["df_tvl_eth_chg_30"] = _chg(tvl_eth, 30); f["df_tvl_eth_chg_90"] = _chg(tvl_eth, 90)
        f["df_tvl_over_mcap_z365"] = _z(t / eth.set_index("time")["CapMrktCurUSD"].asfreq("D").reindex(t.index).ffill(limit=5), 365)
        if not fees.empty:
            fe = fees.set_index("time")["eth_fees_usd"].asfreq("D").reindex(t.index)
            f["df_fees_lr_30_365"] = _logratio(fe, 30, 365)
            _reg("defi_tvl", "df_fees_lr_30_365")
        _reg("defi_tvl", "df_tvl_eth_chg_30", "df_tvl_eth_chg_90", "df_tvl_over_mcap_z365")
        frames.append(_shift_days(f.reset_index().rename(columns={"index": "time"}), lag))
    # 宏观：月度值在该月结束后 macro_lag 天可见
    mac = []
    for k in ("us10y", "sp500", "gold"):
        m = load_table(f"macro_{k}")
        if m.empty:
            continue
        m = m.sort_values("time").set_index("time")[f"macro_{k}"]
        g = pd.DataFrame(index=m.index)
        if k == "us10y":
            g["mc_us10y"] = m; g["mc_us10y_chg_3m"] = m - m.shift(3)
            _reg("macro", "mc_us10y", "mc_us10y_chg_3m")
        elif k == "sp500":
            g["mc_sp500_ret_3m"] = _chg(m, 3); g["mc_sp500_ret_12m"] = _chg(m, 12)
            _reg("macro", "mc_sp500_ret_3m", "mc_sp500_ret_12m")
        else:
            g["mc_gold_ret_12m"] = _chg(m, 12)
            _reg("macro", "mc_gold_ret_12m")
        g = g.reset_index()
        g["time"] = g["time"] + pd.offsets.MonthBegin(1) + pd.Timedelta(days=macro_lag)   # 月末 + 滞后
        mac.append(g)
    if mac:
        mm = mac[0]
        for g in mac[1:]:
            mm = mm.merge(g, on="time", how="outer")
        mm = mm.sort_values("time").set_index("time").asfreq("D").ffill(limit=70).reset_index()   # 月度值向前填充到下一次发布
        frames.append(mm)
    if not frames:
        return pd.DataFrame(columns=["time"])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="time", how="outer")
    return out.sort_values("time")


def build_onchain_features(ts_symbol: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """ts_symbol: DataFrame[ts, symbol]。返回按 (ts, symbol) 对齐的链上特征表与特征组注册表。"""
    c = cfg().get("onchain", {})
    lag, mlag = int(c.get("availability_lag_days", 1)), int(c.get("macro_lag_days", 40))
    for g in GROUPS.values():
        g["cols"] = []
    mk = market_features(lag, mlag)
    parts = []
    for sym, asset in (("BTCUSDT", "btc"), ("ETHUSDT", "eth")):
        ch = chain_features(asset, lag)
        k = ts_symbol[ts_symbol["symbol"] == sym][["ts", "symbol"]].copy()
        k["time"] = k["ts"].dt.floor("D")
        m = k.merge(ch, on="time", how="left").merge(mk, on="time", how="left")
        parts.append(m.drop(columns=["time"]))
    out = pd.concat(parts, ignore_index=True)
    # BTC 行没有 ETH 专属列时自动为 NaN（LightGBM/CatBoost 原生处理缺失）
    all_cols = [c for g in GROUPS.values() for c in g["cols"]]
    for c in all_cols:
        if c not in out:
            out[c] = np.nan
    return out[["ts", "symbol"] + all_cols].replace([np.inf, -np.inf], np.nan), {k: dict(v) for k, v in GROUPS.items()}
