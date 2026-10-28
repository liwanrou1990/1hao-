"""交叉校验源：DefiLlama coins API（CoinGecko 聚合日收盘价，BTC 2013 年起、ETH 2015 年起）。
只用于：① 校验主数据源收盘价（中位数相对偏差）；② 记录 2017 年之前的价格历史范围。不参与训练（无 OHLC/成交量）。"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from ..config import cfg

SOURCE = "defillama_coingecko_daily_close"


class LlamaClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        c = cfg()["data"]["llama"]
        self.base = c["base"].rstrip("/")
        self.ids = c["ids"]
        self.client = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "spot90/0.1"})

    def first_timestamp(self, cid: str) -> int | None:
        try:
            r = self.client.get(f"{self.base}/prices/first/{cid}")
            r.raise_for_status()
            return int(r.json()["coins"][cid]["timestamp"])
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    def daily_close(self, symbol: str, start_ts: int | None = None, max_requests: int = 60) -> pd.DataFrame:
        cid = self.ids[symbol]
        pts: list[dict] = []
        start = start_ts or self.first_timestamp(cid) or 1_367_107_200     # 起点必须 ≥ 该币首个价格时间，否则接口返回错位数据
        now = int(time.time())
        for _ in range(max_requests):
            if start >= now:
                break
            try:
                r = self.client.get(f"{self.base}/chart/{cid}", params={"start": start, "span": 500, "period": "1d"})
                r.raise_for_status()
            except httpx.HTTPError:
                break
            p = r.json().get("coins", {}).get(cid, {}).get("prices", [])
            if not p:
                break
            pts += p
            start = int(p[-1]["timestamp"]) + 86400
            time.sleep(0.2)
        if not pts:
            return pd.DataFrame(columns=["date", "close_ref", "source"])
        df = pd.DataFrame(pts)
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.floor("D")
        df = df.rename(columns={"price": "close_ref"}).drop_duplicates("date", keep="last").sort_values("date")
        df["source"] = SOURCE
        return df[["date", "close_ref", "source"]].reset_index(drop=True)
