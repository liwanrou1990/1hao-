"""离线导入真实历史数据（绕过云环境网络限制）：K 线 / 逐笔成交 / 盘口深度 CSV（或 JSON）。

支持格式（自动识别列名，大小写与分隔符不敏感；也可用 column_map 显式映射）：
  * Bitget 公开接口导出：history-candles 行 [ts, open, high, low, close, baseVol, quoteVol, usdtVol]、fills-history 行 {tradeId, price, size, side, ts}
    （随附脚本 scripts/fetch_bitget_public.py 在有网络的机器上生成 data_in/<SYMBOL>/<day>/{kline,trades,depth}.csv）；
  * Tardis.dev：trades（exchange,symbol,timestamp(µs),local_timestamp,id,side,price,amount）、book_snapshot_N（asks[i].price/amount, bids[i].price/amount）、quotes；
  * 通用 OHLCV：含 time/timestamp/date 与 open/high/low/close(/volume/amount) 列。
所有导入记录 source = "REAL_HISTORICAL:csv:<文件名>"（quality 不含 SIMULATED 位）；时间列支持秒/毫秒/微秒/纳秒/ISO。
导入不做任何价格修饰，不填补缺口；缺口由质量标记如实记录。
"""
from __future__ import annotations

import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .quality import apply_quality_flags, research_ready
from .schema import Venue, normalize_bars

SOURCE_PREFIX = "REAL_HISTORICAL:csv"
_TS_CANDIDATES = ("ts", "ts_ms", "ts_us", "ts_ns", "timestamp", "timestamp_ms", "time", "time_ms", "datetime", "date", "open_time", "open_time_ms",
                  "candle_begin_time", "t", "local_timestamp")


