"""统一K线/成交数据模式：所有记录带时间戳、来源、时区、质量标记。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag, StrEnum

import pandas as pd


class Venue(StrEnum):
    NATIVE = "native"       # 原生美股（交易所价格，基准）
    TOKEN = "token"         # 代币化股票（链上成交价/发行商报价）


class DataSourceTag(StrEnum):
    MASSIVE = "massive"
    BITGET_RWA_ONDO = "bitget_rwa:ondo"
    BITGET_RWA_XSTOCKS = "bitget_rwa:xstocks"
    BITGET_RWA_UNKNOWN = "bitget_rwa:unknown"
    SIMULATED = "SIMULATED"  # 大写以在任何输出中醒目


class QualityFlag(IntFlag):
    OK = 0
    GAP_BEFORE = 1          # 与前一根之间缺失K线
    DUPLICATE = 2           # 重复时间戳（已去重保留最后）
    OUTLIER_RETURN = 4      # 单根收益率异常
    OFF_HOURS = 8           # 不在常规交易时段（代币 24/7 行情会带此标记）
    INCOMPLETE = 16         # 未完成K线（研究中必须剔除）
    ZERO_VOLUME = 32
    NO_VOLUME_FIELD = 64    # 数据源不提供成交量（如 ondo 报价K线）
    SIMULATED = 128         # 模拟数据
    BACKFILLED = 256        # 由其他来源回填
    NON_TRADING_DAY = 512   # 落在非交易日的原生K线（应被拒绝）


BAR_COLUMNS = [
    "symbol", "venue", "source", "chain", "contract",
    "ts",            # UTC bar 开始时间 (tz-aware)
    "ts_ny",         # America/New_York 本地时间 (naive string for storage)
    "open", "high", "low", "close", "volume", "amount", "txn",
    "quality",       # QualityFlag bitmask
    "is_complete",
    "ingested_at",
]


@dataclass(slots=True)
class Bar:
    symbol: str
    venue: str
    source: str
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    txn: int | None = None
    chain: str | None = None
    contract: str | None = None
    quality: int = 0
    is_complete: bool = True


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in BAR_COLUMNS})
    return df


def normalize_bars(df: pd.DataFrame, *, symbol: str, venue: str, source: str,
                   chain: str | None = None, contract: str | None = None,
                   quality_extra: int = 0) -> pd.DataFrame:
    """把任意 OHLCV 表整理成标准列，补齐来源/时区/质量字段。ts 必须可解析为 UTC。"""
    ts = pd.to_datetime(df["ts"], utc=True).reset_index(drop=True)
    df = df.reset_index(drop=True)
    out = pd.DataFrame(index=df.index)
    out["symbol"] = symbol
    out["venue"] = str(venue)
    out["source"] = source
    out["chain"] = chain
    out["contract"] = contract
    out["ts"] = ts
    out["ts_ny"] = ts.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in ["open", "high", "low", "close"]:
        out[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    for c in ["volume", "amount"]:
        out[c] = pd.to_numeric(df[c], errors="coerce").astype(float) if c in df else float("nan")
    out["txn"] = pd.to_numeric(df["txn"], errors="coerce") if "txn" in df else float("nan")
    out["quality"] = int(quality_extra)
    out["is_complete"] = df["is_complete"].astype(bool) if "is_complete" in df else True
    out["ingested_at"] = pd.Timestamp.now(tz="UTC")
    out = out.sort_values("ts").reset_index(drop=True)
    return out[BAR_COLUMNS]
