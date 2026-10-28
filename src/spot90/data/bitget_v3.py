"""Bitget 公开现货历史 K 线客户端（只读、无密钥）。

优先 V3：GET /api/v3/market/history-candles?category=SPOT&symbol=BTCUSDT&interval=1day&endTime=&limit=200
回退 V2：GET /api/v2/spot/market/history-candles?symbol=BTCUSDT&granularity=1Dutc&endTime=&limit=200

说明
* 周期枚举无法在本容器核对（api.bitget.com 被出网策略拒绝），因此 interval/granularity 采用候选列表自动探测，
  探测结果写入 provenance，在能联网的机器上首次运行即可确定。
* 分页：按 endTime 向前翻页，直到无数据 / 无进展 / 到达起点；页大小不信任 len(page) < limit 作为终止条件。
* 去重、排序、剔除未收盘 K 线、非法 OHLC 在 schema.normalize_rows 统一处理。
* 本模块不包含任何下单、转账或签名接口。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from ..config import cfg
from .schema import TF_SECONDS, empty_bars, normalize_rows

SOURCE_V3 = "bitget_spot_v3"
SOURCE_V2 = "bitget_spot_v2"


class BitgetUnreachable(RuntimeError):
    pass


@dataclass
class FetchLog:
    requests: int = 0
    pages: int = 0
    errors: list[str] = field(default_factory=list)
    api_version: str | None = None
    interval_used: str | None = None
    notes: list[str] = field(default_factory=list)


class BitgetSpotHistoryClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None, transport: httpx.BaseTransport | None = None):
        c = cfg()["data"]["bitget"]
        self.c = c
        self.base_url = (base_url or c["base_url"]).rstrip("/")
        self.client = httpx.Client(timeout=timeout or c["timeout_s"], transport=transport, headers={"User-Agent": "spot90/0.1 (read-only research)"})
        self.min_interval = float(c.get("min_interval_s", 0.08))
        self._last = 0.0
        self.log = FetchLog()

    # ---------- 低层 ----------
    def _get(self, path: str, params: dict[str, Any]) -> dict:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(5):
            try:
                self._last = time.monotonic()
                r = self.client.get(self.base_url + path, params=params)
                self.log.requests += 1
            except httpx.HTTPError as e:                      # 网络/代理层失败
                if attempt == 4:
                    raise BitgetUnreachable(f"{type(e).__name__}: {str(e)[:160]}") from e
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (400, 404):
                # 参数不被接受（例如 interval 枚举不对）：交给调用方换候选
                raise ValueError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            if str(data.get("code")) != "00000":
                raise ValueError(f"code={data.get('code')} msg={data.get('msg')}")
            return data
        raise BitgetUnreachable("重试后仍失败（限频或服务端错误）")

    def probe(self) -> dict[str, Any]:
        try:
            d = self._get("/api/v2/public/time", {})
            return {"ok": True, "server_time": d.get("data"), "detail": "api.bitget.com 可达"}
        except (BitgetUnreachable, ValueError, httpx.HTTPError) as e:
            return {"ok": False, "detail": f"api.bitget.com 不可达或拒绝：{type(e).__name__}: {str(e)[:160]}"}

    # ---------- 单页 ----------
    def _page_v3(self, symbol: str, interval: str, end_ms: int, limit: int) -> list:
        d = self._get(self.c["v3_path"], {"category": self.c["v3_category"], "symbol": symbol, "interval": interval,
                                           "endTime": str(end_ms), "limit": str(limit)})
        data = d.get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("data") or []
        return data or []

    def _page_v2(self, symbol: str, granularity: str, end_ms: int, limit: int) -> list:
        d = self._get(self.c["v2_path"], {"symbol": symbol, "granularity": granularity, "endTime": str(end_ms), "limit": str(limit)})
        return d.get("data") or []

    # ---------- 分页抓取 ----------
    def _paginate(self, page_fn, start_ms: int, end_ms: int, limit: int, max_pages: int = 5000) -> list:
        rows: list = []
        cur_end = end_ms
        seen_oldest = None
        for _ in range(max_pages):
            page = page_fn(cur_end, limit)
            self.log.pages += 1
            if not page:
                break
            rows += page
            oldest = min(int(r[0]) for r in page)
            if seen_oldest is not None and oldest >= seen_oldest:     # 无进展，防死循环
                break
            seen_oldest = oldest
            if oldest <= start_ms:
                break
            cur_end = oldest - 1
        return rows

    def fetch(self, symbol: str, tf: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """抓取 [start, end] 的已收盘 K 线。start 为空 → 尽可能早（翻页直到接口无数据）。"""
        end = end or datetime.now(timezone.utc)
        end_ms = int(end.timestamp() * 1000)
        start_ms = int(start.timestamp() * 1000) if start else 0
        limit = int(self.c.get("page_limit", 200))
        errors: list[str] = []
        # V3 候选
        for iv in self.c["v3_interval_candidates"][tf]:
            try:
                rows = self._paginate(lambda e, l, iv=iv: self._page_v3(symbol, iv, e, l), start_ms, end_ms, limit)
            except ValueError as e:
                errors.append(f"v3 interval={iv}: {e}")
                continue
            if rows:
                self.log.api_version, self.log.interval_used = "v3", iv
                df = normalize_rows(rows, symbol, tf, SOURCE_V3)
                return self._check_tf(df, tf, errors)
            errors.append(f"v3 interval={iv}: 空数据")
        # V2 回退
        for g in self.c["v2_granularity_candidates"][tf]:
            try:
                rows = self._paginate(lambda e, l, g=g: self._page_v2(symbol, g, e, l), start_ms, end_ms, limit)
            except ValueError as e:
                errors.append(f"v2 granularity={g}: {e}")
                continue
            if rows:
                self.log.api_version, self.log.interval_used = "v2", g
                df = normalize_rows(rows, symbol, tf, SOURCE_V2)
                return self._check_tf(df, tf, errors)
            errors.append(f"v2 granularity={g}: 空数据")
        self.log.errors += errors
        return empty_bars()

    def _check_tf(self, df: pd.DataFrame, tf: str, errors: list[str]) -> pd.DataFrame:
        """核对返回数据周期是否与请求一致（防止候选枚举被服务端静默当作别的周期）。"""
        n_in = int(df.attrs.get("n_input", len(df)) or 0)
        if n_in and df.attrs.get("dropped_misaligned", 0) > 0.5 * n_in:
            errors.append(f"超过一半 K 线不在 {tf} 边界上（丢弃 {df.attrs['dropped_misaligned']}/{n_in}），判定周期不符，丢弃")
            self.log.errors += errors
            return empty_bars()
        if len(df) >= 3:
            step = df["ts"].diff().dropna().dt.total_seconds().mode().iloc[0]
            if abs(step - TF_SECONDS[tf]) > 1:
                errors.append(f"返回周期 {step}s 与请求 {tf} 不符，丢弃")
                self.log.errors += errors
                return empty_bars()
        self.log.errors += errors
        return df

    def close(self) -> None:
        self.client.close()
