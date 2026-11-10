"""第二十二阶段：主动方向口径（OKX side = 主动方；Coinbase side = 挂单方需取反）；归档按 UTC 重新切日；成交编号缺口检测与回补；分档深度；
订单流条件与证据等级无未来数据；验证的成交 / 成本 / 最大不利波动 / 冷却 / 判定；页面标注与无交易控件；不影响第二十一阶段冻结。
（合成数据只在本测试中用于检验代码，不进入任何结果。）"""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.pa21 import acceptance21, freeze21
from brando_pa.pa22 import archive22, collector22, dashboard22, flow22, phase22, validate22

ROOT = Path(__file__).resolve().parents[1]
T = lambda s: pd.Timestamp(s, tz="UTC")


def _csv(rows):
    return ("instrument_name,trade_id,side,price,size,created_time\r\n" + "".join(f"BTC-USDT,{t},{s},{p},{q},{ms}\r\n" for t, s, p, q, ms in rows)).encode()


def test_archive_parse_utc_split_and_sides():
    t0 = int(T("2024-01-01 16:00").timestamp() * 1000)                                   # 归档文件 2024-01-02 从 UTC 01-01 16:00 开始
    rows = [(10, "BUY", 100.0, 2.0, t0 + 1), (11, "sell", 101.0, 1.0, t0 + 2), (12, "buy", 50_000.0, 3.0, t0 + 3600_000 * 8 + 5), (13, "SELL", 50_000.0, 1.0, t0 + 3600_000 * 8 + 6)]
    df = archive22.parse_trades(_csv(rows)); h = archive22.hourly(df, "BTC-USDT")
    assert list(h["hour"]) == [T("2024-01-01 16:00"), T("2024-01-02 00:00")]
    assert h["tb"].iloc[0] == 200.0 and h["ts"].iloc[0] == 101.0 and h["ltb"].iloc[1] == 150_000.0 and h["lts"].iloc[1] == 0.0       # 大额门槛 10 万
    with pytest.raises(ValueError):
        archive22.parse_trades(_csv([(1, "bid", 1.0, 1.0, t0)]))
    d = archive22.daily(h.assign(n=h["n"]))
    assert list(d["ts"]) == [T("2024-01-01"), T("2024-01-02")] and d["buy_usdt"].iloc[0] == 200.0 and d["imb"].iloc[0] == pytest.approx((200 - 101) / 301)


def test_coinbase_side_is_inverted_okx_is_not():
    t = {"trade_id": 5, "side": "buy", "size": "1", "price": "10", "time": "2026-11-10T00:00:00.000Z"}
    assert collector22.cb_norm(t, "BTC-USDT", 0)["taker"] == "sell" and collector22.cb_norm(t | {"side": "sell"}, "BTC-USDT", 0)["taker"] == "buy"
    assert collector22.okx_norm({"instId": "BTC-USDT", "tradeId": "7", "ts": "1", "px": "10", "sz": "1", "side": "buy"}, 0)["taker"] == "buy"


class FakeNet:
    """按 URL 返回预设响应（测试用）。"""
    def __init__(self, okx_batches, okx_hist):
        self.okx_batches = list(okx_batches); self.okx_hist = okx_hist; self.bytes = 0; self.requests = 0; self.errors = 0

    def get(self, url, params=None):
        self.requests += 1
        class R:
            def __init__(s, j): s._j = j; s.headers = {}; s.content = b"x"
            def json(s): return s._j
        if url.endswith("/market/trades"):
            return R({"data": [{"instId": "BTC-USDT", "tradeId": str(t), "ts": str(1000 + t), "px": "100", "sz": "1", "side": "buy"} for t in sorted(self.okx_batches.pop(0), reverse=True)]})
        if url.endswith("/market/history-trades"):
            after = int(params["after"]); got = sorted([t for t in self.okx_hist if t < after], reverse=True)[:100]
            return R({"data": [{"instId": "BTC-USDT", "tradeId": str(t), "ts": str(1000 + t), "px": "100", "sz": "1", "side": "sell"} for t in got]})
        raise AssertionError(url)


def test_gap_backfill_and_unresolved_gap():
    s = collector22.Stream("okx", "BTC-USDT"); net = FakeNet([range(100, 105), range(110, 115)], okx_hist=range(105, 110))
    s.poll(net); new = s.poll(net); assert [x["tid"] for x in new] == list(range(105, 115)) and s.gaps_backfilled == 1 and s.gaps_unresolved == 0 and s.missing_ids == 0
    s2 = collector22.Stream("okx", "BTC-USDT"); net2 = FakeNet([range(100, 105), range(110, 115)], okx_hist=range(107, 110))
    s2.poll(net2); s2.poll(net2); assert s2.gaps_unresolved == 1 and s2.missing_ids == 2


def test_band_depth():
    bids = [["100", "1"], ["99.6", "2"], ["98.5", "4"]]; asks = [["100.2", "1"], ["101", "3"], ["103", "5"]]; b = collector22.band_depth(bids, asks)
    mid = 100.1; assert b["mid"] == pytest.approx(mid) and b["bid_0.005"] == pytest.approx(100 + 99.6 * 2) and b["ask_0.01"] == pytest.approx(100.2 + 101 * 3) and b["covered_0.02"] is False


