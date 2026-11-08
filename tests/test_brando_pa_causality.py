"""前瞻成交因果顺序测试（最后一次真实性检查）：正常轮询、延迟轮询、断线、重启、离线过期。
不变量：决策时刻 < 价格时间 ≤ 执行时间（= 引擎处理时刻 = 记账时刻）；执行时间 ≥ exec_after；绝不用处理时刻之前某根 K 线的开盘价；
离线恢复后只能 expired 或 late（按预登记规则，不看后来的 K 线结果决定）；回放模式单独标注。"""
import json
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.audit import audit_timeline
from tests.test_brando_pa_forward import _store, _synth, SYMS

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _synth_oc():
    """开盘 ≠ 收盘的合成行情（开盘 = 上一根收盘），用于区分“开盘价回填”与“可观察收盘价”。"""
    bars = _synth()
    for s_ in SYMS:
        h = bars[s_]["1h"]; h["open"] = h["close"].shift(1).fillna(h["close"]); h["high"] = h[["open", "close"]].max(axis=1) * 1.001; h["low"] = h[["open", "close"]].min(axis=1) * 0.999
        bars[s_]["1d"] = h.set_index("ts").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).reset_index()
    return bars


def _runner(tmp_path, bars=None, upto=None, name="fw"):
    bars = bars or _synth_oc(); store = _store(tmp_path / name, bars, upto=upto); return ForwardRunner(cfg(), log_dir=tmp_path / name / "logs", store=store), store, bars


def _fills(tmp_path, name="fw", acc="A_BTC_SMA200"):
    return [json.loads(l) for l in open(tmp_path / name / "logs" / acc / "events.jsonl") if json.loads(l)["kind"] == "fill"]


def _events(tmp_path, name="fw", acc="A_BTC_SMA200"):
    return [json.loads(l) for l in open(tmp_path / name / "logs" / acc / "events.jsonl")]


def test_normal_polling_executes_at_processing_time_with_observable_close(tmp_path):
    r, store, bars = _runner(tmp_path)
    r.run_once(now=T("2024-08-01 05:00")); out = r.run_once(now=T("2024-08-02 00:30"))         # 决策 00:30 → exec_after 01:00
    pend = out["accounts"]["A_BTC_SMA200"]["pending"]; assert pend and pend[0]["exec_after"] == "2024-08-02 01:00:00+00:00"
    out = r.run_once(now=T("2024-08-02 00:55")); assert not out["accounts"]["A_BTC_SMA200"]["fills"]   # 未到 exec_after
    out = r.run_once(now=T("2024-08-02 01:05")); f = out["accounts"]["A_BTC_SMA200"]["fills"]; assert f
    f = f[0]; bar00 = bars["BTCUSDT"]["1h"].set_index("ts").loc[T("2024-08-02 00:00")]
    assert f["execution_time"] == "2024-08-02 01:05:00+00:00" and f["price_time"] == "2024-08-02 01:00:00+00:00" and abs(f["reference_price"] - float(bar00["close"])) < 1e-9
    assert f["order_status"] == "filled_estimated" and f["estimated"] and not f["late"] and T(f["decision_time"]) < T(f["price_time"]) <= T(f["execution_time"]) == T(f["fill_recorded_at"])   # 无实时报价 → 明确标记 estimated
    assert "bar_open" not in f                                                                   # 前瞻不使用任何 K 线开盘价
    assert audit_timeline(tmp_path / "fw" / "logs")["ok"]


