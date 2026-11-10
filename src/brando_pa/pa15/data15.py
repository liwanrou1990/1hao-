"""第十五阶段数据层：读取已快照的原始数据（CoinMetrics 社区 1d、blockchain.info、mempool.space、ultrasound.money），
构建按 UTC 日对齐的原始指标面板，并做可获得性 / 时间戳 / 质量审计。

“当时可得”规则（预登记 §2）：链上与矿工日指标在 D+1 ~04:00 UTC 后才可见 → 第 t 日收盘决策只能用 ≤ t−1 日的值；
本模块只给出原始日值，滞后由 factors15.lag_for_decision 统一施加（shift 1 日），并有测试断言。
所有函数只读 raw 目录；不访问网络（抓取由 fetch_raw 单独负责，且只写 raw 目录）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT as ROOT

RAW = ROOT / "data_store" / "brando_pa" / "phase15_onchain" / "raw"
HALVINGS = [pd.Timestamp(x, tz="UTC") for x in ("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20")]
HALVING_INTERVAL_DAYS = 1458                                           # 四次减半的平均间隔（天），只用于“阶段”标签

UNAVAILABLE = {                                                        # 明确不可得（本阶段不用估计值冒充）
    "spot_etf_flows": "现货 ETF 持仓与资金流：Farside 页面为 Cloudflare 人机验证，自动抓取被拒（HTTP 403 / 'Just a moment'）；未接入",
    "staking_queue": "质押与退出队列：beaconcha.in API 需要 API key（HTTP 401）；未接入",
    "lth_sth_cost_basis": "长期 / 短期持有者链上成本（LTH/STH realized price、SOPR）：CoinMetrics 社区层无此指标，Glassnode 为付费；未接入",
    "miner_wallet_flows": "矿工钱包流出 / 矿工持币：CoinMetrics 社区层无 FlowOutMiner / SplyMiner 指标；未接入",
    "balance_cohort_cost": "按余额分层钱包的估计成本：无数据；也不以大钱包推断机构",
    "institutional_disclosed_cost": "机构公开披露成本（SEC 文件等）：非自动化数据，本阶段未接入；任何‘机构成本’都不进入回测",
    "miner_financial_reports": "矿业公司季报（现金成本、算力、售币）：发布滞后数周且非结构化；未接入",
}


def _dates(ts) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).normalize()


def load_coinmetrics(asset: str) -> pd.DataFrame:
    df = pd.read_csv(RAW / f"coinmetrics_{asset}_1d.csv")
    df["date"] = _dates(df["time"]); df = df.set_index("date").sort_index()
    cols = [c for c in df.columns if c not in ("asset", "time") and not c.endswith("-status") and not c.endswith("-status-time")]
    out = df[cols].apply(pd.to_numeric, errors="coerce")
    return out[~out.index.duplicated(keep="last")]


def load_bci(name: str) -> pd.Series:
    d = json.loads((RAW / f"blockchain_info_{name}.json").read_text())
    s = pd.Series({pd.Timestamp(v["x"], unit="s", tz="UTC").normalize(): float(v["y"]) for v in d["values"]}).sort_index()
    return s[~s.index.duplicated(keep="last")].rename(name)


def load_mempool_difficulty() -> pd.DataFrame:
    d = json.loads((RAW / "mempool_space_hashrate_all.json").read_text())
    df = pd.DataFrame(d["difficulty"]); df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_eth_supply() -> pd.Series:
    """ultrasound.money since_burn（2021-08-06 →）供应量，按 UTC 日取最后一个观测；日差 = 净发行（含销毁）。"""
    d = json.loads((RAW / "ultrasound_supply_over_time.json").read_text())
    s = pd.Series({pd.Timestamp(x["timestamp"]).tz_convert("UTC") if pd.Timestamp(x["timestamp"]).tzinfo else pd.Timestamp(x["timestamp"], tz="UTC"): float(x["supply"]) for x in d["since_burn"]}).sort_index()
    return s.resample("1D").last().rename("eth_supply")


def raw_meta() -> dict:
    return json.loads((RAW / "coinmetrics_meta.json").read_text()) if (RAW / "coinmetrics_meta.json").exists() else {}


def build_panel(asset: str) -> pd.DataFrame:
    """原始日值面板（未施加决策滞后）。BTC 附 blockchain.info 算力 / 难度 / 矿工总收入；ETH 附供应量。索引 = UTC 日。"""
    cm = load_coinmetrics(asset)
    if asset == "btc":
        for n, col in (("hash-rate", "bci_hashrate_ths"), ("difficulty", "bci_difficulty"), ("miners-revenue", "bci_miners_revenue_usd"), ("market-price", "bci_price_usd")):
            cm = cm.join(load_bci(n).rename(col), how="outer")
    else:
        cm = cm.join(load_eth_supply(), how="outer")
    cm.index.name = "date"
    return cm.sort_index()


def halving_phase(dates: pd.DatetimeIndex) -> pd.Series:
    """自上次减半的天数 / 1458；减半前为 NaN。只作背景标签。"""
    out = pd.Series(np.nan, index=dates)
    for i, h in enumerate(HALVINGS):
        nxt = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else None
        m = (dates >= h) & ((dates < nxt) if nxt is not None else True)
        out[m] = (dates[m] - h).days / HALVING_INTERVAL_DAYS
    return out


def audit(bars: dict | None = None, now: pd.Timestamp | None = None) -> dict:
    """可获得性 / 发布时间 / 质量 / 时间戳审计（只读）。bars：第十阶段日线（用于价格交叉核对）。"""
    now = now or pd.Timestamp.now(tz="UTC"); out = {"checked_at_utc": str(now), "raw_dir": str(RAW), "fetch_meta": raw_meta(), "sources": {}, "cross_checks": {}, "unavailable": UNAVAILABLE,
                                                      "decision_lag_rule": "链上 / 矿工 / 供应日指标：第 t 日收盘决策只用 ≤ t−1 日的值（shift 1 日）；难度调整与减半日期为链上事实，不滞后；日线价格第 t 日收盘后可用"}
    for asset in ("btc", "eth"):
        cm = load_coinmetrics(asset)
        for col in cm.columns:
            s = cm[col].dropna()
            if s.empty:
                continue
            idx = s.index; gaps = int(((idx[1:] - idx[:-1]).days > 1).sum()) if len(idx) > 1 else 0
            out["sources"][f"coinmetrics_{asset}_{col}"] = {"start": str(idx[0].date()), "end": str(idx[-1].date()), "n": int(len(s)), "internal_gaps": gaps, "future_timestamps": int((idx > now).sum()),
                                                            "publication_lag_assumed_days": 1, "revision_risk": "交易所标签 / 实现市值可能被追溯修订（无历史快照可查）" if col in ("CapMVRVCur", "FlowInExNtv", "FlowOutExNtv", "SplyExNtv") else "低"}
    for n in ("hash-rate", "difficulty", "miners-revenue", "market-price"):
        s = load_bci(n); idx = s.index
        out["sources"][f"blockchain_info_{n}"] = {"start": str(idx[0].date()), "end": str(idx[-1].date()), "n": int(len(s)), "internal_gaps": int(((idx[1:] - idx[:-1]).days > 1).sum()), "future_timestamps": int((idx > now).sum()), "publication_lag_assumed_days": 1,
                                                 "revision_risk": "算力为区块时间戳估计，日值噪声大（用 30/60 日均）" if n == "hash-rate" else "低"}
    md = load_mempool_difficulty(); out["sources"]["mempool_difficulty_adjustments"] = {"start": str(md["time"].iloc[0].date()), "end": str(md["time"].iloc[-1].date()), "n": int(len(md)), "publication_lag_assumed_days": 0, "note": "调整时刻链上精确可知"}
    es = load_eth_supply().dropna(); out["sources"]["ultrasound_eth_supply"] = {"start": str(es.index[0].date()), "end": str(es.index[-1].date()), "n": int(len(es)), "internal_gaps": int(es.reindex(pd.date_range(es.index[0], es.index[-1], freq="1D")).isna().sum()), "publication_lag_assumed_days": 1, "note": "2021-08-06 之前净发行 = CoinMetrics IssTotNtv（无销毁）"}
    out["sources"]["halvings"] = {"dates": [str(h.date()) for h in HALVINGS], "publication_lag_assumed_days": 0}
    # 交叉核对：CoinMetrics 算力 vs blockchain.info 算力；CoinMetrics 价格 vs 日线收盘
    btc = build_panel("btc"); both = btc[["HashRate", "bci_hashrate_ths"]].dropna(); both = both[both.index >= "2018-01-01"]
    r = (both["HashRate"] / both["bci_hashrate_ths"]); out["cross_checks"]["hashrate_cm_over_bci"] = {"median_ratio": float(r.median()), "p5": float(r.quantile(.05)), "p95": float(r.quantile(.95)), "n": int(len(r)), "note": "两者同为 TH/s 的日均估计；比值接近 1 说明单位与口径一致"}
    if bars:
        for sym, asset in (("BTCUSDT", "btc"), ("ETHUSDT", "eth")):
            b = bars[sym].copy(); b["date"] = _dates(b["ts"]); c = b.set_index("date")["close"].astype(float); cm = load_coinmetrics(asset)["PriceUSD"]
            j = pd.concat([c.rename("close"), cm.rename("cm")], axis=1).dropna(); j = j[j.index >= "2018-01-01"]; rel = (j["cm"] / j["close"] - 1).abs()
            out["cross_checks"][f"price_{sym}_cm_vs_close"] = {"median_abs_rel_diff": float(rel.median()), "p99_abs_rel_diff": float(rel.quantile(.99)), "n": int(len(rel)), "note": "CoinMetrics PriceUSD 为多所加权日均价，日线收盘为单一所收盘价；差异只反映口径，不用于回测定价"}
    return out
