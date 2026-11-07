"""行情来源适配器（只读、无密钥、无下单路径）。

抽象接口 MarketDataAdapter：
  * fetch_klines(symbol, tf, start, end) → 统一 K 线 DataFrame（已收盘）
  * ticker(symbol) → {price, ts, source}
  * funding_history / open_interest → 可选，不可用返回空并写明原因
实现：
  * StaticKlinesAdapter：GitHub 公开 Binance 现货 K 线缓存（本环境唯一可达的 K 线来源；每日更新，最新 K 线可能落后一天）
  * BitgetPublicAdapter：复用 spot90.data.bitget_v3 / derivatives（api.bitget.com 可达时才可用）
  * LiveReferencePrice：复用 spot90.monitor.live（Bitget → DefiLlama → CoinGecko）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

from ..config import cfg, own_data_dir
from ..schema import empty_bars, normalize_rows, tf_seconds

SOURCE_STATIC = "binance_spot_via_static_klines"


class MarketDataAdapter(Protocol):
    name: str

    def probe(self) -> dict: ...
    def fetch_klines(self, symbol: str, tf: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame: ...
    def ticker(self, symbol: str) -> dict: ...
    def funding_history(self, symbol: str) -> pd.DataFrame: ...
    def open_interest_history(self, symbol: str) -> pd.DataFrame: ...


def _add_months(d: date, months: int) -> date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def window_starts(tf: str, today: date | None = None, c: dict | None = None) -> list[date]:
    w = (c or cfg())["data"]["static_klines"]["windows"][tf]
    anchor = date.fromisoformat(w["anchor"])
    today = today or datetime.now(timezone.utc).date()
    out, d = [], anchor
    while d <= today:
        out.append(d)
        d = _add_months(d, int(w["step"])) if w["unit"] == "months" else d + pd.Timedelta(weeks=int(w["step"])).to_pytimedelta()
        if isinstance(d, datetime):
            d = d.date()
    return out


@dataclass
class StaticKlinesAdapter:
    name: str = "static_klines"
    cache_root: Path | None = None
    transport: httpx.BaseTransport | None = None
    timeout: float = 30.0
    requests: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        c = cfg()["data"]["static_klines"]
        self.raw_base = c["raw_base"].rstrip("/")
        self.cache_root = self.cache_root or (own_data_dir() / "raw_cache" / "static_klines")
        self.client = httpx.Client(timeout=self.timeout, transport=self.transport, headers={"User-Agent": "brando_pa/0.1 (read-only research)"})

    def probe(self) -> dict:
        try:
            r = self.client.get(f"{self.raw_base}/BTCUSDT/1d/2016-01-01.json")
            return {"ok": r.status_code == 200, "detail": f"raw.githubusercontent.com HTTP {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": f"raw.githubusercontent.com 不可达：{type(e).__name__}"}

    def _fetch_window(self, symbol: str, tf: str, start: date, refresh: bool) -> list | None:
        p = self.cache_root / symbol / tf / f"{start.isoformat()}.json"
        if p.exists() and not refresh:
            try:
                return json.loads(p.read_text())
            except json.JSONDecodeError:
                p.unlink(missing_ok=True)
        url = f"{self.raw_base}/{symbol}/{tf}/{start.isoformat()}.json"
        for attempt in range(4):
            try:
                self.requests += 1
                r = self.client.get(url)
            except httpx.HTTPError as e:
                if attempt == 3:
                    self.errors.append(f"{url}: {type(e).__name__}")
                    return json.loads(p.read_text()) if p.exists() else None
                time.sleep(1.0 * (attempt + 1)); continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                time.sleep(1.0 * (attempt + 1)); continue
            try:
                rows = r.json()
            except ValueError:
                self.errors.append(f"{url}: 非 JSON"); return None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rows))
            return rows
        self.errors.append(f"{url}: HTTP {r.status_code}")
        return json.loads(p.read_text()) if p.exists() else None

    def fetch_klines(self, symbol: str, tf: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None, refresh_recent: int = 2) -> pd.DataFrame:
        """下载该周期全部窗口；最近 refresh_recent 个窗口每次刷新（缓存每日更新，最后一个窗口会增长）。"""
        starts = window_starts(tf)
        if start is not None:
            keep = [s for s in starts if pd.Timestamp(s, tz="UTC") <= start]
            first_idx = max(len(keep) - 1, 0)
            starts = starts[first_idx:]
        rows: list = []
        for i, s in enumerate(starts):
            w = self._fetch_window(symbol, tf, s, refresh=i >= len(starts) - refresh_recent)
            if w:
                rows += w
        df = normalize_rows(rows, symbol, tf, SOURCE_STATIC)
        if start is not None and not df.empty:
            df = df[df["ts"] >= start]
        if end is not None and not df.empty:
            df = df[df["ts"] <= end]
        return df.reset_index(drop=True)

    def ticker(self, symbol: str) -> dict:
        from spot90.monitor.live import fetch_live_prices  # 复用：Bitget → DefiLlama → CoinGecko
        return fetch_live_prices([symbol]).get(symbol, {})

    def funding_history(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", "ts", "funding_rate", "source"]).assign(_reason="static_klines 不提供衍生品数据")

    def open_interest_history(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", "ts", "open_interest", "source"]).assign(_reason="static_klines 不提供衍生品数据")


@dataclass
class BitgetPublicAdapter:
    """Bitget 公开接口（只读）。复用 spot90 的 V3/V2 K 线客户端与衍生品客户端；不可达时 probe 返回 ok=False。"""
    name: str = "bitget_public"

    def __post_init__(self):
        from spot90.data.bitget_v3 import BitgetSpotHistoryClient
        from spot90.data.derivatives import BitgetDerivativesClient
        self.spot = BitgetSpotHistoryClient()
        self.deriv = BitgetDerivativesClient()

    def probe(self) -> dict:
        return self.spot.probe()

    def fetch_klines(self, symbol: str, tf: str, start=None, end=None) -> pd.DataFrame:
        if tf not in ("1d", "4h", "1h"):
            return empty_bars()
        df = self.spot.fetch(symbol, tf)
        return df if start is None else df[df["ts"] >= start].reset_index(drop=True)

    def ticker(self, symbol: str) -> dict:
        from spot90.monitor.live import fetch_live_prices
        return fetch_live_prices([symbol]).get(symbol, {})

    def funding_history(self, symbol: str) -> pd.DataFrame:
        try:
            return self.deriv.funding_history(symbol)
        except Exception as e:  # noqa: BLE001
            return pd.DataFrame(columns=["symbol", "ts", "funding_rate", "source"]).assign(_reason=f"{type(e).__name__}")

    def open_interest_history(self, symbol: str) -> pd.DataFrame:
        p = self.deriv.root / f"open_interest_{symbol}.parquet"
        return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["symbol", "ts", "open_interest", "source"])


def make_adapter(name: str | None = None) -> MarketDataAdapter:
    name = name or cfg()["data"].get("exchange_adapter", "static_klines")
    if name == "bitget_public":
        a = BitgetPublicAdapter()
        if a.probe().get("ok"):
            return a
        return StaticKlinesAdapter()
    return StaticKlinesAdapter()


def utcnow() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def bars_are_stale(bars: pd.DataFrame, tf: str, now: pd.Timestamp | None = None, multiplier: float | None = None) -> tuple[bool, str]:
    """最新已收盘 K 线的收盘时刻距今超过 multiplier×周期 → 过期。"""
    if bars is None or bars.empty:
        return True, "无 K 线"
    now = now or utcnow()
    mult = multiplier or float(cfg()["data"].get("max_bar_age_multiplier", 3))
    last_close = bars["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf))
    age = (now - last_close).total_seconds()
    stale = age > mult * tf_seconds(tf)
    return stale, f"最新 {tf} 收盘 {last_close.isoformat()}，落后 {age/3600:.1f} 小时" + ("（过期）" if stale else "")