def test_delayed_polling_marks_late_and_uses_latest_close_not_old_open(tmp_path):
    r, store, bars = _runner(tmp_path)
    r.run_once(now=T("2024-08-01 05:00")); r.run_once(now=T("2024-08-02 00:30"))                 # exec_after 01:00
    out = r.run_once(now=T("2024-08-02 02:30")); f = out["accounts"]["A_BTC_SMA200"]["fills"][0]  # 引擎 02:30 才处理
    bar01 = bars["BTCUSDT"]["1h"].set_index("ts").loc[T("2024-08-02 01:00")]
    assert f["late"] and f["order_status"] == "filled_late_estimated" and f["execution_time"] == "2024-08-02 02:30:00+00:00" and f["price_time"] == "2024-08-02 02:00:00+00:00"
    assert abs(f["reference_price"] - float(bar01["close"])) < 1e-9 and abs(f["reference_price"] - float(bar01["open"])) > 1e-9      # 用 02:00 可观察收盘价，不是 01:00 开盘价
    assert audit_timeline(tmp_path / "fw" / "logs")["ok"]


def test_offline_recovery_at_0510_for_exec_after_0400_is_late_not_backdated(tmp_path):
    """用户场景：03:53 决策（exec_after 04:00），服务 05:10 才恢复 → 不得按 04:00 开盘价记账。"""
    r, store, bars = _runner(tmp_path)
    r.run_once(now=T("2024-08-01 05:00")); out = r.run_once(now=T("2024-08-02 03:53"))
    pend = out["accounts"]["A_BTC_SMA200"]["pending"]; assert pend and pend[0]["exec_after"] == "2024-08-02 04:00:00+00:00"
    r2 = ForwardRunner(cfg(), log_dir=tmp_path / "fw" / "logs", store=store)                       # 服务重启（状态恢复）
    out = r2.run_once(now=T("2024-08-02 05:10")); f = out["accounts"]["A_BTC_SMA200"]["fills"]; assert f
    f = f[0]; h = bars["BTCUSDT"]["1h"].set_index("ts")
    assert f["late"] and f["execution_time"] == "2024-08-02 05:10:00+00:00" and f["price_time"] == "2024-08-02 05:00:00+00:00"
    assert abs(f["reference_price"] - float(h.loc[T("2024-08-02 04:00"), "close"])) < 1e-9 and abs(f["reference_price"] - float(h.loc[T("2024-08-02 04:00"), "open"])) > 1e-9
    assert T(f["fill_recorded_at"]) == T("2024-08-02 05:10") and T(f["decision_time"]) < T(f["price_time"]) <= T(f["execution_time"])
    assert audit_timeline(tmp_path / "fw" / "logs")["ok"]


def test_offline_beyond_grace_expires_without_fill_regardless_of_later_prices(tmp_path):
    r, store, bars = _runner(tmp_path)
    r.run_once(now=T("2024-08-01 05:00")); r.run_once(now=T("2024-08-02 03:53"))                 # exec_after 04:00，宽限 3h → 07:00
    r2 = ForwardRunner(cfg(), log_dir=tmp_path / "fw" / "logs", store=store)
    out = r2.run_once(now=T("2024-08-02 07:30"))
    ev = _events(tmp_path); assert not [e for e in ev if e["kind"] == "fill"] and [e for e in ev if e["kind"] == "order_expired" and e["order_status"] == "expired"]
    assert out["accounts"]["A_BTC_SMA200"]["nav"]["cost"]["cash"] == 10000.0 and not out["accounts"]["A_BTC_SMA200"]["pending"]
    # 过期判定只依赖时间（exec_after + grace），与后来价格无关：同一场景下价格路径改变，结论不变
    bars2 = _synth_oc(); bars2["BTCUSDT"]["1h"]["close"] *= 1.5; bars2["BTCUSDT"]["1h"]["open"] *= 1.5
    r3, store3, _ = _runner(tmp_path, bars=bars2, name="fw2"); r3.run_once(now=T("2024-08-01 05:00")); r3.run_once(now=T("2024-08-02 03:53"))
    ForwardRunner(cfg(), log_dir=tmp_path / "fw2" / "logs", store=store3).run_once(now=T("2024-08-02 07:30"))
    ev2 = _events(tmp_path, "fw2"); assert not [e for e in ev2 if e["kind"] == "fill"] and [e for e in ev2 if e["kind"] == "order_expired"]


