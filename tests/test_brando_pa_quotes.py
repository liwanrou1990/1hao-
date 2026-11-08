"""前瞻成交报价精度：实时 Bid/Ask（BUY 用 Ask、SELL 用 Bid）、报价时间戳与来源、新鲜度阈值（过期 → 暂停成交，不用历史价格）、
仅有已收盘 K 线 → 明确标记 estimated、断线恢复、不看后续价格决定是否执行。"""
import json

import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.audit import audit_timeline
from brando_pa.forward.quotes import Quote
from tests.test_brando_pa_causality import _synth_oc, T
from tests.test_brando_pa_forward import _store, SYMS


class FakeQuotes:
    """可控报价源：按处理时刻给出 Bid/Ask；可设置延迟（报价过期）或断线（抛错/None）。"""

    def __init__(self, bars, spread_bps=2.0, lag_s=5, fail=False, none=False):
        self.bars, self.spread, self.lag, self.fail, self.none = bars, spread_bps, lag_s, fail, none; self.now = None; self.calls = 0

    def __call__(self, symbol):
        self.calls += 1
        if self.fail:
            raise ConnectionError("quote endpoint down")
        if self.none:
            return None
        h = self.bars[symbol]["1h"]; closed = h[(h["ts"] + pd.Timedelta(hours=1)) <= self.now]
        mid = float(closed["close"].iloc[-1]) * 1.001          # 与 K 线收盘略有差异，便于区分
        return Quote(symbol, mid * (1 - self.spread / 2e4), mid * (1 + self.spread / 2e4), self.now - pd.Timedelta(seconds=self.lag), "fake_ticker", self.now)


def _setup(tmp_path, quotes, name="fw"):
    bars = _synth_oc(); store = _store(tmp_path / name, bars); r = ForwardRunner(cfg(), log_dir=tmp_path / name / "logs", store=store, quote_provider=quotes); return r, store, bars


def _run(r, q, t):
    q.now = T(t); return r.run_once(now=T(t))


def _fills(tmp_path, name="fw", acc="A_BTC_SMA200"):
    return [json.loads(l) for l in open(tmp_path / name / "logs" / acc / "events.jsonl") if json.loads(l)["kind"] == "fill"]


def test_buy_uses_ask_sell_uses_bid_with_quote_timestamp_and_source(tmp_path):
    q = FakeQuotes(_synth_oc()); r, store, bars = _setup(tmp_path, q)
    _run(r, q, "2024-08-01 05:00"); _run(r, q, "2024-08-02 00:30"); out = _run(r, q, "2024-08-02 01:05")
    f = [x for x in out["accounts"]["A_BTC_SMA200"]["fills"] if x["ledger"] == "ideal"][0]
    assert f["price_kind"] == "live_quote_ask" and not f["estimated"] and f["order_status"] == "filled" and f["market_data_source"] == "fake_ticker"
    assert abs(f["reference_price"] - f["quote"]["ask"]) < 1e-9 and f["price_time"] == str(T("2024-08-02 01:05") - pd.Timedelta(seconds=5)) and f["quote"]["age_s"] == 5
    assert T(f["decision_time"]) < T(f["price_time"]) <= T(f["execution_time"])
    fc = [x for x in out["accounts"]["A_BTC_SMA200"]["fills"] if x["ledger"] == "cost"][0]
    assert fc["execution_price"] > fc["reference_price"] and abs(fc["execution_price"] / fc["reference_price"] - (1 + 5 / 1e4)) < 1e-12     # Ask 之上再加预登记滑点 5 bps；手续费另计
    # 卖出用 Bid：推进到 BTC 趋势失效
    for t in pd.date_range("2024-08-03 00:30", "2024-09-16 00:30", freq="1D", tz="UTC"):
        _run(r, q, str(t)[:16]); _run(r, q, str(t + pd.Timedelta(minutes=35))[:16])
    sells = [x for x in _fills(tmp_path) if x["side"] == "SELL"]; assert sells and all(x["price_kind"] == "live_quote_bid" and abs(x["reference_price"] - x["quote"]["bid"]) < 1e-9 for x in sells)
    assert audit_timeline(tmp_path / "fw" / "logs")["ok"]


