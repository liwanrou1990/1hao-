"""实时现货报价（前瞻成交用）：OKX ticker → Coinbase Exchange ticker。返回 Bid/Ask、报价时间戳、来源、年龄。
只读公开接口，无密钥；不可达或过期时返回 None，由成交逻辑决定“估算”或“暂停”。"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pandas as pd

from ..data.sources import utcnow
from .datafeed import PAIR


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    ts: pd.Timestamp          # 交易所报价时间戳（UTC）
    source: str
    fetched_at: pd.Timestamp

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def age_s(self, now: pd.Timestamp) -> float:
        return float((now - self.ts).total_seconds())

    def as_dict(self, now: pd.Timestamp) -> dict:
        return {"symbol": self.symbol, "bid": self.bid, "ask": self.ask, "mid": self.mid, "spread_bps": (self.ask - self.bid) / self.mid * 1e4 if self.mid else None, "quote_ts": str(self.ts), "fetched_at": str(self.fetched_at), "source": self.source, "age_s": self.age_s(now)}


class QuoteService:
    def __init__(self, priority: list[str] | None = None, timeout: float = 8.0, client: httpx.Client | None = None):
        self.priority = list(priority or ["okx_spot", "coinbase_exchange"]); self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": "brando-pa-research/0.9"}); self.errors: list[str] = []

    def _okx(self, symbol: str) -> Quote:
        r = self.client.get("https://www.okx.com/api/v5/market/ticker", params={"instId": PAIR[symbol]["okx"]}); r.raise_for_status(); d = r.json()["data"][0]
        return Quote(symbol, float(d["bidPx"]), float(d["askPx"]), pd.Timestamp(int(d["ts"]), unit="ms", tz="UTC"), "okx_spot", utcnow())

    def _coinbase(self, symbol: str) -> Quote:
        r = self.client.get(f"https://api.exchange.coinbase.com/products/{PAIR[symbol]['coinbase']}/ticker"); r.raise_for_status(); d = r.json()
        return Quote(symbol, float(d["bid"]), float(d["ask"]), pd.Timestamp(d["time"]).tz_convert("UTC") if pd.Timestamp(d["time"]).tzinfo else pd.Timestamp(d["time"], tz="UTC"), "coinbase_exchange", utcnow())

    def get(self, symbol: str) -> Quote | None:
        for name in self.priority:
            fn = {"okx_spot": self._okx, "coinbase_exchange": self._coinbase}.get(name)
            if fn is None:
                continue
            try:
                q = fn(symbol)
                if q.bid > 0 and q.ask >= q.bid:
                    return q
                self.errors.append(f"{name} {symbol}: invalid quote bid={q.bid} ask={q.ask}")
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{name} {symbol}: {type(e).__name__}: {str(e)[:100]}")
        return None