def _synthetic_daily(n=80, seed=1):
    rng = np.random.default_rng(seed); c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))); o = np.r_[100, c[:-1]]
    tot = np.full(n, 1e6); buy = tot * 0.5
    d = pd.DataFrame({"ts": pd.date_range(T("2025-01-01"), periods=n, freq="D"), "open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1000.0, "buy_usdt": buy, "sell_usdt": tot - buy, "hours": 24, "missing_ids": 0})
    return d


def _fin(d):
    d = d.copy(); d["tot"] = d["buy_usdt"] + d["sell_usdt"]; d["net"] = d["buy_usdt"] - d["sell_usdt"]; d["imb"] = d["net"] / d["tot"]; return d


def test_flow_B_V_Q_definitions():
    d = _synthetic_daily(); i = 70
    d.loc[i - 2:i, "buy_usdt"] = 0.56e6; d.loc[i - 2:i, "sell_usdt"] = 0.44e6                       # 3 天失衡 +12%，之前 10 天 0 → B
    d.loc[i, "buy_usdt"] = 1.2e6; d.loc[i, "sell_usdt"] = 0.9e6; d.loc[i, "close"] = d.loc[i, "high"] * 0.99            # 成交额 2.1 倍、收盘位置高、净额 ≥ 0 → V
    st = flow22.evaluate(_fin(d)); r = st.iloc[i]
    assert r["B"] and r["V"] and r["Q"]
    d.loc[i - 5, "missing_ids"] = 3; st2 = flow22.evaluate(_fin(d)); assert not st2.iloc[i]["Q"] and st2.iloc[i]["level"] == "证据不足"


def test_flow_no_future_data():
    d = _fin(_synthetic_daily(160, seed=4)); full = flow22.evaluate(d)
    for cut in (90, 120, 150):
        part = flow22.evaluate(d.iloc[:cut + 1]); cols = ["L", "A", "B", "V", "R", "Q", "level"]
        assert part[cols].equals(full.iloc[:cut + 1][cols].reset_index(drop=True)), cut


def test_validation_math_and_verdict():
    d = pd.DataFrame({"ts": pd.date_range(T("2024-01-01"), periods=200, freq="D"), "open": 100.0, "close": 110.0, "low": 90.0, "high": 120.0})
    r = validate22.outcomes(d, [10], 30); side = 0.0015
    assert r["ret_net"].iloc[0] == pytest.approx(110 * (1 - side) / (100 * (1 + side)) - 1) and r["mae"].iloc[0] == pytest.approx(-0.10)
    assert validate22.outcomes(d, [190], 30).empty                                                        # 未到期不计
    assert validate22.pick(np.array([1, 1, 1] + [0] * 30 + [1]), cooldown=30) == [0, 33]
    lo, hi = validate22.wilson(8, 10); assert 0.44 < lo < 0.5 and 0.94 < hi < 0.98
    empty = {seg: {n: {h: {"pooled": {"n": 0, "n_independent": 0}} for h in validate22.HORIZONS} for n in ("confirm", "control_L")} for seg in ("holdout",)}
    assert validate22.verdict(empty)["verdict"] == "证据不足"


def test_run_and_dashboard_on_test_archive(tmp_path, monkeypatch):
    dd = tmp_path / "data"; ad = dd / "okx_archive"; ad.mkdir(parents=True)
    for s in ("BTC-USDT", "ETH-USDT"):
        rng = np.random.default_rng(3); hours = pd.date_range(T("2025-01-01"), periods=24 * 120, freq="h"); c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, len(hours)))); tb = rng.uniform(4e5, 6e5, len(hours))
        h = pd.DataFrame({"symbol": s, "hour": hours, "tb": tb, "ts": 1e6 - tb, "bb": 1.0, "bs": 1.0, "nb": 10, "ns": 10, "ltb": 0.0, "lts": 0.0, "open": np.r_[100, c[:-1]], "high": c * 1.002, "low": c * 0.998, "close": c})
        h["n"] = 20; h["first_tid"] = np.arange(len(h)) * 20; h["last_tid"] = h["first_tid"] + 19; h.to_parquet(ad / f"{s}_hourly.parquet", index=False)
    out = tmp_path / "out"; R = phase22.run(dd, out, check_rubik=False)
    assert set(R["current"]) == {"BTC-USDT", "ETH-USDT"} and R["verdict"]["verdict"] == "证据不足" and R["current"]["BTC-USDT"]["forward_level"] in flow22.LEVELS
    page = dashboard22.build(out, dd).read_text(encoding="utf-8")
    for x in ("真实买盘监测", "真实成交 · 逐笔主动方向", "挂单 · 可撤销，不计为买盘", "独立样本验证", "证据等级", "Binance 在本环境返回 451"):
        assert x in page, x
    assert "<button" not in page and "<form" not in page and "立即买入" not in page


def test_no_order_api_and_phase21_freeze_untouched():
    assert acceptance21.static_no_order_api(ROOT / "src" / "brando_pa" / "pa22")["ok"] and freeze21.verify()["ok"]