def test_disconnect_no_fresh_price_keeps_pending_then_fills_or_expires(tmp_path):
    bars = _synth_oc(); r, store, _ = _runner(tmp_path, bars=bars, upto=T("2024-08-02 01:00"))        # 行情只到 00:00 K 线（01:00 收盘）
    r.run_once(now=T("2024-08-01 05:00")); r.run_once(now=T("2024-08-02 00:30"))                 # exec_after 01:00
    out = r.run_once(now=T("2024-08-02 01:05")); f = out["accounts"]["A_BTC_SMA200"]["fills"]       # 01:00 收盘可观察（price_time 01:00 ≥ exec_after）→ 成交
    assert f and f[0]["price_time"] == "2024-08-02 01:00:00+00:00"
    # 第二天：行情断线（没有 exec_after 之后的收盘价）→ 挂单保持 pending，不用旧价格；行情恢复后在处理时刻成交（late）
    bars3 = _synth_oc(); r3, store3, _ = _runner(tmp_path, bars=bars3, upto=T("2024-08-03 00:00"), name="fw3")   # 行情只到 08-02 23:00 K 线（00:00 收盘）
    r3.run_once(now=T("2024-08-02 05:00")); r3.run_once(now=T("2024-08-03 00:30"))               # 决策 00:30 → exec_after 01:00
    out = r3.run_once(now=T("2024-08-03 01:20")); assert not out["accounts"]["A_BTC_SMA200"]["fills"] and out["accounts"]["A_BTC_SMA200"]["pending"]   # 无 01:00 之后的可观察价格
    full = _store(tmp_path / "fw3", bars3)                                                       # 行情恢复
    out = r3.run_once(now=T("2024-08-03 02:40")); f = out["accounts"]["A_BTC_SMA200"]["fills"]; assert f and f[0]["late"] and f[0]["price_time"] == "2024-08-03 02:00:00+00:00" and f[0]["execution_time"] == "2024-08-03 02:40:00+00:00"


def test_restart_between_decision_and_execution_matches_continuous(tmp_path):
    bars = _synth_oc()
    ra, sa, _ = _runner(tmp_path, bars=bars, name="cont"); rb, sb, _ = _runner(tmp_path, bars=bars, name="rest")
    for t in ("2024-08-01 05:00", "2024-08-02 00:30", "2024-08-02 01:05", "2024-08-03 00:30", "2024-08-03 01:05"):
        ra.run_once(now=T(t)); ForwardRunner(cfg(), log_dir=tmp_path / "rest" / "logs", store=sb).run_once(now=T(t))      # 每一步都重启
    for acc in ("A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"):
        fa = _fills(tmp_path, "cont", acc); fb = _fills(tmp_path, "rest", acc)
        assert [(f["order_id"], f["execution_time"], round(f["execution_price"], 8)) for f in fa] == [(f["order_id"], f["execution_time"], round(f["execution_price"], 8)) for f in fb]
    assert audit_timeline(tmp_path / "rest" / "logs")["ok"]


def test_replay_mode_is_labelled_and_separate(tmp_path):
    bars = _synth(); store = _store(tmp_path / "rp", bars); r = ForwardRunner(cfg(), log_dir=tmp_path / "rp" / "logs", store=store, mode="replay")
    r.replay("2024-08-01", "2024-08-05")
    ev = [json.loads(l) for l in open(tmp_path / "rp" / "logs" / "A_BTC_SMA200" / "events.jsonl")]
    fills = [e for e in ev if e["kind"] == "fill"]; assert fills and all(e["mode"] == "replay" and e["fill_rule"].startswith("replay") for e in fills)
    assert all(e.get("mode") == "replay" for e in ev if e["kind"] in ("signal", "order", "market", "nav"))
    assert audit_timeline(tmp_path / "rp" / "logs")["ok"]