def test_stale_quote_falls_back_to_estimated_or_pauses_never_old_price(tmp_path):
    q = FakeQuotes(_synth_oc(), lag_s=600); r, store, bars = _setup(tmp_path, q)                   # 报价落后 10 min > 120 s → 过期
    _run(r, q, "2024-08-01 05:00"); _run(r, q, "2024-08-02 00:30"); out = _run(r, q, "2024-08-02 01:05")
    f = out["accounts"]["A_BTC_SMA200"]["fills"][0]
    assert f["estimated"] and f["price_kind"] == "estimated_closed_bar_close" and f["order_status"] == "filled_estimated" and "ESTIMATED" in f["note"] and f["price_time"] == "2024-08-02 01:00:00+00:00"
    bar00 = bars["BTCUSDT"]["1h"].set_index("ts").loc[T("2024-08-02 00:00")]; assert abs(f["reference_price"] - float(bar00["close"])) < 1e-9
    # 报价过期且已收盘 K 线也过旧（行情断线 2 小时）→ 暂停成交，记录原因；不使用更早价格
    bars2 = _synth_oc(); store2 = _store(tmp_path / "fw2", bars2, upto=T("2024-08-03 01:00"))     # 1h 数据只到 00:00 K 线（01:00 收盘）
    q2 = FakeQuotes(bars2, lag_s=600); r2 = ForwardRunner(cfg(), log_dir=tmp_path / "fw2" / "logs", store=store2, quote_provider=q2)
    _run(r2, q2, "2024-08-02 05:00"); _run(r2, q2, "2024-08-03 00:30")                              # exec_after 01:00
    out = _run(r2, q2, "2024-08-03 02:15")                                                           # 最新已收盘收盘价时间 01:00，年龄 75 min > 70 min → 暂停
    assert not out["accounts"]["A_BTC_SMA200"]["fills"] and out["accounts"]["A_BTC_SMA200"]["pending"]
    ev = [json.loads(l) for l in open(tmp_path / "fw2" / "logs" / "A_BTC_SMA200" / "events.jsonl")]
    assert any(e["kind"] == "fill_paused" and "no fresh price" in e["reason"] for e in ev)
    # 行情恢复（有新鲜报价）→ 在恢复时刻按实时 Ask 成交（late），不是 01:00 的价格
    q2.lag = 5; out = _run(r2, q2, "2024-08-03 02:40"); f = out["accounts"]["A_BTC_SMA200"]["fills"][0]
    assert f["price_kind"] == "live_quote_ask" and f["late"] and f["order_status"] == "filled_late" and f["execution_time"] == "2024-08-03 02:40:00+00:00"
    assert audit_timeline(tmp_path / "fw2" / "logs")["ok"]


def test_quote_endpoint_down_then_recovers(tmp_path):
    q = FakeQuotes(_synth_oc(), fail=True); r, store, bars = _setup(tmp_path, q)
    _run(r, q, "2024-08-01 05:00"); _run(r, q, "2024-08-02 00:30"); out = _run(r, q, "2024-08-02 01:05")
    f = out["accounts"]["A_BTC_SMA200"]["fills"][0]                                                    # 报价接口异常 → 退回估算（已收盘收盘价，新鲜）并标记
    assert f["estimated"] and "error" in f["quote"] and "ConnectionError" in f["quote"]["error"]
    q.fail = False                                                                                           # 报价接口恢复
    for t in pd.date_range("2024-08-03 00:30", "2024-09-16 00:30", freq="1D", tz="UTC"):
        _run(r, q, str(t)[:16]); _run(r, q, str(t + pd.Timedelta(minutes=35))[:16])
    later = [x for x in _fills(tmp_path) if x["execution_time"] >= "2024-08-03"]
    assert later and all(x["price_kind"].startswith("live_quote") and not x["estimated"] for x in later)      # 恢复后全部用实时报价（含趋势失效后的卖出）
    assert audit_timeline(tmp_path / "fw" / "logs")["ok"]


def test_execution_does_not_depend_on_future_prices(tmp_path):
    """同一决策、同一处理时刻：之后的价格路径不同，成交与否、成交价完全相同。"""
    res = {}
    for tag, bump in (("a", 1.0), ("b", 1.3)):
        bars = _synth_oc(); h = bars["BTCUSDT"]["1h"]; m = h["ts"] > T("2024-08-02 01:05"); h.loc[m, ["open", "high", "low", "close"]] *= bump
        store = _store(tmp_path / tag, bars); q = FakeQuotes(bars); r = ForwardRunner(cfg(), log_dir=tmp_path / tag / "logs", store=store, quote_provider=q)
        _run(r, q, "2024-08-01 05:00"); _run(r, q, "2024-08-02 00:30"); out = _run(r, q, "2024-08-02 01:05")
        res[tag] = [(x["order_id"], round(x["reference_price"], 8), x["execution_time"], x["price_kind"]) for x in out["accounts"]["A_BTC_SMA200"]["fills"]]
    assert res["a"] == res["b"] and res["a"]
