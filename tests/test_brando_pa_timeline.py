"""第九阶段最终检查：时间顺序回归——决策之后才能提交订单；执行 K 线开盘必须晚于决策；决策前开盘的 K 线绝不成交；只用已收盘 K 线记账；
成交事件字段完整；纠错日志不改原始事件；缺口补齐与来源切换一致性；账户真实性（无负现金、无重复成交、未成交不计已实现）。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.audit import audit_timeline, repair_invalid_orders
from brando_pa.forward.datafeed import MarketDataService
from brando_pa.trendsys.paper import verify_chain
from tests.test_brando_pa_forward import _FakeSource, _store, _synth, SYMS

REQUIRED = ["signal_candle_close_time", "decision_time", "order_submit_time", "execution_time", "execution_price", "market_data_source", "order_status", "cash_before", "cash_after", "position_before", "position_after", "equity_after", "realized_pnl_usdt", "unrealized_pnl_usdt"]


def test_decision_before_execution_slot_and_no_backdated_fill(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars); r = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)
    r.run_once(now=pd.Timestamp("2024-08-01 05:00", tz="UTC"))
    # 决策在 03:26（日线 00:00 收盘，宽限期内）→ 执行时隙必须是 04:00 开盘的 K 线，而不是 03:00
    out = r.run_once(now=pd.Timestamp("2024-08-02 03:26", tz="UTC"))
    pend = out["accounts"]["A_BTC_SMA200"]["pending"]; assert pend and all(o["exec_after"] == "2024-08-02 04:00:00+00:00" for o in pend)
    assert all(pd.Timestamp(o["order_submit_time"]) >= pd.Timestamp(o["decision_time"]) for o in pend) and all(pd.Timestamp(o["exec_after"]) > pd.Timestamp(o["decision_time"]) for o in pend)
    # 前瞻成交 = 引擎处理时刻（04:30 ≥ exec_after 04:00）按当时可观察的最新已收盘 1h 收盘价（03:00 K 线，04:00 收盘）成交；不用任何 K 线开盘价
    out = r.run_once(now=pd.Timestamp("2024-08-02 03:59", tz="UTC")); assert not out["accounts"]["A_BTC_SMA200"]["fills"]      # 未到 exec_after
    out = r.run_once(now=pd.Timestamp("2024-08-02 04:30", tz="UTC")); fills = out["accounts"]["A_BTC_SMA200"]["fills"]; assert fills
    f = fills[0]; bar03 = bars["BTCUSDT"]["1h"].set_index("ts").loc[pd.Timestamp("2024-08-02 03:00", tz="UTC")]
    assert f["execution_time"] == "2024-08-02 04:30:00+00:00" and f["price_time"] == "2024-08-02 04:00:00+00:00" and abs(f["reference_price"] - float(bar03["close"])) < 1e-9
    assert pd.Timestamp(f["decision_time"]) < pd.Timestamp(f["price_time"]) <= pd.Timestamp(f["execution_time"]) == pd.Timestamp(f["fill_recorded_at"]) and "bar_open" not in f
    for k in REQUIRED:
        assert k in f, k
    assert f["order_status"] == "filled_estimated" and f["estimated"] and abs(f["equity_after"] - (f["cash_after"] + sum(f["position_after"][s] * f["mark_prices"][s] for s in SYMS))) < 1e-6
    a = audit_timeline(tmp_path / "fw"); assert a["ok"], a
    # 注入一张"决策后用更早 K 线"的坏订单 → fill_pending 拒绝成交并作废
    acc = r.accounts["A_BTC_SMA200"]; st = acc.state
    st["pending"].append({"order_id": "A_BTC_SMA200:cost:BTCUSDT:bad:SELL:9", "ledger": "cost", "symbol": "BTCUSDT", "side": "SELL", "qty": 0.001, "amount": None, "signal_ts": "2024-08-02 00:00:00+00:00", "decision_time": "2024-08-02 03:26:00+00:00", "order_submit_time": "2024-08-02 03:26:00+00:00", "exec_after": "2024-08-02 03:00:00+00:00", "reason": "injected", "order_status": "submitted"})
    acc.save(); out = r.run_once(now=pd.Timestamp("2024-08-02 06:10", tz="UTC"))
    ev = [json.loads(l) for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl")]
    # 该坏订单 exec_after 03:00，引擎 06:10 处理：已超过宽限（3h）→ expired，绝不以 03:00 开盘价成交
    assert any(e["kind"] == "order_expired" and e["order_id"].endswith(":bad:SELL:9") for e in ev) and not any(e["kind"] == "fill" and e["order_id"].endswith(":bad:SELL:9") for e in ev)


def test_corrections_log_marks_invalid_orders_without_touching_originals(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars); r = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)
    r.run_once(now=pd.Timestamp("2024-08-01 05:00", tz="UTC")); r.run_once(now=pd.Timestamp("2024-08-02 03:26", tz="UTC"))
    d = tmp_path / "fw" / "A_BTC_SMA200"; st = json.loads((d / "state.json").read_text())
    # 把挂单改成有缺陷的 03:00 时隙（模拟修复前产生的记录），并在事件链里追加一条同样的 order 事件
    from brando_pa.trendsys.paper import _append
    for o in st["pending"]:
        o["exec_after"] = "2024-08-02 03:00:00+00:00"; _append(d / "events.jsonl", {"kind": "order", "account": "A_BTC_SMA200", **o})
    (d / "state.json").write_text(json.dumps(st, default=str))
    before = (d / "events.jsonl").read_text().splitlines(); n_before = len(before)
    a = audit_timeline(tmp_path / "fw"); assert not a["ok"] and any(v["rule"] == "T3-slot" for v in a["accounts"]["A_BTC_SMA200"]["violations"])
    rep = repair_invalid_orders(tmp_path / "fw", now=pd.Timestamp("2024-08-02 03:40", tz="UTC")); assert rep["A_BTC_SMA200"]
    after = (d / "events.jsonl").read_text().splitlines()
    assert after[:n_before] == before                                              # 原始事件一字未改
    assert verify_chain(d / "events.jsonl")["ok"] and verify_chain(d / "corrections.jsonl")["ok"]
    corr = [json.loads(l) for l in open(d / "corrections.jsonl")]; assert all(c["kind"] == "order_invalidated" and not c["filled"] for c in corr)
    st2 = json.loads((d / "state.json").read_text()); assert not st2["pending"] and all(o in st2["processed_orders"] for o in rep["A_BTC_SMA200"])
    a2 = audit_timeline(tmp_path / "fw"); assert all(v["rule"] != "T3-slot" or v["status"] == "invalidated" for v in a2["accounts"]["A_BTC_SMA200"]["violations"])
    r2 = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)                   # 纠错在服务停止后进行；重启后的实例从 state.json 恢复
    out = r2.run_once(now=pd.Timestamp("2024-08-02 05:10", tz="UTC"))               # 作废后不会以历史价格补成交
    assert not out["accounts"]["A_BTC_SMA200"]["fills"] and out["accounts"]["A_BTC_SMA200"]["nav"]["cost"]["cash"] == 10000.0


def test_account_truthfulness_invariants(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars); r = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)
    r.run_once(now=pd.Timestamp("2024-08-01 05:00", tz="UTC"))
    for t in pd.date_range("2024-08-02 00:10", "2024-09-16 23:10", freq="50min", tz="UTC"):
        r.run_once(now=t)
    a = audit_timeline(tmp_path / "fw"); assert a["ok"], [v for x in a["accounts"].values() for v in x["violations"]][:5]
    for n, acc in r.accounts.items():
        ev = [json.loads(l) for l in open(tmp_path / "fw" / n / "events.jsonl")]
        fills = [e for e in ev if e["kind"] == "fill"]; assert len({f["order_id"] for f in fills}) == len(fills)          # 无重复成交
        for k in ("ideal", "cost"):
            L = acc.ledger(k); assert L.cash >= 0 and all(L.qty(s) >= 0 for s in SYMS)                                  # 现金非负、无卖空
            realized_from_fills = sum(f.get("net_pnl", 0.0) for f in fills if f["ledger"] == k and f["side"] == "SELL")
            assert abs(L.realized - realized_from_fills) < 1e-6                                                            # 已实现只来自已成交卖出
        cost_fees = sum(f["fee"] for f in fills if f["ledger"] == "cost"); ideal_fees = sum(f["fee"] for f in fills if f["ledger"] == "ideal")
        assert ideal_fees == 0 and (cost_fees > 0 if any(f["ledger"] == "cost" for f in fills) else True)
        nav = [e for e in ev if e["kind"] == "nav"][-1]
        for k in ("ideal", "cost"):
            l = nav["ledgers"][k]; assert abs(l["equity_usdt"] - (l["cash"] + sum(l["holdings"][s] * nav["prices"][s] for s in SYMS))) < 1e-6   # 净资产 = 持仓 × 市价 + 现金


class _GappySource(_FakeSource):
    name = "gappy_exchange"

    def fetch(self, symbol, tf, start, end):
        df = super().fetch(symbol, tf, start, end)
        drop = pd.Timestamp("2024-06-01 06:00", tz="UTC"); return df[df["ts"] != drop]


def test_backup_gap_fill_and_switch_consistency(tmp_path):
    bars = _synth(); now = pd.Timestamp("2024-06-01 12:30", tz="UTC")
    store = _store(tmp_path, bars, upto=pd.Timestamp("2024-05-30", tz="UTC"))
    import brando_pa.forward.datafeed as df_mod; df_mod.time.sleep = lambda *_: None
    good = _FakeSource(bars); gappy = _GappySource(bars)
    svc = MarketDataService(cfg(), store=store, log_dir=tmp_path / "log", sources=[good, gappy], max_retries=1, seed_from=None)
    st = svc.update(SYMS, ["1h", "1d"], now=now); assert st.ok and st.source == "fake_exchange" and not st.quality_fault
    # 主源断开 → 备用源有缺口 → 从其他可达来源补齐并验证（这里主源失败不可用于补齐 → 缺口无法补齐 → 质量异常，不参与决策）
    good.fail = 99; now2 = now + pd.Timedelta(hours=6)
    st = svc.update(SYMS, ["1h", "1d"], now=now2)
    assert st.source == "gappy_exchange" and st.source_switch.get("to") == "gappy_exchange"
    assert st.consistency.get("switch_signal_check") is not None and all(not v.get("trend_mismatch") for v in st.consistency["switch_signal_check"].values())   # 切换时 SMA200/趋势一致性检查
    # 恢复主源后，缺口由主源补齐并通过偏差验证
    good.fail = 0; svc.sources = [gappy, good]; st3 = svc.update(SYMS, ["1h", "1d"], now=now2 + pd.Timedelta(hours=1))
    h = store.load("BTCUSDT", "1h", allow_fallback=False); d = h["ts"].diff().dropna()
    assert (d == pd.Timedelta(hours=1)).all(), "缺口应被补齐"
