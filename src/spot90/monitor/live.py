"""最新参考价（只读）。优先级：Bitget 现货 ticker（api.bitget.com，本环境被拒绝时自动跳过）→ DefiLlama 当前价（CoinGecko 聚合）→ CoinGecko simple price。
每个结果都带来源、价格时间戳、采集时间与延迟秒数；超过 freshness_max_s 标记为“延迟”，全部失败标记为“不可用”，绝不把旧日线收盘冒充实时价。"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

IDS = {"BTCUSDT": "coingecko:bitcoin", "ETHUSDT": "coingecko:ethereum"}
CG = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}


def _now() -> float:
    return time.time()


def fetch_live_prices(symbols: list[str], timeout: float = 10.0, freshness_max_s: int = 1800, transport: httpx.BaseTransport | None = None) -> dict:
    out: dict = {s: {"price": None, "source": None, "price_ts": None, "age_s": None, "status": "不可用", "errors": []} for s in symbols}
    client = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "spot90-monitor/0.1"})
    # 1) Bitget 现货 ticker
    for s in symbols:
        try:
            r = client.get("https://api.bitget.com/api/v2/spot/market/tickers", params={"symbol": s})
            d = r.json()
            if r.status_code == 200 and str(d.get("code")) == "00000" and d.get("data"):
                row = d["data"][0]
                ts = int(row.get("ts", _now() * 1000)) / 1000
                out[s].update({"price": float(row["lastPr"]), "source": "bitget_spot_ticker", "price_ts": ts})
        except Exception as e:  # noqa: BLE001
            out[s]["errors"].append(f"bitget: {type(e).__name__}")
    # 2) DefiLlama 当前价
    missing = [s for s in symbols if out[s]["price"] is None]
    if missing:
        try:
            r = client.get("https://coins.llama.fi/prices/current/" + ",".join(IDS[s] for s in missing))
            coins = r.json().get("coins", {}) if r.status_code == 200 else {}
            for s in missing:
                c = coins.get(IDS[s])
                if c and c.get("price"):
                    out[s].update({"price": float(c["price"]), "source": "defillama_coingecko_current", "price_ts": float(c.get("timestamp", _now()))})
        except Exception as e:  # noqa: BLE001
            for s in missing:
                out[s]["errors"].append(f"defillama: {type(e).__name__}")
    # 3) CoinGecko
    missing = [s for s in symbols if out[s]["price"] is None]
    if missing:
        try:
            r = client.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(CG[s] for s in missing), "vs_currencies": "usd", "include_last_updated_at": "true"})
            d = r.json() if r.status_code == 200 else {}
            for s in missing:
                c = d.get(CG[s])
                if c and c.get("usd"):
                    out[s].update({"price": float(c["usd"]), "source": "coingecko_simple_price", "price_ts": float(c.get("last_updated_at", _now()))})
        except Exception as e:  # noqa: BLE001
            for s in missing:
                out[s]["errors"].append(f"coingecko: {type(e).__name__}")
    now = _now()
    for s in symbols:
        o = out[s]
        o["collected_at"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
        if o["price"] is not None:
            o["age_s"] = int(now - (o["price_ts"] or now))
            o["price_ts"] = datetime.fromtimestamp(o["price_ts"], timezone.utc).isoformat() if o["price_ts"] else None
            o["status"] = "正常" if o["age_s"] <= freshness_max_s else f"延迟 {o['age_s']//60} 分钟"
    return out


def daily_bar_freshness(last_bar_ts, now: datetime | None = None) -> dict:
    """日线最后一根（UTC 开盘时间）相对今天的延迟：昨日 K 线已收盘 = 0 天延迟。"""
    now = now or datetime.now(timezone.utc)
    last_day = last_bar_ts.date() if hasattr(last_bar_ts, "date") else last_bar_ts
    expected = (now.date() - last_day).days - 1
    lag = max(expected, 0)
    return {"last_bar_date": str(last_day), "lag_days": int(lag), "status": "正常" if lag == 0 else f"日线延迟 {lag} 天"}
