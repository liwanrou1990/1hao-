"""K 线统一列（与 spot90 BAR_COLUMNS 兼容）：symbol, tf, ts(UTC 开盘), open, high, low, close, volume, quote_volume, source, ingested_at。

* TF_SECONDS 覆盖 15m~1d；1w 以 Binance 规则（周一 00:00 UTC 开盘）单独对齐。
* normalize_rows：数值化、去重、排序、对齐周期边界、剔除未收盘 K 线（收盘时间 > now 的一律删除）、剔除非法 OHLC。
* bar_close_time：K 线收盘时刻 = ts + 周期长度；跨周期对齐一律用“收盘时刻 ≤ 决策时刻”判定，杜绝用未收盘的高周期 K 线。
"""
from __future__ import annotations

import pandas as pd

BAR_COLUMNS = ["symbol", "tf", "ts", "open", "high", "low", "close", "volume", "quote_volume", "source", "ingested_at"]
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400, "1w": 604800}
WEEK_OFFSET_S = 4 * 86400          # 1970-01-01 是周四；周一 00:00 UTC 对齐需偏移 4 天


def tf_seconds(tf: str) -> int:
    if tf not in TF_SECONDS:
        raise KeyError(f"未知周期 {tf}")
    return TF_SECONDS[tf]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in BAR_COLUMNS})


def bar_close_time(ts: pd.Series | pd.Timestamp, tf: str):
    return ts + pd.Timedelta(seconds=tf_seconds(tf))


def normalize_rows(rows: list, symbol: str, tf: str, source: str, now_utc: pd.Timestamp | None = None) -> pd.DataFrame:
    """rows: [ts_ms, open, high, low, close, volume, (quote_volume)]（Binance / Bitget 公共格式）。"""
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
        recs.append([r[0], r[1], r[2], r[3], r[4], r[5], r[7] if len(r) > 7 else (r[6] if len(r) > 6 else None)])
    df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df = df.dropna(subset=["ts", "open", "high", "low", "close"])
    sec = tf_seconds(tf)
    epoch_s = (df["ts"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().round().astype("int64")
    aligned = ((epoch_s - WEEK_OFFSET_S) % sec == 0) if tf == "1w" else (epoch_s % sec == 0)
    n_before = len(df)
    df = df[aligned]
    df = df[df["ts"] + pd.Timedelta(seconds=sec) <= now_utc]                     # 剔除未完成 K 线
    ok = (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) & (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9) & (df["low"] > 0)
    df = df[ok].drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    df["symbol"], df["tf"], df["source"] = symbol, tf, source
    df["ingested_at"] = now_utc
    out = df[BAR_COLUMNS].copy()
    out.attrs["dropped_misaligned"] = int(n_before - len(df))
    return out


def resample_bars(df: pd.DataFrame, src_tf: str, dst_tf: str) -> pd.DataFrame:
    """由低周期合成高周期（仅用完整窗口；最后一个不完整窗口丢弃）。1w 以周一 00:00 UTC 为界。"""
    if df is None or df.empty:
        return empty_bars()
    sec_src, sec_dst = tf_seconds(src_tf), tf_seconds(dst_tf)
    if sec_dst % sec_src != 0:
        raise ValueError(f"{src_tf} 不能整除合成 {dst_tf}")
    d = df.sort_values("ts").set_index("ts")
    rule = "W-MON" if dst_tf == "1w" else f"{sec_dst}s"
    kw = {"label": "left", "closed": "left"}
    agg = d.resample(rule, **kw).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "quote_volume": "sum"})
    cnt = d["close"].resample(rule, **kw).count()
    agg = agg[cnt == sec_dst // sec_src].dropna(subset=["open"])
    out = agg.reset_index()
    out["symbol"], out["tf"], out["source"] = df["symbol"].iloc[0], dst_tf, f"resampled_from_{src_tf}"
    out["ingested_at"] = pd.Timestamp.now(tz="UTC")
    return out[BAR_COLUMNS]
