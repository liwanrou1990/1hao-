"""Massive（原 Polygon.io）美股分钟K线客户端 —— 作为原生股票价格的独立基准。

端点（Polygon 兼容，Massive 与 api.polygon.io 并行服务同一 API）：
  GET /v2/aggs/ticker/{ticker}/range/1/minute/{from}/{to}?adjusted=true&sort=asc&limit=50000
  认证：query apiKey=... 或 header Authorization: Bearer ...
返回字段：t(毫秒) o h l c v vw n
套餐限制（公开资料）：Basic 免费 5 次/分钟、仅日终；Starter/Developer 15 分钟延迟、无调用次数限制；历史深度按套餐不同。
本环境未能联网验证（主机被网络策略拒绝），以上以运行时 probe 为准。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pandas as pd

from ..calendar_utils import NY, is_trading_day, session_bounds_utc
from ..config import get_settings
from .base import ProbeResult
from .schema import DataSourceTag, QualityFlag, Venue, normalize_bars


class MassiveClient:
    name = "massive"
    is_simulated = False

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 20.0,
                 transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.massive_api_key
        self.base_url = (base_url or s.massive_base_url).rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self.call_log: list[dict[str, Any]] = []

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        t0 = time.perf_counter()
        resp = self._client.get(path, params=params, headers=headers)
        self.call_log.append({"path": path, "status_code": resp.status_code, "elapsed_s": round(time.perf_counter() - t0, 3),
                              "ts": datetime.now(timezone.utc).isoformat()})
        if resp.status_code in (401, 403):
            raise PermissionError(f"Massive {path}: HTTP {resp.status_code} 权限不足: {resp.text[:200]}")
        if resp.status_code == 429:
            raise RuntimeError(f"Massive {path}: HTTP 429 频率限制: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    def aggregates(self, ticker: str, start: date, end: date, multiplier: int = 1, timespan: str = "minute") -> list[dict[str, Any]]:
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start.isoformat()}/{end.isoformat()}"
        out: list[dict[str, Any]] = []
        data = self._get(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        out.extend(data.get("results", []) or [])
        # 分页
        next_url = data.get("next_url")
        while next_url:
            data = self._get(next_url.replace(self.base_url, ""), {})
            out.extend(data.get("results", []) or [])
            next_url = data.get("next_url")
        return out

    def rows_to_bars(self, rows: list[dict[str, Any]], ticker: str, day: date | None = None) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "n": "txn"})
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df["amount"] = df["volume"] * df.get("vw", df["close"])
        bars = normalize_bars(df, symbol=ticker, venue=Venue.NATIVE, source=str(DataSourceTag.MASSIVE))
        if day is not None:
            if not is_trading_day(day):
                raise ValueError(f"{day} 为非交易日，拒绝生成原生K线")
            o, c = session_bounds_utc(day)
            bars = bars[(bars["ts"] >= o) & (bars["ts"] < c)].reset_index(drop=True)   # 仅常规时段
        else:
            ny = bars["ts"].dt.tz_convert(NY)
            off = (ny.dt.hour * 60 + ny.dt.minute < 570) | (ny.dt.hour * 60 + ny.dt.minute >= 960)
            bars.loc[off, "quality"] |= int(QualityFlag.OFF_HOURS)
        return bars

    def fetch_minute_bars(self, symbol: str, day: date) -> pd.DataFrame:
        if not self.has_credentials:
            raise PermissionError("缺少 MASSIVE_API_KEY")
        if not is_trading_day(day):
            raise ValueError(f"{day} 不是美股交易日")
        return self.rows_to_bars(self.aggregates(symbol, day, day), symbol, day)

    def probe(self) -> ProbeResult:
        res = ProbeResult(source=self.name, ok=False, detail="", has_credentials=self.has_credentials,
                          cost_note="Basic 免费(5 次/分, 日终)；Starter/Developer 付费(15 分钟延迟)；分钟级历史深度随套餐")
        if not self.has_credentials:
            res.detail = "未配置 MASSIVE_API_KEY：无法验证套餐、历史深度与限频"
            res.blocked = True
            return res
        try:
            from ..calendar_utils import previous_trading_day
            d = previous_trading_day(date.today())
            rows = self.aggregates("NVDA", d, d)
            res.endpoints["aggregates(1m)"] = f"ok ({len(rows)} bars for {d})"
            # 历史深度探测：两年前的一天
            d2 = previous_trading_day(date(d.year - 2, d.month, d.day))
            rows2 = self.aggregates("NVDA", d2, d2)
            res.history_span = f"{d2}: {len(rows2)} bars（0 表示该套餐无此深度）"
            res.ok = True
            res.detail = "探测完成"
        except Exception as e:  # noqa: BLE001
            res.detail = f"失败: {e}"
            res.blocked = True
        lat = [c["elapsed_s"] for c in self.call_log]
        res.rate_limit = f"{len(self.call_log)} 次调用，平均延迟 {sum(lat)/max(len(lat),1):.3f}s"
        return res
