"""Bitget Wallet 官方 RWA 行情 API 客户端（ToB OpenAPI）。

接口规格来源：官方 SDK 源码 bitgetwallet/tob-api-sdk-ts 与 bitgetwallet/tob-api-sdk（Go）。
  Base URL : https://bopenapi.bgwapi.io
  POST /bgw-pro/market/v3/rwa/stockList            body {}
  POST /bgw-pro/market/v3/rwa/stockInfo            body {ticker} 或 {chain, contract}[, has_offline]
  POST /bgw-pro/market/v3/rwa/kline                body {chain, contract, period?, size?, end_time?}
  POST /bgw-pro/market/v3/rwa/transactionList      body {chain, contract, side?, page?, size?}
  POST /bgw-pro/market/v3/rwa/checkContractIsRwa   body {chain, contract}
签名：headers x-api-key / x-api-timestamp(毫秒) / x-api-signature
  payload = JSON(排序键){ "apiPath": path, "body": 原始body字串, ...query参数, "x-api-key": key, "x-api-timestamp": ts }
  signature = base64(HMAC-SHA256(secret, payload))
注意：
  * data_source 官方枚举仅 ondo / xstocks；"Reality" 不在枚举中。
  * ondo K线不含 volume/amount/txn（为发行商报价），链上K线含成交量。两者不得与原股价格混同。
  * 本模块只读行情；不涉及任何下单、签名交易或私钥。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pandas as pd

from ..calendar_utils import NY
from ..config import get_settings
from .base import ProbeResult
from .schema import DataSourceTag, QualityFlag, Venue, normalize_bars

RWA_PREFIX = "/bgw-pro/market/v3/rwa"
KLINE_PERIODS = ["1s", "5s", "15s", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"]
KNOWN_DATA_SOURCES = {"ondo", "xstocks"}


def build_sign_payload(api_path: str, body: str, query: dict[str, str], api_key: str, ts_ms: str) -> str:
    fields: dict[str, str] = {"apiPath": api_path, "body": body, **query, "x-api-key": api_key, "x-api-timestamp": ts_ms}
    # 与 TS SDK 的 JSON.stringify(sorted) 一致：键排序、无空白、不转义非 ASCII
    return json.dumps({k: fields[k] for k in sorted(fields)}, separators=(",", ":"), ensure_ascii=False)


def sign_payload(payload: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")


def signed_headers(api_path: str, body: str, api_key: str, api_secret: str,
                   query: dict[str, str] | None = None, ts_ms: str | None = None) -> dict[str, str]:
    ts_ms = ts_ms or str(int(time.time() * 1000))
    payload = build_sign_payload(api_path, body, query or {}, api_key, ts_ms)
    return {
        "x-api-key": api_key,
        "x-api-timestamp": ts_ms,
        "x-api-signature": sign_payload(payload, api_secret),
        "Content-Type": "application/json",
    }


@dataclass
class RwaContract:
    ticker: str
    chain: str
    contract: str
    symbol: str | None
    data_source: str
    status: str

    @property
    def issuer_known(self) -> bool:
        return self.data_source in KNOWN_DATA_SOURCES


class BitgetRwaClient:
    name = "bitget_rwa"
    is_simulated = False

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 base_url: str | None = None, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.bgw_api_key
        self.api_secret = api_secret if api_secret is not None else s.bgw_api_secret
        self.base_url = (base_url or s.bgw_base_url).rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self.call_log: list[dict[str, Any]] = []   # 记录每次调用，用于成本/频率评估

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # ---------- 低层 ----------
    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"{RWA_PREFIX}/{endpoint}"
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = signed_headers(path, raw, self.api_key, self.api_secret)
        t0 = time.perf_counter()
        resp = self._client.post(path, content=raw.encode("utf-8"), headers=headers)
        elapsed = time.perf_counter() - t0
        self.call_log.append({"endpoint": endpoint, "status_code": resp.status_code, "elapsed_s": round(elapsed, 3),
                              "ts": datetime.now(timezone.utc).isoformat()})
        if resp.status_code in (401, 403):
            raise PermissionError(f"Bitget RWA {endpoint}: HTTP {resp.status_code} 权限不足或签名错误: {resp.text[:200]}")
        if resp.status_code == 429:
            raise RuntimeError(f"Bitget RWA {endpoint}: HTTP 429 触发频率限制: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        if data.get("status", 0) != 0:
            raise RuntimeError(f"Bitget RWA {endpoint} 业务失败 status={data.get('status')}: {data.get('data')}")
        return data

    # ---------- 官方端点 ----------
    def stock_list(self) -> list[dict[str, Any]]:
        return self._post("stockList", {}).get("data", {}).get("list", []) or []

    def stock_info(self, ticker: str | None = None, chain: str | None = None, contract: str | None = None,
                   has_offline: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"has_offline": has_offline}
        if ticker:
            body["ticker"] = ticker
        else:
            body.update({"chain": chain, "contract": contract})
        return self._post("stockInfo", body).get("data", {}) or {}

    def kline(self, chain: str, contract: str, period: str = "1m", size: int | None = None,
              end_time: int | None = None) -> list[dict[str, Any]]:
        if period not in KLINE_PERIODS:
            raise ValueError(f"period 必须是 {KLINE_PERIODS}")
        body: dict[str, Any] = {"chain": chain, "contract": contract, "period": period}
        if size:
            body["size"] = size
        if end_time:
            body["end_time"] = end_time
        return self._post("kline", body).get("data", {}).get("list", []) or []

    def transaction_list(self, chain: str, contract: str, side: str | None = None, page: int = 1,
                         size: int = 100) -> dict[str, Any]:
        body: dict[str, Any] = {"chain": chain, "contract": contract, "page": page, "size": min(size, 100)}
        if side:
            body["side"] = side
        return self._post("transactionList", body).get("data", {}) or {}

    def check_contract_is_rwa(self, chain: str, contract: str) -> dict[str, Any]:
        return self._post("checkContractIsRwa", {"chain": chain, "contract": contract}).get("data", {}) or {}

    # ---------- 高层 ----------
    def contracts_for(self, ticker: str) -> list[RwaContract]:
        out = []
        for item in self.stock_list():
            if (item.get("ticker") or "").upper() != ticker.upper():
                continue
            for c in item.get("contracts", []) or []:
                out.append(RwaContract(ticker=item["ticker"], chain=c.get("chain", ""), contract=c.get("contract", ""),
                                       symbol=c.get("symbol"), data_source=c.get("data_source", "unknown"),
                                       status=c.get("status", "unknown")))
        return out

    def kline_to_bars(self, rows: list[dict[str, Any]], *, ticker: str, chain: str, contract: str,
                      data_source: str) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        ts = pd.to_numeric(df["ts"])
        # 秒或毫秒自动识别
        unit = "ms" if ts.max() > 1e12 else "s"
        df["ts"] = pd.to_datetime(ts, unit=unit, utc=True)
        src = {"ondo": DataSourceTag.BITGET_RWA_ONDO, "xstocks": DataSourceTag.BITGET_RWA_XSTOCKS}.get(
            data_source, DataSourceTag.BITGET_RWA_UNKNOWN)
        q = 0
        if "volume" not in df.columns:
            q |= QualityFlag.NO_VOLUME_FIELD
        bars = normalize_bars(df, symbol=ticker, venue=Venue.TOKEN, source=str(src), chain=chain, contract=contract,
                              quality_extra=int(q))
        # 代币 24/7 报价：常规时段外标记 OFF_HOURS
        ny = bars["ts"].dt.tz_convert(NY)
        off = (ny.dt.hour * 60 + ny.dt.minute < 9 * 60 + 30) | (ny.dt.hour * 60 + ny.dt.minute >= 16 * 60) | (ny.dt.weekday >= 5)
        bars.loc[off, "quality"] = bars.loc[off, "quality"] | int(QualityFlag.OFF_HOURS)
        # 最后一根可能未完成：以当前时间判断
        now = pd.Timestamp.now(tz="UTC")
        last_open = bars["ts"].iloc[-1]
        if now < last_open + pd.Timedelta(minutes=1):
            bars.loc[bars.index[-1], "is_complete"] = False
            bars.loc[bars.index[-1], "quality"] |= int(QualityFlag.INCOMPLETE)
        return bars

    def fetch_minute_bars(self, symbol: str, day: date, chain: str | None = None, contract: str | None = None) -> pd.DataFrame:
        if not self.has_credentials:
            raise PermissionError("缺少 BGW_API_KEY/BGW_API_SECRET，无法访问 Bitget RWA 行情")
        cands = self.contracts_for(symbol)
        if chain and contract:
            cands = [c for c in cands if c.chain == chain and c.contract == contract]
        if not cands:
            raise LookupError(f"{symbol} 在官方 stockList 中无可用合约")
        c = cands[0]
        end_dt = datetime.combine(day, datetime.max.time()).replace(tzinfo=NY)
        rows = self.kline(c.chain, c.contract, period="1m", size=1440, end_time=int(end_dt.timestamp()))
        bars = self.kline_to_bars(rows, ticker=symbol, chain=c.chain, contract=c.contract, data_source=c.data_source)
        if bars.empty:
            return bars
        ny_dates = bars["ts"].dt.tz_convert(NY).dt.date
        return bars[ny_dates == day].reset_index(drop=True)

    # ---------- 探测（权限、历史跨度、频率、成本） ----------
    def probe(self) -> ProbeResult:
        res = ProbeResult(source=self.name, ok=False, detail="", has_credentials=self.has_credentials,
                          cost_note="官方未公开 RWA 行情计费；需向 bgw_devsupport@bitget.com 确认配额与费用")
        if not self.has_credentials:
            res.detail = "未配置 BGW_API_KEY/BGW_API_SECRET：无法验证权限、历史跨度与频率限制"
            res.blocked = True
            return res
        try:
            lst = self.stock_list()
            res.endpoints["stockList"] = f"ok ({len(lst)} tickers)"
        except Exception as e:  # noqa: BLE001
            res.endpoints["stockList"] = f"失败: {e}"
            res.detail = str(e)
            res.blocked = True
            return res
        try:
            nvda = [x for x in lst if (x.get("ticker") or "").upper() == "NVDA"]
            if nvda and nvda[0].get("contracts"):
                c = nvda[0]["contracts"][0]
                info = self.stock_info(ticker="NVDA")
                res.endpoints["stockInfo"] = f"ok ({len(info.get('chain_assets', []) or [])} chain assets)"
                rows = self.kline(c["chain"], c["contract"], period="1m", size=1440)
                if rows:
                    ts = pd.to_numeric(pd.DataFrame(rows)["ts"])
                    unit = "ms" if ts.max() > 1e12 else "s"
                    t0, t1 = pd.to_datetime(ts.min(), unit=unit, utc=True), pd.to_datetime(ts.max(), unit=unit, utc=True)
                    res.history_span = f"1m kline 单次返回 {len(rows)} 根: {t0} ~ {t1}"
                res.endpoints["kline"] = f"ok ({len(rows)} rows)"
                tx = self.transaction_list(c["chain"], c["contract"], size=20)
                res.endpoints["transactionList"] = f"ok ({len(tx.get('list', []) or [])} rows)"
            else:
                res.endpoints["stockInfo"] = "跳过：stockList 中无 NVDA"
        except Exception as e:  # noqa: BLE001
            res.endpoints["kline/transactionList"] = f"失败: {e}"
        lat = [c["elapsed_s"] for c in self.call_log]
        res.rate_limit = f"{len(self.call_log)} 次调用，平均延迟 {sum(lat)/max(len(lat),1):.3f}s；官方限频未公开，429 时需退避"
        res.ok = all(v.startswith("ok") or v.startswith("跳过") for v in res.endpoints.values())
        res.detail = "探测完成" if res.ok else "部分端点失败"
        return res
