"""第十阶段数据：BTC/ETH 日线 2017-08 → 最新已收盘日（UTC），独立副本 + 验证清单 + OKX 抽样交叉比对。

来源优先级：仓内 data_store/brando_pa/bars/<sym>_1d.parquet（Binance 现货，静态镜像）→ data_store/brando_pa/forward_bars（OKX 现货，最新日）→
缺口时通过 OKX 公开接口 history-candles 补齐（记录来源）。只保留已收盘日线（ts + 1 天 ≤ now）。禁止未来函数：本模块只整理数据，不做任何信号计算。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import own_data_dir
from ..data.store import BarStore

SYMS = ["BTCUSDT", "ETHUSDT"]
OKX_INST = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}
DAY = pd.Timedelta(days=1)


def phase10_data_dir(c: dict | None = None) -> Path:
    d = own_data_dir(c) / "phase10_holding"; d.mkdir(parents=True, exist_ok=True); return d


def _normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "source"])
    out = df[["ts", "open", "high", "low", "close", "volume"]].copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True).dt.floor("D")
    for k in ("open", "high", "low", "close", "volume"):
        out[k] = out[k].astype(float)
    out["source"] = df["source"].astype(str) if "source" in df else source
    return out.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def okx_daily(symbol: str, start: pd.Timestamp, end: pd.Timestamp, timeout: float = 20.0) -> pd.DataFrame:
    """OKX 公开 history-candles（1Dutc），分页向过去取；只返回 [start, end] 内的已收盘日线。"""
    rows = []; after = int((end + DAY).timestamp() * 1000)
    with httpx.Client(timeout=timeout) as cl:
        for _ in range(60):
            r = cl.get("https://www.okx.com/api/v5/market/history-candles", params={"instId": OKX_INST[symbol], "bar": "1Dutc", "after": str(after), "limit": "100"})
            r.raise_for_status(); data = r.json().get("data", [])
            if not data:
                break
            for x in data:
                ts = pd.Timestamp(int(x[0]), unit="ms", tz="UTC")
                if x[8] == "1":                                            # confirm == 1 → 已收盘
                    rows.append({"ts": ts, "open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])})
            after = int(data[-1][0])
            if pd.Timestamp(after, unit="ms", tz="UTC") <= start:
                break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[(df["ts"] >= start) & (df["ts"] <= end)]; df["source"] = "okx_spot"
    return _normalize(df, "okx_spot")


def validate_daily(df: pd.DataFrame) -> dict:
    ts = df["ts"]; d = ts.diff().dt.total_seconds().div(86400)
    gaps = df.loc[d > 1.5, "ts"].dt.strftime("%Y-%m-%d").tolist()
    bad_ohlc = int(((df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))).sum())
    nonpos = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    jumps = df["close"].pct_change().abs(); big = df.loc[jumps > 0.35, "ts"].dt.strftime("%Y-%m-%d").tolist()
    return {"n": int(len(df)), "first": str(ts.iloc[0].date()) if len(df) else None, "last": str(ts.iloc[-1].date()) if len(df) else None, "gaps_after": gaps[:20], "n_gaps": len(gaps), "duplicates": int(ts.duplicated().sum()),
            "bad_ohlc_rows": bad_ohlc, "non_positive_rows": nonpos, "daily_moves_gt_35pct": big, "sources": df["source"].value_counts().to_dict(), "ok": not gaps and bad_ohlc == 0 and nonpos == 0 and int(ts.duplicated().sum()) == 0}


def build_dataset(c: dict | None = None, now: pd.Timestamp | None = None, cross_check: bool = True, cross_check_days: int = 400) -> dict:
    """生成独立副本 <phase10_dir>/<sym>_1d.parquet 与 data_manifest.json。返回 manifest。"""
    now = now or pd.Timestamp.now(tz="UTC"); out_dir = phase10_data_dir(c); manifest = {"built_at_utc": str(now), "symbols": {}, "rule": "closed daily bars only (ts + 1d <= now, UTC day boundary); decisions use close(t), execution open(t+1)"}
    own = BarStore(); fwd = BarStore(root=own_data_dir(c) / "forward_bars", fallback_root=None)
    for s in SYMS:
        a = _normalize(own.load(s, "1d"), "binance_spot_via_static_klines"); b = _normalize(fwd.load(s, "1d", allow_fallback=False), "okx_spot")
        df = pd.concat([a, b[b["ts"] > (a["ts"].max() if len(a) else pd.Timestamp("2000-01-01", tz="UTC"))]], ignore_index=True) if len(b) else a
        df = df[df["ts"] + DAY <= now].reset_index(drop=True)
        v = validate_daily(df); filled = []
        if v["n_gaps"]:
            try:
                ok = okx_daily(s, df["ts"].min(), df["ts"].max()); add = ok[~ok["ts"].isin(df["ts"])]
                if len(add):
                    df = pd.concat([df, add], ignore_index=True).sort_values("ts").reset_index(drop=True); filled = add["ts"].dt.strftime("%Y-%m-%d").tolist(); v = validate_daily(df)
            except Exception as e:  # noqa: BLE001
                v["gap_fill_error"] = f"{type(e).__name__}: {e}"
        xc = None
        if cross_check:
            try:
                ref = okx_daily(s, df["ts"].max() - pd.Timedelta(days=cross_check_days), df["ts"].max())
                m = df.merge(ref[["ts", "close"]], on="ts", suffixes=("", "_okx"))
                if len(m):
                    rel = ((m["close"] - m["close_okx"]).abs() / m["close_okx"]); xc = {"n_overlap": int(len(m)), "rel_close_diff_median": float(rel.median()), "rel_close_diff_max": float(rel.max()), "worst_day": str(m.loc[rel.idxmax(), "ts"].date()), "note": "Binance 镜像 vs OKX 现货日收盘；交易所差异只记录不修正"}
                else:
                    xc = {"n_overlap": 0}
            except Exception as e:  # noqa: BLE001
                xc = {"error": f"{type(e).__name__}: {e}"}
        df.to_parquet(out_dir / f"{s}_1d.parquet", index=False)
        manifest["symbols"][s] = {"validation": v, "gap_filled_days_from_okx": filled, "cross_check_okx": xc, "file": str(out_dir / f"{s}_1d.parquet")}
    manifest["latest_complete_day"] = min(manifest["symbols"][s]["validation"]["last"] for s in SYMS)
    atomic_write_json(out_dir / "data_manifest.json", manifest, indent=1, default=str); return manifest


def load_dataset(c: dict | None = None) -> dict:
    d = phase10_data_dir(c); out = {}
    for s in SYMS:
        p = d / f"{s}_1d.parquet"
        if not p.exists():
            raise FileNotFoundError(f"{p} 不存在：先运行 phase10-data")
        df = pd.read_parquet(p); df["ts"] = pd.to_datetime(df["ts"], utc=True); out[s] = df.sort_values("ts").reset_index(drop=True)
    return out
