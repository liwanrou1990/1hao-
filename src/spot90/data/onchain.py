"""链上 / 资金面 / 宏观数据采集（第二阶段）。只用本环境实际可达的免费来源，不可达的记录为 blocked，绝不伪造。

来源与可达性（2026-10-28 本容器实测）：
* Coin Metrics Community：官方 API（community-api.coinmetrics.io）被出网策略拒绝；改用官方 GitHub 仓库 coinmetrics/data 每日发布的
  CSV 归档（raw.githubusercontent.com 可达）。指标：活跃地址、交易数、MVRV、交易所流入/流出/储备、算力、手续费、发行量、
  现货成交额、USDT/USDC 供应量等。**该归档数据截至 2026-05-23**（之后缺失，记录为缺口，不填充）。
* DefiLlama（api.llama.fi 可达；stablecoins.llama.fi 不可达）：以太坊链 TVL（2017-09 起）、以太坊日手续费（2018-03 起）、
  Lido 质押 ETH 数量（2020-12 起，作为 ETH 质押代理指标）。TVL 历史会随适配器修订而重算 → 标记为非 Point-in-Time。
* 宏观：GitHub datasets 组织的月度 10 年期美债收益率、标普 500、黄金（月频，发布滞后按保守规则处理）。
* Glassnode / CryptoQuant / Santiment / Coinglass / 各交易所期货接口 / Farside ETF / FRED：本环境全部不可达，且 Glassnode、
  CryptoQuant 需付费密钥（通过环境变量 GLASSNODE_API_KEY / CRYPTOQUANT_API_KEY 预留，未实现调用即视为 blocked）。

Point-in-Time 规则：
* 交易所流量/储备依赖交易所地址标签，标签会事后修订 → pit_safe=False；DefiLlama TVL/手续费历史可被重算 → pit_safe=False。
* 活跃地址、交易数、MVRV、供应量、算力、Lido 质押量、稳定币供应量：由链上历史直接计算，修订极少 → pit_safe=True。
* 可用时间：Coin Metrics 日 t 的数据在 t+1 日 UTC 02:00-05:00 左右完成（EOD 完成时间列实测中位 2.6-5.1 小时），
  本系统决策点为日 t 收盘(= t+1 00:00 UTC)，因此日 t 的链上数据对日 t 决策不可见，统一使用 availability_lag_days=1（可配置为 2 做稳健性检验）。
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..config import cfg, data_dir, env

CM_RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"
CM_ASSETS = ["btc", "eth", "usdt", "usdc"]
CM_KEEP = ["AdrActCnt", "AdrBalCnt", "BlkCnt", "CapMVRVCur", "CapMrktCurUSD", "FeeTotNtv", "FlowInExNtv", "FlowInExUSD", "FlowOutExNtv",
           "FlowOutExUSD", "HashRate", "IssTotNtv", "PriceUSD", "SplyCur", "SplyExNtv", "SplyExUSD", "TxCnt", "TxTfrCnt", "volume_reported_spot_usd_1d",
           "AssetEODCompletionTime"]
LLAMA = {"eth_chain_tvl": "https://api.llama.fi/v2/historicalChainTvl/Ethereum",
         "eth_fees": "https://api.llama.fi/overview/fees/ethereum?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyFees",
         "lido": "https://api.llama.fi/protocol/lido"}
MACRO = {"us10y": "https://raw.githubusercontent.com/datasets/bond-yields-us-10y/main/data/monthly.csv",
         "sp500": "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv",
         "gold": "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"}
BLOCKED_SOURCES = {
    "glassnode": {"why": "api.glassnode.com 不可达；需付费密钥 GLASSNODE_API_KEY", "metrics": "SOPR / NUPL / LTH-STH / 巨鲸 / 矿工流"},
    "cryptoquant": {"why": "api.cryptoquant.com 不可达；需付费密钥 CRYPTOQUANT_API_KEY", "metrics": "交易所储备 / 矿工流 / 稳定币交易所净流"},
    "coinmetrics_api": {"why": "community-api.coinmetrics.io 不可达；已用其 GitHub CSV 归档替代", "metrics": "同 CSV"},
    "derivatives": {"why": "api.bitget.com(mix) / fapi.binance.com / bybit / okx / coinglass 全部不可达", "metrics": "资金费率 / 未平仓量 / 清算"},
    "etf_flows": {"why": "farside.co.uk / tftc.io / jan3.com 不可达", "metrics": "BTC/ETH 现货 ETF 日资金流"},
    "stablecoin_exchange_flows": {"why": "需 CryptoQuant/Glassnode；CM 社区版只有稳定币总供应量", "metrics": "USDT/USDC 交易所净流入"},
    "macro_daily": {"why": "FRED 不可达；只有 GitHub datasets 的月度序列", "metrics": "DXY / 日频利率 / 流动性"},
}


def _root() -> Path:
    p = data_dir() / "onchain"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get(client: httpx.Client, url: str, cache: Path, refresh: bool) -> bytes | None:
    if cache.exists() and not refresh:
        return cache.read_bytes()
    try:
        r = client.get(url)
        if r.status_code == 200:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(r.content)
            return r.content
    except httpx.HTTPError:
        pass
    return cache.read_bytes() if cache.exists() else None


def _meta(name: str, df: pd.DataFrame, source: str, pit_safe: bool, lag_days: int, cadence: str, note: str = "") -> dict:
    cols = [c for c in df.columns if c not in ("time",)]
    return {"name": name, "source": source, "pit_safe": pit_safe, "availability_lag_days": lag_days, "cadence": cadence,
            "rows": int(len(df)), "first": str(df["time"].min().date()) if len(df) else None, "last": str(df["time"].max().date()) if len(df) else None,
            "missing_ratio_since_2017": {c: round(float(df.loc[df["time"] >= "2017-08-17", c].isna().mean()), 3) for c in cols},
            "collected_at": datetime.now(timezone.utc).isoformat(), "note": note}


def fetch_all(refresh: bool = True) -> dict:
    raw = data_dir() / "raw_cache"
    out = _root()
    client = httpx.Client(timeout=90, headers={"User-Agent": "spot90/0.2"})
    status: dict = {"run_ts": datetime.now(timezone.utc).isoformat(), "sources": {}, "blocked": BLOCKED_SOURCES}
    lag = int(cfg().get("onchain", {}).get("availability_lag_days", 1))
    # ---- Coin Metrics CSV 归档
    for a in CM_ASSETS:
        b = _get(client, CM_RAW.format(asset=a), raw / "coinmetrics" / f"{a}.csv", refresh)
        if b is None:
            status["sources"][f"cm_{a}"] = {"ok": False, "why": "raw.githubusercontent.com 不可达且无本地缓存"}
            continue
        df = pd.read_csv(io.BytesIO(b))
        keep = ["time"] + [c for c in CM_KEEP if c in df.columns]
        df = df[keep].copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.dropna(subset=[c for c in keep if c not in ("time", "AssetEODCompletionTime")], how="all")
        if "AssetEODCompletionTime" in df:
            df["published_at"] = pd.to_datetime(df["AssetEODCompletionTime"], unit="s", utc=True, errors="coerce")
            df = df.drop(columns=["AssetEODCompletionTime"])
        df.to_parquet(out / f"cm_{a}.parquet", index=False)
        m = _meta(f"cm_{a}", df, "coinmetrics/data GitHub CSV（Community）", True, lag, "daily",
                  "交易所流量/储备列(FlowInEx*/FlowOutEx*/SplyEx*) 依赖地址标签 → 非 PIT，特征层单独分组")
        m["ok"] = True
        status["sources"][f"cm_{a}"] = m
    # ---- DefiLlama
    for k, url in LLAMA.items():
        b = _get(client, url, raw / "defillama" / f"{k}.json", refresh)
        if b is None:
            status["sources"][k] = {"ok": False, "why": "api.llama.fi 不可达且无缓存"}
            continue
        d = json.loads(b)
        if k == "eth_chain_tvl":
            df = pd.DataFrame(d).rename(columns={"date": "time", "tvl": "eth_tvl_usd"})
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.floor("D")
        elif k == "eth_fees":
            df = pd.DataFrame(d.get("totalDataChart", []), columns=["time", "eth_fees_usd"])
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.floor("D")
        else:
            tk = d["chainTvls"]["Ethereum"]["tokens"]
            df = pd.DataFrame({"time": pd.to_datetime([x["date"] for x in tk], unit="s", utc=True).floor("D"),
                               "lido_staked_eth": [sum(v for kk, v in x["tokens"].items() if kk in ("WETH", "ETH", "STETH")) for x in tk]})
        df = df.drop_duplicates("time", keep="last").sort_values("time")
        df.to_parquet(out / f"{k}.parquet", index=False)
        pit = k == "lido"
        m = _meta(k, df, "api.llama.fi", pit, lag, "daily", "TVL/手续费历史会因适配器修订重算 → 非 PIT" if not pit else "链上质押数量（Lido 占全网质押约 1/4~1/3，仅作代理）")
        m["ok"] = True
        status["sources"][k] = m
    # ---- 宏观（月度）
    for k, url in MACRO.items():
        b = _get(client, url, raw / "macro" / f"{k}.csv", refresh)
        if b is None:
            status["sources"][f"macro_{k}"] = {"ok": False, "why": "不可达且无缓存"}
            continue
        df = pd.read_csv(io.BytesIO(b))
        col = {"us10y": "Rate", "sp500": "SP500", "gold": "Price"}[k]
        df = df.rename(columns={"Date": "time", col: f"macro_{k}"})[["time", f"macro_{k}"]]
        df["time"] = pd.to_datetime(df["time"].astype(str).str.slice(0, 7) + "-01", utc=True)
        df = df[df["time"] >= "2010-01-01"]
        df.to_parquet(out / f"macro_{k}.parquet", index=False)
        m = _meta(f"macro_{k}", df, "GitHub datasets 月度 CSV", True, 40, "monthly", "月度值视为该月结束后 40 天才可用（保守发布滞后）")
        m["ok"] = True
        status["sources"][f"macro_{k}"] = m
    # ---- 付费/不可达接口：只记录
    for key, envname in (("glassnode", "GLASSNODE_API_KEY"), ("cryptoquant", "CRYPTOQUANT_API_KEY")):
        status["blocked"][key]["has_key_in_env"] = bool(env(envname))
    (out / "onchain_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str))
    return status


def load_table(name: str) -> pd.DataFrame:
    p = _root() / f"{name}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def load_status() -> dict | None:
    p = _root() / "onchain_status.json"
    return json.loads(p.read_text()) if p.exists() else None
