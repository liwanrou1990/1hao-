"""阶段一：数据与标的。"""
from datetime import date

import httpx
import pandas as pd
import pytest

from bgw_quant.calendar_utils import is_trading_day, previous_trading_day, regular_minute_index
from bgw_quant.config import load_yaml, research_symbols
from bgw_quant.data.bitget_rwa import BitgetRwaClient, build_sign_payload, sign_payload
from bgw_quant.data.massive import MassiveClient
from bgw_quant.data.quality import coverage_report, describe_flags, research_ready
from bgw_quant.data.registry import build_data_plan
from bgw_quant.data.schema import QualityFlag
from bgw_quant.data.simulated import SimulatedSource, regime_for_day
from bgw_quant.data.store import Store


def test_signature_matches_official_sdk_vector():
    body = '{"chain":"sol","contract":"Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg","period":"1m"}'
    payload = build_sign_payload("/bgw-pro/market/v3/rwa/kline", body, {}, "k123", "1760000000000")
    assert payload == ('{"apiPath":"/bgw-pro/market/v3/rwa/kline","body":"{\\"chain\\":\\"sol\\",\\"contract\\":'
                       '\\"Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg\\",\\"period\\":\\"1m\\"}",'
                       '"x-api-key":"k123","x-api-timestamp":"1760000000000"}')
    # 由 Node.js 按官方 TS SDK auth.ts 算法独立计算的参考值
    assert sign_payload(payload, "s3cret") == "ZEiK4S7yBqn2j17fPc0pItFy3Q3baKMr1i6+W/St610="


def test_bitget_client_without_keys_is_blocked_not_faked():
    c = BitgetRwaClient(api_key="", api_secret="")
    p = c.probe()
    assert p.blocked and not p.ok and not p.has_credentials
    with pytest.raises(PermissionError):
        c.fetch_minute_bars("NVDA", date(2026, 10, 16))


def test_bitget_client_mock_transport_parses_kline_and_marks_ondo_no_volume():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "k"
        assert request.headers["x-api-signature"]
        path = request.url.path
        if path.endswith("stockList"):
            return httpx.Response(200, json={"status": 0, "data": {"list": [
                {"ticker": "NVDA", "name": "NVIDIA", "status": "online", "contracts": [
                    {"chain": "sol", "contract": "C1", "symbol": "NVDAon", "data_source": "ondo", "status": "online"}]}]}})
        if path.endswith("kline"):
            return httpx.Response(200, json={"status": 0, "data": {"list": [
                {"ts": int(pd.Timestamp("2026-10-16 13:30", tz="UTC").timestamp()), "open": 1, "high": 2, "low": 0.5, "close": 1.5},
                {"ts": int(pd.Timestamp("2026-10-16 13:31", tz="UTC").timestamp()), "open": 1.5, "high": 2, "low": 1, "close": 1.8}]}})
        return httpx.Response(200, json={"status": 0, "data": {}})

    c = BitgetRwaClient(api_key="k", api_secret="s", transport=httpx.MockTransport(handler))
    cs = c.contracts_for("NVDA")
    assert cs and cs[0].data_source == "ondo" and cs[0].issuer_known
    bars = c.fetch_minute_bars("NVDA", date(2026, 10, 16))
    assert len(bars) == 2
    assert bars["venue"].iloc[0] == "token" and bars["source"].iloc[0] == "bitget_rwa:ondo"
    assert all(bars["quality"] & int(QualityFlag.NO_VOLUME_FIELD))


