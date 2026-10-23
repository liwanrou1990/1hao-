"""Bitget 公开现货接口客户端（MockTransport，不访问网络）：K 线分页、逐笔成交→token_trades 列、深度快照；严格模式拒绝 SIMULATED。"""
import json
from datetime import date

import httpx
import pytest

from bgw_quant.data.crypto import BitgetSpotPublicClient, crypto_day_bounds_utc

DAY = date(2026, 10, 22)


def _client(handler):
    c = BitgetSpotPublicClient()
    c.client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bitget.com")
    return c


def test_fetch_day_pages_backwards_and_filters_day():
    start, end = crypto_day_bounds_utc(DAY)
    s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    def handler(req: httpx.Request):
        assert req.url.path == "/api/v2/spot/market/history-candles"
        end_t = int(req.url.params["endTime"])
        rows = [[str(t), "100", "101", "99", "100.5", "10", "1000", "1000"] for t in range(end_t - 200 * 60_000 + 60_000, end_t + 1, 60_000)]
        return httpx.Response(200, json={"code": "00000", "data": rows})

    bars = _client(handler).fetch_day("BTC", DAY, "1m")
    assert len(bars) == 1440 and bars["source"].iloc[0].startswith("bitget_spot_public")
    assert bars["ts"].min().timestamp() * 1000 >= s_ms and bars["ts"].max().timestamp() * 1000 < e_ms
    assert int(bars["quality"].iloc[0]) & 128 == 0                      # 非 SIMULATED


def test_fetch_trades_matches_token_trades_schema():
    start, _ = crypto_day_bounds_utc(DAY)
    base = int(start.timestamp() * 1000) + 3_600_000

    def handler(req: httpx.Request):
        assert req.url.path == "/api/v2/spot/market/fills-history"
        rows = [{"tradeId": str(i), "price": "100", "size": "0.5", "side": "buy" if i % 2 else "sell", "ts": str(base + i * 1000)} for i in range(50)]
        return httpx.Response(200, json={"code": "00000", "data": rows})

    tx = _client(handler).fetch_trades("BTC", DAY)
    assert list(tx.columns) == ["symbol", "chain", "contract", "ts", "ts_ny", "side", "price", "amount", "value", "tx_hash", "source", "ingested_at"]
    assert len(tx) == 50 and tx["value"].iloc[0] == 50.0 and tx["tx_hash"].nunique() == 50


def test_orderbook_depth_summary():
    def handler(req: httpx.Request):
        assert req.url.path == "/api/v2/spot/market/orderbook"
        bids = [[str(100 - i * 0.01), "10"] for i in range(50)]
        asks = [[str(100.02 + i * 0.01), "10"] for i in range(50)]
        return httpx.Response(200, json={"code": "00000", "data": {"bids": bids, "asks": asks, "ts": "1"}})

    ob = _client(handler).orderbook("BTC")
    assert ob["mid"] == pytest.approx(100.01) and 1.9 < ob["spread_bps"] < 2.1
    assert ob["depth_usd_10bp"] > 0 and ob["depth_usd_100bp"] >= ob["depth_usd_10bp"]


def test_require_real_rejects_simulated(tmp_path, monkeypatch):
    monkeypatch.setenv("BGWQ_DB_PATH", str(tmp_path / "x.duckdb"))
    from bgw_quant.config import get_settings
    get_settings.cache_clear()
    from bgw_quant.pipeline import Pipeline
    pl = Pipeline(probe=False)
    pl.ingest_crypto(["BTC"], DAY, DAY)
    prov = pl.data_provenance(DAY, ["BTC", "ETH"])
    assert prov["BTC"]["simulated"] is True and prov["ETH"]["available"] is False
    with pytest.raises(RuntimeError, match="SIMULATED"):
        pl.require_real(DAY, ["BTC"])
    get_settings.cache_clear()
