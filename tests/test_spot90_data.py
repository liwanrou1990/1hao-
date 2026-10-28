"""spot90 数据层测试（MockTransport，不访问网络）：V3 分页/去重/未收盘剔除、V2 回退、备用源解析、存储 upsert、拼接校验。"""
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

from spot90.data.bitget_v3 import BitgetSpotHistoryClient
from spot90.data.ingest import _splice
from spot90.data.quality import continuity_report
from spot90.data.schema import normalize_rows
from spot90.data.static_klines import StaticKlinesClient, window_starts
from spot90.data.store import BarStore

DAY = 86_400_000
T0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _daily_rows(start_ms: int, n: int, price: float = 100.0):
    return [[str(start_ms + i * DAY), str(price + i), str(price + i + 2), str(price + i - 2), str(price + i + 1), "10", "1000"] for i in range(n)]


def test_v3_pagination_dedupe_and_incomplete_removed():
    now = datetime.now(timezone.utc)
    last_open = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)   # 今天 0 点 = 未收盘
    all_rows = _daily_rows(last_open - 499 * DAY, 500)
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path == "/api/v2/public/time":
            return httpx.Response(200, json={"code": "00000", "data": "1"})
        assert req.url.path == "/api/v3/market/history-candles"
        assert req.url.params["category"] == "SPOT" and req.url.params["interval"] == "1day"
        end = int(req.url.params["endTime"])
        page = [r for r in all_rows if int(r[0]) <= end][-200:]
        if page and len(page) > 1:
            page.append(page[-1])                       # 人为制造重复
        return httpx.Response(200, json={"code": "00000", "data": page})

    c = BitgetSpotHistoryClient(transport=httpx.MockTransport(handler))
    df = c.fetch("BTCUSDT", "1d")
    assert c.log.api_version == "v3" and c.log.interval_used == "1day"
    assert len(df) == 499                               # 未收盘的今天被剔除，重复被去掉
    assert df["ts"].is_monotonic_increasing and not df["ts"].duplicated().any()
    assert df["source"].iloc[0] == "bitget_spot_v3"
    assert calls.count("/api/v3/market/history-candles") >= 3


def test_v3_bad_interval_falls_back_to_v2():
    rows = _daily_rows(T0, 50)

    def handler(req: httpx.Request):
        if req.url.path == "/api/v3/market/history-candles":
            return httpx.Response(400, json={"code": "40034", "msg": "Parameter interval error"})
        if req.url.path == "/api/v2/spot/market/history-candles":
            assert req.url.params["granularity"] == "1Dutc"
            end = int(req.url.params["endTime"])
            return httpx.Response(200, json={"code": "00000", "data": [r for r in rows if int(r[0]) <= end][-200:]})
        return httpx.Response(404)

    c = BitgetSpotHistoryClient(transport=httpx.MockTransport(handler))
    df = c.fetch("ETHUSDT", "1d")
    assert c.log.api_version == "v2" and len(df) == 50 and df["source"].iloc[0] == "bitget_spot_v2"


def test_wrong_period_returned_is_rejected():
    rows = [[str(T0 + i * 3_600_000), "1", "2", "0.5", "1.5", "1", "1"] for i in range(100)]   # 请求 1d 却返回 1h

    def handler(req: httpx.Request):
        return httpx.Response(200, json={"code": "00000", "data": rows})

    c = BitgetSpotHistoryClient(transport=httpx.MockTransport(handler))
    assert c.fetch("BTCUSDT", "1d").empty
    assert any("不符" in e for e in c.log.errors)


def test_normalize_rejects_invalid_ohlc_and_misaligned():
    rows = [[T0, 100, 101, 99, 100.5, 1, 1], [T0 + DAY, 100, 90, 99, 100.5, 1, 1], [T0 + 2 * DAY + 5000, 100, 101, 99, 100, 1, 1]]
    df = normalize_rows(rows, "BTCUSDT", "1d", "t")
    assert len(df) == 1


def test_static_klines_windows_and_parse(tmp_path):
    starts = window_starts("1d", today=datetime(2026, 10, 28).date())
    assert starts[0].isoformat() == "2016-01-01" and starts[-1].isoformat() == "2026-01-01" and len(starts) == 6
    q = window_starts("4h", today=datetime(2026, 10, 28).date())
    assert q[-1].isoformat() == "2026-10-01"

    def handler(req: httpx.Request):
        if "2016-01-01" in req.url.path:
            return httpx.Response(200, json=[])
        if "2018-01-01" in req.url.path:
            return httpx.Response(200, json=[[T0 - 10 * DAY + i * DAY, "1", "2", "0.5", "1.5", "3", 0, "4.5", 1, "1", "1", "0"] for i in range(5)])
        return httpx.Response(404)

    c = StaticKlinesClient(cache_root=tmp_path, transport=httpx.MockTransport(handler))
    df = c.fetch("BTCUSDT", "1d")
    assert len(df) == 5 and df["quote_volume"].iloc[0] == 4.5 and df["source"].iloc[0] == "binance_spot_via_static_klines"
    assert (tmp_path / "BTCUSDT" / "1d" / "2018-01-01.json").exists()      # 本地缓存


def test_store_upsert_and_continuity(tmp_path):
    st = BarStore(root=tmp_path)
    a = normalize_rows(_daily_rows(T0, 10), "BTCUSDT", "1d", "a")
    b = normalize_rows(_daily_rows(T0 + 8 * DAY, 10, price=500), "BTCUSDT", "1d", "b")
    assert st.upsert(a)["added"] == 10
    r = st.upsert(b)
    assert r["added"] == 8 and r["replaced"] == 2 and r["total"] == 18
    df = st.load("BTCUSDT", "1d")
    assert df.loc[df["ts"] == pd.Timestamp(T0 + 8 * DAY, unit="ms", tz="UTC"), "source"].iloc[0] == "b"
    rep = continuity_report(df.drop(index=[3]), "1d")
    assert rep["n_gap_bars"] == 1 and rep["duplicates"] == 0


def test_splice_rejects_inconsistent_sources():
    p = normalize_rows(_daily_rows(T0 + 100 * DAY, 50, price=200.0), "BTCUSDT", "1d", "p")
    good = normalize_rows(_daily_rows(T0, 150), "BTCUSDT", "1d", "b")
    merged, info = _splice(p, good, 0.8)
    assert info["mode"] == "spliced" and len(merged) == 150 and (merged["source"].iloc[:100] == "b").all()
    bad = normalize_rows(_daily_rows(T0, 150, price=1000), "BTCUSDT", "1d", "b")
    merged2, info2 = _splice(p, bad, 0.8)
    assert info2["mode"] == "primary_only_splice_rejected" and len(merged2) == 50