def _read_any(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            inner = [n for n in z.namelist() if n.lower().endswith((".csv", ".json", ".txt"))]
            if not inner:
                raise ValueError(f"{path} 内无 csv/json")
            with z.open(inner[0]) as f:
                return _read_any_buffer(f, inner[0])
    with open(path, "rb") as f:
        return _read_any_buffer(f, path.name)


def _read_any_buffer(f, name: str) -> pd.DataFrame:
    if name.lower().endswith(".json"):
        data = json.load(f)
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or data
        df = pd.DataFrame(data)
        if df.shape[1] >= 6 and all(isinstance(c, int) for c in df.columns):      # Bitget candles: 位置列
            df.columns = ["ts", "open", "high", "low", "close", "volume", "amount", "usdt_volume"][: df.shape[1]]
        return df
    raw = f.read()
    text = raw.decode("utf-8-sig", errors="ignore")
    sep = "\t" if text.count("\t") > text.count(",") else ","
    first = text.splitlines()[0] if text else ""
    has_header = bool(re.search(r"[A-Za-z]", first))
    from io import StringIO
    df = pd.read_csv(StringIO(text), sep=sep, header=0 if has_header else None)
    if not has_header and df.shape[1] >= 5:
        df.columns = ["ts", "open", "high", "low", "close", "volume", "amount", "usdt_volume"][: df.shape[1]]
    return df


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [re.sub(r"[^a-z0-9_\[\]\.]", "_", str(c).strip().lower()) for c in df.columns]
    return df


def _parse_ts(s: pd.Series) -> pd.Series:
    if np.issubdtype(s.dtype, np.number) or s.astype(str).str.fullmatch(r"\d+(\.\d+)?").all():
        v = pd.to_numeric(s, errors="coerce").astype(float)
        mag = float(np.nanmedian(v))
        unit = "s" if mag < 1e11 else "ms" if mag < 1e14 else "us" if mag < 1e17 else "ns"
        return pd.to_datetime(v, unit=unit, utc=True)
    return pd.to_datetime(s, utc=True, errors="coerce")


def _pick(df: pd.DataFrame, names: tuple[str, ...], column_map: dict | None, key: str) -> str | None:
    if column_map and key in column_map and column_map[key] in df:
        return column_map[key]
    for n in names:
        if n in df:
            return n
    if key == "ts":                                              # 兜底：以 ts/time/timestamp 开头的列
        for c in df.columns:
            if str(c).startswith(("ts", "time", "timestamp")):
                return c
    return None


# ----------------------------------------------------------------------------- K 线
def parse_kline_csv(path: str | Path, symbol: str, column_map: dict | None = None, tf: str = "1m") -> pd.DataFrame:
    df = _norm_cols(_read_any(path))
    ts_col = _pick(df, _TS_CANDIDATES, column_map, "ts")
    if ts_col is None:
        raise ValueError(f"{path}: 找不到时间列（{_TS_CANDIDATES}）")
    cols = {}
    for k, names in (("open", ("open", "o")), ("high", ("high", "h")), ("low", ("low", "l")), ("close", ("close", "c")),
                     ("volume", ("volume", "vol", "base_volume", "basevol", "v", "size")), ("amount", ("amount", "quote_volume", "quotevol", "turnover", "usdt_volume", "usdtvol", "quote_vol"))):
        c = _pick(df, names, column_map, k)
        if c is not None:
            cols[k] = c
    for k in ("open", "high", "low", "close"):
        if k not in cols:
            raise ValueError(f"{path}: 缺少 {k} 列；可用 column_map 映射。现有列：{list(df.columns)}")
    out = pd.DataFrame({"ts": _parse_ts(df[ts_col])})
    for k, c in cols.items():
        out[k] = pd.to_numeric(df[c], errors="coerce")
    if "amount" not in out and "volume" in out:
        out["amount"] = out["volume"] * (out["open"] + out["close"]) / 2
    out["tf"] = tf
    out = out.dropna(subset=["ts", "close"]).drop_duplicates("ts", keep="last").sort_values("ts")
    bars = normalize_bars(out, symbol=symbol, venue=Venue.TOKEN, source=f"{SOURCE_PREFIX}:{Path(path).name}")
    return research_ready(apply_quality_flags(bars))


# ----------------------------------------------------------------------------- 逐笔成交
def parse_trades_csv(path: str | Path, symbol: str, column_map: dict | None = None) -> pd.DataFrame:
    df = _norm_cols(_read_any(path))
    ts_col = _pick(df, ("ts", "timestamp", "time", "datetime", "trade_time", "t"), column_map, "ts")
    px = _pick(df, ("price", "p", "px"), column_map, "price")
    qty = _pick(df, ("size", "amount", "qty", "quantity", "q", "vol", "volume"), column_map, "size")
    if ts_col is None or px is None or qty is None:
        raise ValueError(f"{path}: 逐笔成交需要时间/价格/数量列；现有列：{list(df.columns)}")
    side = _pick(df, ("side", "taker_side", "direction"), column_map, "side")
    tid = _pick(df, ("tradeid", "trade_id", "id"), column_map, "id")
    out = pd.DataFrame({"ts": _parse_ts(df[ts_col]), "price": pd.to_numeric(df[px], errors="coerce"), "amount": pd.to_numeric(df[qty], errors="coerce")})
    out["side"] = df[side].astype(str).str.lower() if side else None
    out["tx_hash"] = df[tid].astype(str) if tid else None
    out = out.dropna(subset=["ts", "price", "amount"])
    out["value"] = out["price"] * out["amount"]
    out["ts_ny"] = out["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["symbol"], out["chain"], out["contract"] = symbol, "csv", None
    out["source"], out["ingested_at"] = f"{SOURCE_PREFIX}:{Path(path).name}", pd.Timestamp.now(tz="UTC")
    return out[["symbol", "chain", "contract", "ts", "ts_ny", "side", "price", "amount", "value", "tx_hash", "source", "ingested_at"]].sort_values("ts").reset_index(drop=True)


# ----------------------------------------------------------------------------- 盘口深度
def _depth_from_levels(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict[str, Any]:
    bids = [(p, q) for p, q in bids if p > 0 and q > 0]
    asks = [(p, q) for p, q in asks if p > 0 and q > 0]
    if not bids or not asks:
        return {}
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    mid = (bb + ba) / 2
    out = {"mid": mid, "spread_bps": (ba - bb) / mid * 1e4, "levels": len(bids) + len(asks)}
    for bp in (10, 25, 50, 100):
        lo, hi = mid * (1 - bp / 1e4), mid * (1 + bp / 1e4)
        out[f"bid_usd_{bp}bp"] = sum(p * q for p, q in bids if p >= lo)
        out[f"ask_usd_{bp}bp"] = sum(p * q for p, q in asks if p <= hi)
    return out


def parse_depth_csv(path: str | Path, symbol: str, column_map: dict | None = None) -> pd.DataFrame:
    """支持：Tardis book_snapshot（asks[i].price/amount, bids[i].price/amount 列）；
    Bitget orderbook 导出（bids/asks 为 JSON 数组字符串）；汇总格式（已含 bid_usd_10bp… 列）。"""
    df = _norm_cols(_read_any(path))
    ts_col = _pick(df, _TS_CANDIDATES, column_map, "ts")
    if ts_col is None:
        raise ValueError(f"{path}: 找不到时间列")
    rows = []
    ts = _parse_ts(df[ts_col])
    ask_px = sorted([c for c in df.columns if re.fullmatch(r"asks\[\d+\]\.price", c)], key=lambda c: int(re.findall(r"\d+", c)[0]))
    bid_px = sorted([c for c in df.columns if re.fullmatch(r"bids\[\d+\]\.price", c)], key=lambda c: int(re.findall(r"\d+", c)[0]))
    if ask_px and bid_px:
        for i in range(len(df)):
            r = df.iloc[i]
            asks = [(float(r[c]), float(r[c.replace(".price", ".amount")])) for c in ask_px if pd.notna(r[c])]
            bids = [(float(r[c]), float(r[c.replace(".price", ".amount")])) for c in bid_px if pd.notna(r[c])]
            d = _depth_from_levels(bids, asks)
            if d:
                rows.append({"ts": ts.iloc[i], **d})
    elif "bids" in df and "asks" in df:
        for i in range(len(df)):
            r = df.iloc[i]
            bids = [(float(p), float(q)) for p, q in json.loads(r["bids"])]
            asks = [(float(p), float(q)) for p, q in json.loads(r["asks"])]
            d = _depth_from_levels(bids, asks)
            if d:
                rows.append({"ts": ts.iloc[i], **d})
    elif "bid_usd_10bp" in df:
        for i in range(len(df)):
            r = df.iloc[i]
            rows.append({"ts": ts.iloc[i], **{k: (float(r[k]) if k in df and pd.notna(r[k]) else None) for k in
                                              ("mid", "spread_bps", "bid_usd_10bp", "ask_usd_10bp", "bid_usd_25bp", "ask_usd_25bp", "bid_usd_50bp", "ask_usd_50bp", "bid_usd_100bp", "ask_usd_100bp", "levels")}})
    else:
        raise ValueError(f"{path}: 无法识别深度格式；现有列：{list(df.columns)[:12]}")
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["ts_ny"] = out["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["symbol"], out["source"], out["ingested_at"] = symbol, f"{SOURCE_PREFIX}:{Path(path).name}", pd.Timestamp.now(tz="UTC")
    return out.sort_values("ts").reset_index(drop=True)


# ----------------------------------------------------------------------------- 导入
def import_file(store, kind: str, path: str | Path, symbol: str, column_map: dict | None = None, tf: str = "1m") -> dict:
    if kind == "kline":
        bars = parse_kline_csv(path, symbol, column_map, tf)
        n = store.upsert_bars(bars)
        days = sorted(set(pd.to_datetime(bars["ts_ny"]).dt.date)) if len(bars) else []
        return {"kind": kind, "symbol": symbol, "rows": n, "days": [d.isoformat() for d in days], "source": f"{SOURCE_PREFIX}:{Path(path).name}",
                "gaps": int((bars["quality"].astype(int) & 1 > 0).sum()) if len(bars) else 0}
    if kind == "trades":
        tx = parse_trades_csv(path, symbol, column_map)
        if tx.empty:
            return {"kind": kind, "symbol": symbol, "rows": 0}
        days = sorted(set(pd.to_datetime(tx["ts_ny"]).dt.date))
        with store._lock:
            store.con.register("new_tx", tx)
            for d in days:
                store.con.execute("DELETE FROM token_trades WHERE symbol=? AND CAST(ts_ny AS DATE)=?", [symbol, d])
            store.con.execute("INSERT INTO token_trades SELECT * FROM new_tx")
            store.con.unregister("new_tx")
        return {"kind": kind, "symbol": symbol, "rows": int(len(tx)), "days": [d.isoformat() for d in days], "source": f"{SOURCE_PREFIX}:{Path(path).name}"}
    if kind == "depth":
        dp = parse_depth_csv(path, symbol, column_map)
        n = store.upsert_depth(dp)
        days = sorted(set(pd.to_datetime(dp["ts_ny"]).dt.date)) if n else []
        return {"kind": kind, "symbol": symbol, "rows": n, "days": [d.isoformat() for d in days], "source": f"{SOURCE_PREFIX}:{Path(path).name}"}
    raise ValueError("kind 必须是 kline / trades / depth")


def import_dir(store, root: str | Path, day: date | None = None) -> list[dict]:
    """扫描 data_in/<SYMBOL>/<YYYY-MM-DD>/{kline,trades,depth}.(csv|json|zip) 并导入；day 指定时只导入该日。"""
    root = Path(root)
    out = []
    if not root.exists():
        return out
    for sym_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for day_dir in sorted(p for p in sym_dir.iterdir() if p.is_dir()):
            try:
                d = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if day and d != day:
                continue
            for kind in ("kline", "trades", "depth"):
                for f in sorted(day_dir.glob(f"{kind}.*")):
                    try:
                        out.append(import_file(store, kind, f, sym_dir.name.upper()) | {"file": str(f)})
                    except Exception as e:  # noqa: BLE001
                        out.append({"kind": kind, "symbol": sym_dir.name.upper(), "file": str(f), "error": str(e)})
    return out
