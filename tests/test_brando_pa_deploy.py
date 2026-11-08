"""第九阶段部署验收测试：单实例锁、启动完整性检查、扩展健康检查字段、离线过期订单不回填、来源优先级配置、跨来源审计、72h 验收工具的“未完成”语义。"""
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.forward.acceptance import acceptance_status
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.datafeed import MarketDataService, cross_source_audit
from brando_pa.forward.service import ForwardService
from tests.test_brando_pa_forward import _FakeSource, _store, _synth, SYMS
from tests.test_brando_pa_service import _Clock, _ShiftedSource, _service


def test_single_instance_lock_and_startup_check(tmp_path):
    bars = _synth(); svc, store = _service(tmp_path, bars, pd.Timestamp("2024-07-31 00:00", tz="UTC"), [_FakeSource(bars)])
    assert svc.acquire_lock()
    svc2 = ForwardService(cfg(), log_dir=tmp_path / "svc" / "fw", runner=svc.runner, feed=svc.feed, clock=svc.clock)
    assert not svc2.acquire_lock()                                   # 第二个实例拿不到锁
    svc.release_lock(); assert svc2.acquire_lock(); svc2.release_lock()
    chk = svc.startup_check(); assert chk["ok"] and set(chk["accounts"]) == {"A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"} and (tmp_path / "svc" / "fw" / "startup_check.json").exists()
    # 篡改一条事件 → 启动检查失败并告警
    p = tmp_path / "svc" / "fw" / "A_BTC_SMA200" / "events.jsonl"
    if not p.exists():
        svc.cycle()
    lines = p.read_text().splitlines(); rec = json.loads(lines[0]); rec["kind"] = "x"; lines[0] = json.dumps(rec, ensure_ascii=False); p.write_text("\n".join(lines) + "\n")
    chk = svc.startup_check(); assert not chk["ok"] and any(json.loads(l)["kind"] == "startup_check_failed" for l in open(svc.alerts_path))


def test_health_fields_and_consecutive_failures(tmp_path):
    bars = _synth(); src = _FakeSource(bars); clk = _Clock("2024-08-01 05:00")
    svc, store = _service(tmp_path, bars, pd.Timestamp("2024-07-31 00:00", tz="UTC"), [src], clock=clk)
    svc.cycle(); h = svc.health()
    for k in ("process_alive", "latest_bar_age_hours", "last_ok_cycle_at", "consecutive_failures", "data_source", "accounts", "latest_log_utc", "chains"):
        assert k in h
    assert h["process_alive"] and h["data_source"] == "fake_exchange" and "A_BTC_SMA200" in h["accounts"] and h["latest_bar_age_hours"]["BTCUSDT_1h_h"] < 2
    src.fail = 99
    for i in range(3):
        clk.t = clk.t + pd.Timedelta(minutes=10); svc.cycle()
    h = svc.health(); assert h["consecutive_failures"] >= 3 and any("consecutive" in r for r in h["reasons"]) and any(json.loads(l)["kind"] == "consecutive_failures" for l in open(svc.alerts_path))


def test_offline_pending_order_expires_instead_of_backfill(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars)
    r = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)
    r.run_once(now=pd.Timestamp("2024-08-01 05:00", tz="UTC")); out = r.run_once(now=pd.Timestamp("2024-08-02 00:30", tz="UTC"))
    assert out["accounts"]["A_BTC_SMA200"]["pending"]                                            # 订单挂起，执行 K 线尚未收盘
    out = r.run_once(now=pd.Timestamp("2024-08-03 12:00", tz="UTC"))                            # 服务离线一天半后恢复
    ev = [json.loads(l) for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl")]
    assert not [e for e in ev if e["kind"] == "fill"] and [e for e in ev if e["kind"] == "order_expired"] and not out["accounts"]["A_BTC_SMA200"]["pending"]
    assert [e for e in ev if e["kind"] == "missed_signal" and e["signal_ts"].startswith("2024-08-03")]   # 08-03 00:00 收盘已超宽限 → missed
    assert out["accounts"]["A_BTC_SMA200"]["nav"]["cost"]["holdings"]["BTCUSDT"] == 0.0


def test_source_priority_config_and_cross_source_audit(tmp_path):
    c = cfg(); assert c["forward"]["source_priority"][0] == "okx_spot" and c["forward"]["source_priority"][1] == "coinbase_exchange"
    svc = MarketDataService(c, store=BarStore(root=tmp_path / "b", fallback_root=None), log_dir=tmp_path / "l", seed_from=None)
    assert [s.name for s in svc.sources][:2] == ["okx_spot", "coinbase_exchange"]
    bars = _synth(); svc.sources = [_FakeSource(bars), _ShiftedSource(bars, 0.02)]
    a = cross_source_audit(svc, SYMS, "1h", 48, now=pd.Timestamp("2024-06-01 12:30", tz="UTC"))
    assert a["sources"]["fake_exchange"]["BTCUSDT"]["n_closed"] > 40 and a["sources"]["fake_exchange"]["BTCUSDT"]["duplicates"] > 0 and a["sources"]["fake_exchange"]["BTCUSDT"]["ohlc_consistent"]
    k = "fake_exchange vs other_exchange BTCUSDT"; assert a["pairwise_close_diff"][k]["exceeds_gate"] and abs(a["pairwise_close_diff"][k]["max_rel_diff"] - (1 - 1 / 1.02)) < 1e-6


def test_acceptance_requires_72_real_hours(tmp_path):
    bars = _synth(); svc, _ = _service(tmp_path, bars, None, [], clock=_Clock("2024-08-01 05:00")); svc.feed.sources = []
    for k in range(12):
        svc.clock.t = pd.Timestamp("2024-08-01 05:00", tz="UTC") + pd.Timedelta(minutes=10 * k); svc.cycle()
    st = acceptance_status(tmp_path / "svc" / "fw", 600, now=svc.clock.t)
    assert st["status"] == "进行中" and st["cycles_observed"] == 12 and "不得宣称" in st["verdict_note"] and (tmp_path / "svc" / "fw" / "acceptance_status.md").exists()
    st2 = acceptance_status(tmp_path / "svc" / "fw", 600, now=svc.clock.t + pd.Timedelta(hours=80))
    assert st2["status"] == "未通过" and st2["coverage"] < 0.5                                     # 真实时间过去 80h 但只有 2h 心跳 → 未通过，不会因模拟而通过
