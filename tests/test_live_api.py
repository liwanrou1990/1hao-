"""官方 RWA API 接入：多周期分页、tf 存储、资产核对、连接测试报告（MockTransport，不访问网络）。"""
from datetime import date

import httpx
import pandas as pd
import pytest

from bgw_quant.data.bitget_rwa import BitgetRwaClient
from bgw_quant.data.store import Store


def _ts(s):
    return int(pd.Timestamp(s, tz="America/New_York").timestamp())


def _make_handler(all_bars_by_contract: dict, fail_info: bool = False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content or b"{}")
        path = request.url.path
        calls.append((path.rsplit("/", 1)[-1], body))
        assert request.headers["x-api-key"] == "k" and request.headers["x-api-signature"]
        if path.endswith("stockList"):
            return httpx.Response(200, json={"status": 0, "data": {"list": [
                {"ticker": "NVDA", "name": "NVIDIA", "status": "online", "contracts": [
                    {"chain": "sol", "contract": "Cnvda", "symbol": "NVDAx", "data_source": "xstocks", "status": "online"},
                    {"chain": "bnb", "contract": "Cold", "symbol": "NVDAon", "data_source": "ondo", "status": "offline"}]},
                {"ticker": "SPCX", "name": "SpaceX", "status": "online", "contracts": [
                    {"chain": "sol", "contract": "Cspcx", "symbol": "SPCXon", "data_source": "ondo", "status": "online"}]}]}})
        if path.endswith("stockInfo"):
            if fail_info:
                return httpx.Response(403, text="forbidden")
            return httpx.Response(200, json={"status": 0, "data": {"ticker": body.get("ticker"), "latest_price": "1.0", "chain_assets": [
                {"chain": "sol", "contract": "C", "data_source": "xstocks", "market_status": "open", "tx_maximum_buy_usd": "50000"}]}})
        if path.endswith("kline"):
            rows = all_bars_by_contract.get(body["contract"], [])
            per = body.get("period", "5m")
            rows = [r for r in rows if r["period"] == per]
            end = body.get("end_time", 10**12)
            size = body.get("size", 500)
            sel = [r for r in rows if r["ts"] <= end]
            sel = sorted(sel, key=lambda r: r["ts"])[-size:]
            return httpx.Response(200, json={"status": 0, "data": {"list": [{k: v for k, v in r.items() if k != "period"} for r in sel]}})
        return httpx.Response(200, json={"status": 0, "data": {}})
    return handler, calls


def _bars(contract_rows, period, start, n, step_min, with_volume=True):
    out = []
    for i in range(n):
        t = _ts(start) + i * step_min * 60
        r = {"ts": t, "open": 100 + i * 0.01, "high": 100.1 + i * 0.01, "low": 99.9 + i * 0.01, "close": 100.05 + i * 0.01, "period": period}
        if with_volume:
            r.update({"volume": 10, "amount": 1000, "txn": 2})
        contract_rows.append(r)


def test_kline_history_pages_backwards_and_dedups():
    rows = {"Cnvda": [], "Cspcx": []}
    _bars(rows["Cnvda"], "1m", "2026-10-16 09:30", 390, 1)
    _bars(rows["Cnvda"], "5m", "2026-10-16 09:30", 78, 5)
    _bars(rows["Cspcx"], "1m", "2026-10-16 09:30", 200, 1, with_volume=False)   # ondo：无成交量、缺半天
    handler, calls = _make_handler(rows)
    c = BitgetRwaClient(api_key="k", api_secret="s", transport=httpx.MockTransport(handler))
    c.MAX_PAGE_SIZE = 100
    b1 = c.fetch_bars("NVDA", date(2026, 10, 16), "1m")
    assert len(b1) == 390 and b1["tf"].iloc[0] == "1m" and b1["ts"].is_monotonic_increasing and b1["ts"].is_unique
    assert sum(1 for k, _ in calls if k == "kline") >= 4            # 分页 ≥ 4 次
    assert b1["chain"].iloc[0] == "sol" and b1["contract"].iloc[0] == "Cnvda" and b1["source"].iloc[0] == "bitget_rwa:xstocks"
    b5 = c.fetch_bars("NVDA", date(2026, 10, 16), "5m")
    assert len(b5) == 78 and b5["tf"].iloc[0] == "5m"
    assert b1["volume"].notna().all() and b1["amount"].notna().all()
    sp = c.fetch_bars("SPCX", date(2026, 10, 16), "1m")
    assert len(sp) == 200 and sp["volume"].isna().all() and sp["source"].iloc[0] == "bitget_rwa:ondo"
    with pytest.raises(ValueError):
        c.fetch_bars("NVDA", date(2026, 10, 16), "3m")


def test_store_keeps_timeframes_separate():
    rows = {"Cnvda": []}
    _bars(rows["Cnvda"], "1m", "2026-10-16 09:30", 30, 1)
    _bars(rows["Cnvda"], "5m", "2026-10-16 09:30", 6, 5)
    handler, _ = _make_handler(rows)
    c = BitgetRwaClient(api_key="k", api_secret="s", transport=httpx.MockTransport(handler))
    st = Store(":memory:")
    st.upsert_bars(c.fetch_bars("NVDA", date(2026, 10, 16), "1m"))
    st.upsert_bars(c.fetch_bars("NVDA", date(2026, 10, 16), "5m"))
    assert len(st.load_bars("NVDA", venue="token", tf="1m")) == 30
    assert len(st.load_bars("NVDA", venue="token", tf="5m")) == 6
    assert st.available_days("NVDA", "token", "5m") == [date(2026, 10, 16)]
    summ = st.sources_summary()
    assert set(summ["tf"]) == {"1m", "5m"}


def test_verify_assets_and_live_test_report():
    rows = {"Cnvda": []}
    _bars(rows["Cnvda"], "1m", "2026-10-16 09:30", 390, 1)
    handler, _ = _make_handler(rows)
    c = BitgetRwaClient(api_key="k", api_secret="s", transport=httpx.MockTransport(handler))
    assets = c.verify_assets(["NVDA", "SPCX", "TSLA"])
    by = {a["ticker"]: a for a in assets}
    assert by["NVDA"]["listed"] and by["NVDA"]["contracts"][0]["data_source"] == "xstocks" and by["NVDA"]["contracts"][0]["issuer_known"]
    assert by["SPCX"]["contracts"][0]["symbol"] == "SPCXon"
    assert not by["TSLA"]["listed"] and "不存在" in by["TSLA"]["error"]
    rep = c.live_test(["NVDA", "SPCX"], date(2026, 10, 16), ("1m", "5m"))
    k = {(x["ticker"], x["period"]): x for x in rep["klines"]}
    assert k[("NVDA", "1m")]["ok"] and k[("NVDA", "1m")]["bars"] == 390 and k[("NVDA", "1m")]["missing_note"] is None
    assert k[("NVDA", "5m")]["ok"] is False and k[("NVDA", "5m")]["bars"] == 0        # mock 无 5m → 无K线即为失败
    assert k[("SPCX", "1m")]["ok"] is False
    assert rep["errors"] and rep["steps"][0]["step"] == "network"


def test_live_test_without_secret_reports_blocker():
    c = BitgetRwaClient(api_key="k", api_secret="", transport=httpx.MockTransport(lambda r: httpx.Response(403, text="x")))
    rep = c.live_test(["NVDA"], date(2026, 10, 16))
    assert rep["has_key"] and not rep["has_secret"]
    assert any("BGW_API_SECRET" in e for e in rep["errors"]) and rep["klines"] == []
