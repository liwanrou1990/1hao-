"""统一 K 线列：symbol, tf, ts(UTC 开盘时间), open, high, low, close, volume(基础币), quote_volume(计价币), source, ingested_at。"""
from __future__ import annotations

import pandas as pd

BAR_COLUMNS = ["symbol", "tf", "ts", "open", "high", "low", "close", "volume", "quote_volume", "source", "ingested_at"]
TF_SECONDS = {"1d": 86400, "4h": 4 * 3600, "1h": 3600}


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in BAR_COLUMNS})


def normalize_rows(rows: list, symbol: str, tf: str, source: str, now_utc: pd.Timestamp | None = None) -> pd.DataFrame:
    """rows: [ts_ms, open, high, low, close, volume, quote_volume?]（Bitget / Binance 公共格式都可）。
    自动：数值化、去重（保留最后）、排序、剔除未收盘 K 线、剔除非法 OHLC。"""
    if not rows:
        return empty_bars()
    now_utc = now_utc or pd.Timestamp.now(tz="UTC")
    recs = []
    for r in rows:
        if isinstance(r, dict):
            r = [r.get("ts") or r.get("t") or r.get("openTime"), r.get("open") or r.get("o"), r.get("high") or r.get("h"),
                 r.get("low") or r.get("l"), r.get("close") or r.get("c"), r.get("baseVolume") or r.get("volume") or r.get("v"),
                 r.get("quoteVolume") or r.get("usdtVolume") or r.get("amount")]
        if len(r) < 6:
            continue
        recs.append([r[0], r[1], r[2], r[3], r[4], r[5], r[6] if len(r) > 6 else None])
    df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df = df.dropna(subset=["ts", "open", "high", "low", "close"])
    sec = TF_SECONDS[tf]
    epoch_s = (df["ts"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().round().astype("int64")
    n_before = len(df)
    df = df[epoch_s % sec == 0]                                                   # 对齐周期边界（pandas 3 的 ms 精度也安全）
    dropped_misaligned = n_before - len(df)
    df = df[df["ts"] + pd.Timedelta(seconds=sec) <= now_utc]                     # 剔除未完成 K 线
    ok = (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) & (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9) & (df["low"] > 0)
    df = df[ok]
    df = df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    df["symbol"], df["tf"], df["source"] = symbol, tf, source
    df["ingested_at"] = now_utc
    out = df[BAR_COLUMNS]
    out.attrs["dropped_misaligned"] = int(dropped_misaligned)
    out.attrs["n_input"] = int(n_before)
    return out