def test_bitget_client_business_failure_raises():
    def handler(request):
        return httpx.Response(200, json={"status": 1, "data": "permission denied", "traceId": "x"})
    c = BitgetRwaClient(api_key="k", api_secret="s", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        c.stock_list()


def test_massive_rows_restricted_to_regular_session_and_rejects_weekend():
    m = MassiveClient(api_key="x")
    rows = [{"t": int(pd.Timestamp("2026-10-16 13:29", tz="UTC").timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 1, "v": 10, "n": 1},
            {"t": int(pd.Timestamp("2026-10-16 13:30", tz="UTC").timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 1, "v": 10, "n": 1}]
    bars = m.rows_to_bars(rows, "NVDA", date(2026, 10, 16))
    assert len(bars) == 1 and bars["venue"].iloc[0] == "native" and bars["source"].iloc[0] == "massive"
    with pytest.raises(ValueError):
        m.rows_to_bars(rows, "NVDA", date(2026, 10, 17))


def test_calendar_rejects_weekend_and_holiday():
    assert not is_trading_day(date(2026, 10, 17))  # 周六
    assert not is_trading_day(date(2026, 7, 3))    # 独立日休市（2026-07-04 为周六，7-3 周五补休）
    assert is_trading_day(date(2026, 10, 16))
    assert previous_trading_day(date(2026, 10, 19)) == date(2026, 10, 16)
    assert len(regular_minute_index(date(2026, 10, 16))) == 390
    assert len(regular_minute_index(date(2026, 11, 27))) == 210  # 黑五早收盘 13:00


def test_simulated_source_is_labelled_and_refuses_non_trading_day(sim_source, nvda_day):
    assert nvda_day["source"].str.startswith("SIMULATED").all()
    assert (nvda_day["quality"] & int(QualityFlag.SIMULATED)).all()
    assert nvda_day["ts"].dt.tz is not None
    with pytest.raises(ValueError):
        sim_source.fetch_minute_bars("NVDA", date(2026, 10, 18))
    rep = coverage_report(nvda_day, date(2026, 10, 16))
    assert rep["simulated"] and rep["expected_bars"] == 390 and 0.95 < rep["coverage"] <= 1


def test_simulated_deterministic_and_token_separate(sim_source):
    a = sim_source.fetch_minute_bars("MSFT", date(2026, 10, 15))
    b = SimulatedSource().fetch_minute_bars("MSFT", date(2026, 10, 15))
    pd.testing.assert_series_equal(a["close"], b["close"])
    t = sim_source.fetch_minute_bars("MSFT", date(2026, 10, 15), venue="token")
    assert t["venue"].iloc[0] == "token" and (t["close"] != a["close"].reindex(t.index)).any()
    assert regime_for_day("MSFT", date(2026, 10, 15)) in {"trend_up", "trend_down", "mean_revert", "high_vol", "quiet"}


def test_quality_flags_and_research_ready(nvda_day):
    assert "GAP_BEFORE" in describe_flags(int(QualityFlag.GAP_BEFORE | QualityFlag.SIMULATED))
    df = nvda_day.copy()
    df.loc[df.index[-1], "is_complete"] = False
    from bgw_quant.data.quality import apply_quality_flags
    df = apply_quality_flags(df)
    rr = research_ready(df)
    assert len(rr) == len(df) - 1


def test_store_roundtrip(nvda_day):
    st = Store(":memory:")
    assert st.upsert_bars(nvda_day) == len(nvda_day)
    assert st.upsert_bars(nvda_day) == len(nvda_day)  # 幂等
    assert len(st.load_bars("NVDA")) == len(nvda_day)
    assert st.available_days("NVDA") == [date(2026, 10, 16)]
    assert bool(st.sources_summary()["simulated"].iloc[0])


def test_symbols_config_spcx_not_tradable():
    syms = {s["ticker"]: s for s in research_symbols()}
    assert len(syms) == 10
    assert syms["SPCX"]["tradable"] is False and syms["SPCX"]["verification_required"]
    assert len(research_symbols(tradable_only=True)) == 9
    assert "reality" in load_yaml("symbols.yaml")["issuers_unconfirmed"]


def test_data_plan_falls_back_to_simulated_without_keys(monkeypatch):
    monkeypatch.setenv("BGW_API_KEY", "")
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    from bgw_quant import config
    config.get_settings.cache_clear()
    plan = build_data_plan(force_mode="auto", do_probe=True)
    assert plan.is_simulated and plan.blockers and "SIMULATED" in plan.banner()
    with pytest.raises(RuntimeError):
        build_data_plan(force_mode="live", do_probe=True)
    config.get_settings.cache_clear()
