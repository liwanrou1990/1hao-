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
import os
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

    # ---------- 只读可执行报价（订单模式价格查询，不创建/不提交订单） ----------
    SWAP_PREFIX = "/bgw-pro/swapx/order"

    def _post_path(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = signed_headers(path, raw, self.api_key, self.api_secret)
        t0 = time.perf_counter()
        resp = self._client.post(path, content=raw.encode("utf-8"), headers=headers)
        self.call_log.append({"endpoint": path.rsplit("/", 1)[-1], "status_code": resp.status_code,
                              "elapsed_s": round(time.perf_counter() - t0, 3), "ts": datetime.now(timezone.utc).isoformat()})
        if resp.status_code in (401, 403):
            raise PermissionError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 429:
            raise RuntimeError(f"{path}: HTTP 429 限频: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        if data.get("status", 0) != 0:
            raise RuntimeError(f"{path} 业务失败 status={data.get('status')}: {data.get('data')}")
        return data

    def executable_quote(self, from_chain: str, from_contract: str, from_amount: str, to_chain: str, to_contract: str,
                         from_address: str) -> dict[str, Any]:
        """getSwapPrice：按给定金额查询当下可执行的 swap 价格（只读）。绝不调用 makeSwapOrder / submitSwapOrder。"""
        body = {"fromChain": from_chain, "fromContract": from_contract, "fromAmount": str(from_amount), "toChain": to_chain,
                "toContract": to_contract, "fromAddress": from_address}
        return self._post_path(f"{self.SWAP_PREFIX}/getSwapPrice", body).get("data", {}) or {}

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

    PERIOD_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
    MAX_PAGE_SIZE = 500          # 官方未公开单次上限；探测时若返回少于请求数即视为到达上限或历史尽头

    def kline_history(self, chain: str, contract: str, period: str, start: datetime, end: datetime,
                      page_size: int | None = None, max_pages: int = 200) -> list[dict[str, Any]]:
        """按需分页：从 end 往前翻页（end_time 秒），直到覆盖 start 或没有更早数据。去重并按时间升序。"""
        if period not in self.PERIOD_MINUTES:
            raise ValueError(f"仅支持 {list(self.PERIOD_MINUTES)}")
        size = page_size or self.MAX_PAGE_SIZE
        end_ts = int(end.timestamp())
        start_ts = int(start.timestamp())
        seen: dict[int, dict[str, Any]] = {}
        for _ in range(max_pages):
            rows = self.kline(chain, contract, period=period, size=size, end_time=end_ts)
            if not rows:
                break
            ts_vals = []
            for r in rows:
                t = int(r["ts"])
                t = t // 1000 if t > 1e12 else t
                seen[t] = r
                ts_vals.append(t)
            oldest = min(ts_vals)
            if oldest <= start_ts or len(rows) < size:
                break
            end_ts = oldest - 1
        out = [dict(r, ts=t) for t, r in sorted(seen.items()) if start_ts <= t <= int(end.timestamp())]
        return out

    def fetch_bars(self, symbol: str, day: date, period: str = "1m", chain: str | None = None, contract: str | None = None) -> pd.DataFrame:
        """获取某交易日（美东）的代币K线，period ∈ 1m/5m/15m…，自动分页；返回标准列并带 tf。"""
        if not self.has_credentials:
            raise PermissionError("缺少 BGW_API_KEY/BGW_API_SECRET，无法访问 Bitget RWA 行情")
        cands = self.contracts_for(symbol)
        if chain and contract:
            cands = [c for c in cands if c.chain == chain and c.contract == contract]
        cands = [c for c in cands if c.status == "online"] or cands
        if not cands:
            raise LookupError(f"{symbol} 在官方 stockList 中无可用合约")
        c = cands[0]
        start = datetime.combine(day, datetime.min.time()).replace(tzinfo=NY)
        end = datetime.combine(day, datetime.max.time()).replace(tzinfo=NY)
        rows = self.kline_history(c.chain, c.contract, period, start, end)
        bars = self.kline_to_bars(rows, ticker=symbol, chain=c.chain, contract=c.contract, data_source=c.data_source)
        if bars.empty:
            return bars
        bars["tf"] = period
        return bars.reset_index(drop=True)

    def verify_assets(self, tickers: list[str]) -> list[dict[str, Any]]:
        """用 stockList + stockInfo 核对代币、链、合约、发行商与市场状态。"""
        out = []
        lst = {(x.get("ticker") or "").upper(): x for x in self.stock_list()}
        for t in tickers:
            item = lst.get(t.upper())
            rec: dict[str, Any] = {"ticker": t, "listed": item is not None, "contracts": [], "stock_info": None, "error": None}
            if item is None:
                rec["error"] = "stockList 中不存在该 ticker"
                out.append(rec)
                continue
            rec["name"], rec["status"] = item.get("name"), item.get("status")
            for c in item.get("contracts", []) or []:
                rec["contracts"].append({"chain": c.get("chain"), "contract": c.get("contract"), "symbol": c.get("symbol"),
                                         "data_source": c.get("data_source"), "status": c.get("status"),
                                         "issuer_known": c.get("data_source") in KNOWN_DATA_SOURCES})
            try:
                info = self.stock_info(ticker=t)
                rec["stock_info"] = {"latest_price": info.get("latest_price"), "price_24h_change_ratio": info.get("price_24h_change_ratio"),
                                     "chain_assets": [{k: a.get(k) for k in ("chain", "contract", "symbol", "data_source", "market_status",
                                                                              "market_status_code", "tx_maximum_buy_usd", "tx_minimum_buy_usd",
                                                                              "tx_maximum_sell_usd", "latest_price")}
                                                      for a in (info.get("chain_assets") or [])]}
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"stockInfo 失败: {e}"
            out.append(rec)
        return out

    def live_test(self, tickers: list[str], day: date, periods: tuple[str, ...] = ("1m", "5m", "15m")) -> dict[str, Any]:
        """连接测试：权限 → stockList 核对 → 各周期K线获取；记录每一步的错误与缺失。只读，不涉及任何交易接口。"""
        from ..calendar_utils import regular_minute_index
        rep: dict[str, Any] = {"base_url": self.base_url, "has_key": bool(self.api_key), "has_secret": bool(self.api_secret),
                               "day": day.isoformat(), "steps": [], "assets": [], "klines": [], "errors": []}
        # 0) 网络可达性（未签名请求，只看能否到达主机；预期 400/403 也算可达）
        try:
            resp = self._client.post(f"{RWA_PREFIX}/stockList", content=b"{}", headers={"Content-Type": "application/json"})
            rep["steps"].append({"step": "network", "ok": True, "http_status": resp.status_code, "body_head": resp.text[:120]})
        except Exception as e:  # noqa: BLE001
            rep["steps"].append({"step": "network", "ok": False, "error": f"{type(e).__name__}: {e}"})
            rep["errors"].append(f"网络不可达 {self.base_url}: {type(e).__name__}: {e}")
        if not self.has_credentials:
            missing = [n for n, v in (("BGW_API_KEY", self.api_key), ("BGW_API_SECRET", self.api_secret)) if not v]
            rep["errors"].append(f"缺少 {', '.join(missing)}：官方签名 x-api-signature = base64(HMAC-SHA256(secret, payload))，没有 Secret 无法发起已签名请求")
            return rep
        try:
            assets = self.verify_assets(tickers)
            rep["assets"] = assets
            rep["steps"].append({"step": "stockList/stockInfo", "ok": True})
        except Exception as e:  # noqa: BLE001
            rep["steps"].append({"step": "stockList/stockInfo", "ok": False, "error": str(e)})
            rep["errors"].append(f"stockList: {e}")
            return rep
        expected_1m = len(regular_minute_index(day)) if True else None
        for t in tickers:
            for per in periods:
                try:
                    bars = self.fetch_bars(t, day, per)
                    n = len(bars)
                    exp = expected_1m // self.PERIOD_MINUTES[per] if expected_1m else None
                    rep["klines"].append({"ticker": t, "period": per, "bars": n, "expected_regular_session": exp,
                                          "first_ts": bars["ts_ny"].iloc[0] if n else None, "last_ts": bars["ts_ny"].iloc[-1] if n else None,
                                          "has_volume": bool(n and bars["volume"].notna().any()),
                                          "source": bars["source"].iloc[0] if n else None, "ok": n > 0,
                                          "missing_note": None if (exp is None or n >= exp) else f"常规时段应有约 {exp} 根，实际 {n} 根"})
                except Exception as e:  # noqa: BLE001
                    rep["klines"].append({"ticker": t, "period": per, "ok": False, "error": str(e)})
                    rep["errors"].append(f"{t} {per}: {e}")
        quote_addr = os.environ.get("BGW_QUOTE_ADDRESS", "")
        quote_usdc = os.environ.get("BGW_QUOTE_STABLE_CONTRACT", "")
        if quote_addr and quote_usdc:
            for a in rep["assets"]:
                c = next((x for x in a.get("contracts", []) if x.get("status") == "online"), None)
                if not c:
                    continue
                try:
                    q = self.executable_quote(c["chain"], quote_usdc, "1000", c["chain"], c["contract"], quote_addr)
                    rep.setdefault("quotes", []).append({"ticker": a["ticker"], "ok": True, "quote": q})
                except Exception as e:  # noqa: BLE001
                    rep.setdefault("quotes", []).append({"ticker": a["ticker"], "ok": False, "error": str(e)[:200]})
        else:
            rep["quotes_note"] = "未配置 BGW_QUOTE_ADDRESS / BGW_QUOTE_STABLE_CONTRACT：跳过只读可执行报价（getSwapPrice）"
        rep["calls"] = self.call_log
        return rep

    def fetch_minute_bars(self, symbol: str, day: date, chain: str | None = None, contract: str | None = None) -> pd.DataFrame:
        if not self.has_credentials:
            raise PermissionError("缺少 BGW_API_KEY/BGW_API_SECRET，无法访问 Bitget RWA 行情")
        cands = self.contracts_for(symbol)
        if chain and contract:
            cands = [c for c in cands if c.chain == chain and c.contract == contract]
        if not cands:
            raise LookupError(f"{symbol} 在官方 stockList 中无可用合约")
        return self.fetch_bars(symbol, day, "1m", chain, contract)

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
