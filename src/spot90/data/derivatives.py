"""衍生品数据（可选模块）：Bitget 公开合约接口，无需密钥。本环境 api.bitget.com 被拒绝 → 状态 blocked；在可联网机器上运行 `spot90 derivatives` 采集。

* 资金费率历史：GET /api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES&pageSize=100&pageNo=1（每 8 小时一条，向后翻页）
* 当前未平仓量：GET /api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=USDT-FUTURES（只有当前值，历史需每日采集累积 → 本模块把每次采集追加到本地表，并记录采集时间）
区分：资金费率/未平仓量是衍生品市场数据，不是链上转账，也不是现货成交量。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from ..config import cfg, data_dir

BASE = "https://api.bitget.com"


class BitgetDerivativesClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        self.client = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "spot90/0.2"})
        self.root = data_dir() / "derivatives"; self.root.mkdir(parents=True, exist_ok=True)

    def _get(self, path: str, params: dict) -> dict:
        r = self.client.get(BASE + path, params=params)
        r.raise_for_status()
        d = r.json()
        if str(d.get("code")) != "00000":
            raise ValueError(f"code={d.get('code')} msg={d.get('msg')}")
        return d

    def funding_history(self, symbol: str, max_pages: int = 200) -> pd.DataFrame:
        rows: list[dict] = []
        for page in range(1, max_pages + 1):
            d = self._get("/api/v2/mix/market/history-fund-rate", {"symbol": symbol, "productType": "USDT-FUTURES", "pageSize": "100", "pageNo": str(page)})
            data = d.get("data") or []
            if not data:
                break
            rows += data
            if len(data) < 100:
                break
            time.sleep(0.1)
        if not rows:
            return pd.DataFrame(columns=["symbol", "ts", "funding_rate", "source", "collected_at"])
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms", utc=True)
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        df["symbol"], df["source"], df["collected_at"] = symbol, "bitget_mix_public", pd.Timestamp.now(tz="UTC")
        return df[["symbol", "ts", "funding_rate", "source", "collected_at"]].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    def open_interest_now(self, symbol: str) -> dict:
        d = self._get("/api/v2/mix/market/open-interest", {"symbol": symbol, "productType": "USDT-FUTURES"})
        data = d.get("data") or {}
        lst = data.get("openInterestList") or []
        oi = float(lst[0]["size"]) if lst else None
        return {"symbol": symbol, "ts": pd.Timestamp.now(tz="UTC"), "open_interest": oi, "source": "bitget_mix_public"}

    def collect(self, symbols: list[str] | None = None) -> dict:
        symbols = symbols or cfg()["symbols"]
        status = {"run_ts": datetime.now(timezone.utc).isoformat(), "symbols": {}}
        try:
            self._get("/api/v2/public/time", {})
        except (httpx.HTTPError, ValueError) as e:
            status["blocked"] = f"api.bitget.com 不可达：{type(e).__name__}"
            (self.root / "derivatives_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
            return status
        for s in symbols:
            fr = self.funding_history(s)
            p = self.root / f"funding_{s}.parquet"
            if p.exists():
                old = pd.read_parquet(p); fr = pd.concat([old, fr]).drop_duplicates("ts", keep="last").sort_values("ts")
            fr.to_parquet(p, index=False)
            oi = self.open_interest_now(s)
            q = self.root / f"open_interest_{s}.parquet"
            oid = pd.DataFrame([oi])
            if q.exists():
                oid = pd.concat([pd.read_parquet(q), oid])
            oid.to_parquet(q, index=False)
            status["symbols"][s] = {"funding_rows": int(len(fr)), "funding_first": str(fr["ts"].min()) if len(fr) else None, "funding_last": str(fr["ts"].max()) if len(fr) else None,
                                    "open_interest_snapshots": int(len(oid))}
        (self.root / "derivatives_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
        return status
