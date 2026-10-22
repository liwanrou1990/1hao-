"""加密货币行情客户端（只读，无密钥）。

来源：Bitget 交易所公开现货 K 线 `GET /api/v2/spot/market/history-candles`（无需鉴权），作为 BTC/ETH/SOL/HYPE 等
主流币的真实历史行情；钱包内 swap 成交价与交易所现货可能存在价差，报告中标注来源 `bitget_spot_public`。
网络不可达 / 返回异常时 probe 返回 blocked，流水线退回 SIMULATED 并明确标注，不会用模拟行情冒充真实行情。
不包含任何下单、转账、签名接口。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd

from ..calendar_utils import NY
from ..config import asset_meta, get_settings
from .base import ProbeResult
from .schema import Venue, normalize_bars

PUBLIC_BASE = "https://api.bitget.com"
GRANULARITY = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h"}
SOURCE_TAG = "bitget_spot_public"


def crypto_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """加密货币“交易日”定义为 America/New_York 自然日 [00:00, 24:00)，与股票交易日对齐。"""
    start = datetime(day.year, day.month, day.day, tzinfo=NY)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


class BitgetSpotPublicClient:
    name = "bitget_spot_public"
    is_simulated = False
    has_credentials = True          # 公开接口无需密钥

    def __init__(self, base_url: str | None = None, timeout: float = 15.0):
        self.base_url = (base_url or PUBLIC_BASE).rstrip("/")
        self.client = httpx.Client(timeout=timeout)
        self.call_log: list[dict] = []

    def _get(self, path: str, params: dict) -> dict:
        t0 = time.time()
        r = self.client.get(self.base_url + path, params=params)
        self.call_log.append({"path": path, "status": r.status_code, "ms": round((time.time() - t0) * 1000)})
        r.raise_for_status()
        data = r.json()
        if str(data.get("code")) != "00000":
            raise RuntimeError(f"Bitget 公开接口返回错误 code={data.get('code')} msg={data.get('msg')}")
        return data

    def candles(self, public_symbol: str, period: str, end_ms: int, limit: int = 200) -> list[list]:
        d = self._get("/api/v2/spot/market/history-candles",
                      {"symbol": public_symbol, "granularity": GRANULARITY[period], "endTime": str(end_ms), "limit": str(limit)})
        return d.get("data") or []

    def fetch_day(self, symbol: str, day: date, period: str = "1m") -> pd.DataFrame:
        meta = asset_meta(symbol)
        pub = meta.get("public_symbol") or f"{symbol}USDT"
        start, end = crypto_day_bounds_utc(day)
        rows: list[list] = []
        end_ms = int(end.timestamp() * 1000)
        start_ms = int(start.timestamp() * 1000)
        for _ in range(40):                       # 1440 根 / 200 = 8 页；留余量
            page = self.candles(pub, period, end_ms)
            if not page:
                break
            rows += page
            oldest = min(int(r[0]) for r in page)
            if oldest <= start_ms or len(page) < 200:
                break
            end_ms = oldest - 1
        if not rows:
            raise LookupError(f"{symbol} {day} 公开接口无 K 线")
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount", "quote_amount"][: len(rows[0])])
        df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"]), unit="ms", utc=True)
        df = df[(df["ts"] >= start) & (df["ts"] < end)].drop_duplicates("ts").sort_values("ts")
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["amount"] = pd.to_numeric(df.get("quote_amount", df.get("amount")), errors="coerce")
        df["tf"] = period
        bars = normalize_bars(df, symbol=symbol, venue=Venue.TOKEN, source=f"{SOURCE_TAG}:{pub}", chain=None, contract=None)
        bars.attrs["asset_class"] = "crypto"
        return bars

    def probe(self) -> ProbeResult:
        try:
            d = self._get("/api/v2/public/time", {})
            return ProbeResult(source=self.name, ok=True, has_credentials=True, detail=f"公开接口可达，服务器时间 {d.get('data')}",
                               history_span="history-candles 每页 200 根，向前分页", rate_limit="官方公开限频（约 20 次/秒）", cost_note="免费")
        except Exception as e:  # noqa: BLE001
            return ProbeResult(source=self.name, ok=False, has_credentials=True, blocked=True,
                               detail=f"api.bitget.com 不可达：{type(e).__name__}: {str(e)[:120]}（需在网络策略放行该域名）",
                               history_span="", rate_limit="", cost_note="")
