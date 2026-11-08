"""第九阶段：24 小时服务的故障注入与稳定性测试——断网重连、数据过期保护、意外退出/重启、重复执行防护、多次重启权益一致性、
日线收盘时序、跨来源切换与偏差门控、无负现金、期末清仓、日志持久化与轮转、健康检查告警、账户隔离、离线错过信号标记。"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.datafeed import MarketDataService
from brando_pa.forward.service import ForwardService
from brando_pa.trendsys.paper import verify_chain
from tests.test_brando_pa_forward import _FakeSource, _store, _synth, SYMS

import brando_pa.forward.datafeed as df_mod
df_mod.time.sleep = lambda *_: None


class _Clock:
    def __init__(self, t):
        t = pd.Timestamp(t); self.t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")

    def __call__(self):
        return self.t


def _service(tmp, bars, upto, sources, name="svc", clock=None):
    store = _store(tmp / name, bars, upto=upto)
    runner = ForwardRunner(cfg(), log_dir=tmp / name / "fw", store=store)
    feed = MarketDataService(cfg(), store=store, log_dir=tmp / name / "feed", sources=sources, max_retries=2, seed_from=None)
    svc = ForwardService(cfg(), log_dir=tmp / name / "fw", runner=runner, feed=feed, clock=clock or _Clock("2024-08-01 05:00"))
    for h in list(svc.log.handlers):
        svc.log.removeHandler(h)
    from logging.handlers import RotatingFileHandler
    h = RotatingFileHandler(tmp / name / "fw" / "service.log", maxBytes=4000, backupCount=2); svc.log.addHandler(h); svc.log.setLevel(logging.INFO)
    return svc, store


def test_service_disconnect_reconnect_stale_and_health(tmp_path):
    bars = _synth(); src = _FakeSource(bars, fail_first=0); clk = _Clock("2024-08-01 05:00")
    svc, store = _service(tmp_path, bars, pd.Timestamp("2024-07-31 00:00", tz="UTC"), [src], clock=clk)
    out = svc.cycle(); assert out["data"]["ok"] and svc.health()["ok"]
    # 断网：来源持续失败 → 本轮不新增订单、告警、health 不 ok；旧数据保留
    src.fail = 99; clk.t = pd.Timestamp("2024-08-02 00:30", tz="UTC"); out = svc.cycle()
    assert not out["data"]["ok"] and not svc.health()["ok"] and "data update failed" in svc.health()["reasons"]
    al = [json.loads(l) for l in open(svc.alerts_path)]; assert any(a["kind"] == "data_unavailable" for a in al)
    # 持续断网到数据过期：账户信号带 data_fault，敞口 0，无成交
    clk.t = pd.Timestamp("2024-08-02 03:30", tz="UTC"); out = svc.cycle()
    assert all(v["fills"] == 0 for v in out["accounts"].values()) and out["data_check"] and not out["data_check"]["ok"]
    # 重连：补齐缺失 K 线，无缺口；下一个日线收盘（宽限期内）正常执行，并在 1h 收盘后成交
    src.fail = 0; clk.t = pd.Timestamp("2024-08-03 00:30", tz="UTC"); out = svc.cycle(); assert out["data"]["ok"]
    h1 = store.load("BTCUSDT", "1h", allow_fallback=False); assert (h1["ts"].diff().dropna() == pd.Timedelta(hours=1)).all() and h1["ts"].iloc[-1] == pd.Timestamp("2024-08-02 23:00", tz="UTC")
    clk.t = pd.Timestamp("2024-08-03 02:10", tz="UTC"); out = svc.cycle()                     # 执行时隙 01:00（决策 00:30 之后首根开盘），02:00 收盘后记账
    assert out["accounts"]["A_BTC_SMA200"]["cost_equity"] != 10000.0 and svc.health()["ok"]
    # 心跳过期告警（外部监控语义）
    clk.t = pd.Timestamp("2024-08-03 03:40", tz="UTC"); assert "heartbeat age" in " ".join(svc.health()["reasons"])
    # 日志持久化与轮转
    logs = list((tmp_path / "svc" / "fw").glob("service.log*")); assert (tmp_path / "svc" / "fw" / "service.log").exists() and len(logs) >= 1
    assert all(v["ok"] for v in svc.runner.verify_all().values())


def test_restart_consistency_dedupe_no_negative_cash_and_isolation(tmp_path):
    bars = _synth(); times = pd.date_range("2024-08-01 05:00", "2024-09-16 23:00", freq="2h", tz="UTC")
    # 连续运行（同一实例）
    svcA, _ = _service(tmp_path, bars, None, [_FakeSource(bars)], name="cont"); svcA.feed.sources = []     # 行情仓已完整，无需来源
    for t in times:
        svcA.clock.t = t; svcA.cycle()
    # 每一轮都"意外退出并重启"（新实例从 state.json 恢复）
    for i, t in enumerate(times):
        svcB, _ = _service(tmp_path, bars, None, [], name="restart") if i == 0 else (ForwardService(cfg(), log_dir=tmp_path / "restart" / "fw", runner=ForwardRunner(cfg(), log_dir=tmp_path / "restart" / "fw", store=BarStore(root=tmp_path / "restart" / "bars_root", fallback_root=None)), feed=MarketDataService(cfg(), store=BarStore(root=tmp_path / "restart" / "bars_root", fallback_root=None), log_dir=tmp_path / "restart" / "feed", sources=[], seed_from=None), clock=_Clock(t)), None)
        svcB.clock.t = t; svcB.cycle()
    for n in ("A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"):
        sa = json.loads((tmp_path / "cont" / "fw" / n / "state.json").read_text()); sb = json.loads((tmp_path / "restart" / "fw" / n / "state.json").read_text())
        for k in ("cost", "ideal"):
            assert abs(sa["ledgers"][k]["cash"] - sb["ledgers"][k]["cash"]) < 1e-6 and sa["ledgers"][k]["lots"].keys() == sb["ledgers"][k]["lots"].keys()
        assert sa["processed_orders"] == sb["processed_orders"] and len(sa["processed_orders"]) == len(set(sa["processed_orders"]))      # 多次重启权益一致、无重复订单
        ev = [json.loads(l) for l in open(tmp_path / "restart" / "fw" / n / "events.jsonl")]
        fills = [e for e in ev if e["kind"] == "fill"]; assert len({f["order_id"] for f in fills}) == len(fills)
        navs = [e for e in ev if e["kind"] == "nav"]; assert all(l["cash"] >= -1e-9 and all(q >= 0 for q in l["holdings"].values()) for e in navs for l in e["ledgers"].values())
        assert all(e["ledgers"]["cost"]["liquidation_usdt"] <= e["ledgers"]["cost"]["equity_usdt"] + 1e-6 and abs(e["ledgers"]["ideal"]["liquidation_usdt"] - e["ledgers"]["ideal"]["equity_usdt"]) < 1e-6 for e in navs)
        # 日线收盘时序：信号时间 = 日线收盘；执行 K 线开盘严格晚于决策且 = exec_after；成交在该 K 线收盘后记账
        orders_by_id = {e["order_id"]: e for e in ev if e["kind"] == "order"}
        for e in fills:
            o = orders_by_id[e["order_id"]]; assert pd.Timestamp(o["decision_time"]) < pd.Timestamp(e["price_time"]) <= pd.Timestamp(e["execution_time"]) == pd.Timestamp(e["fill_recorded_at"]) and pd.Timestamp(e["execution_time"]) >= pd.Timestamp(o["exec_after"])
        for e in [x for x in ev if x["kind"] == "signal"]:
            assert pd.Timestamp(e["signal_ts"]).hour == 0 and pd.Timestamp(e["signal_generated_at"]) >= pd.Timestamp(e["signal_ts"])
    # 账户隔离：篡改 A 的事件链不影响 B；A 状态文件独立
    pA = tmp_path / "restart" / "fw" / "A_BTC_SMA200" / "events.jsonl"; lines = pA.read_text().splitlines(); rec = json.loads(lines[0]); rec["kind"] = "warmupX"; lines[0] = json.dumps(rec, ensure_ascii=False); pA.write_text("\n".join(lines) + "\n")
    v = ForwardRunner(cfg(), log_dir=tmp_path / "restart" / "fw", store=BarStore(root=tmp_path / "restart" / "bars_root", fallback_root=None)).verify_all()
    assert not v["A_BTC_SMA200"]["ok"] and v["B_modelB_state_machine"]["ok"] and v["REF_ETH_SMA200"]["ok"]
    # 期末清仓：含成本清算 = 现金 + Σ 数量 × 价 × (1−滑点)(1−费率)
    st = json.loads((tmp_path / "cont" / "fw" / "B_modelB_state_machine" / "state.json").read_text())
    from brando_pa.forward.accounts import ForwardAccount, account_specs
    acc = ForwardAccount("B_modelB_state_machine", account_specs(cfg())["B_modelB_state_machine"], cfg(), tmp_path / "cont" / "fw" / "B_modelB_state_machine", 10000, 10, 5)
    L = acc.ledger("cost"); px = {s: 100.0 for s in SYMS}
    from brando_pa.usdt.engine import liquidation_value
    assert abs(liquidation_value(L, px) - (L.cash + sum(L.qty(s) * 100.0 * (1 - 5e-4) * (1 - 1e-3) for s in SYMS))) < 1e-6
    # 每日报告
    from brando_pa.forward.report import daily_report
    rep = daily_report(tmp_path / "cont" / "fw", "2024-09-10")
    assert set(rep["accounts"]) == {"A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"} and all("diff_vs_others_usdt" in a["ledgers"]["cost"] for a in rep["accounts"].values())
    assert (tmp_path / "cont" / "fw" / "daily" / "2024-09-10.md").exists()


class _ShiftedSource(_FakeSource):
    """另一家"交易所"：价格系统性偏高 pct。"""
    name = "other_exchange"

    def __init__(self, bars, pct):
        super().__init__(bars); self.pct = pct

    def fetch(self, symbol, tf, start, end):
        df = super().fetch(symbol, tf, start, end).copy()
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * (1 + self.pct)
        return df


def test_source_switch_logging_and_deviation_gate(tmp_path):
    bars = _synth(); clk = _Clock("2024-08-01 05:00")
    good = _FakeSource(bars); other_small = _ShiftedSource(bars, 0.0005); other_big = _ShiftedSource(bars, 0.02)
    svc, store = _service(tmp_path, bars, pd.Timestamp("2024-07-31 00:00", tz="UTC"), [good, other_small], clock=clk)
    svc.cycle(); assert svc.feed._prev_source() == "fake_exchange"
    # 主来源断开 → 切到偏差 0.05% 的来源：记录 source_switch，不触发质量门控
    good.fail = 99; clk.t = pd.Timestamp("2024-08-02 00:30", tz="UTC"); out = svc.cycle()
    assert out["data"]["source"] == "other_exchange" and out["data"]["source_switch"]["from"] == "fake_exchange" and not out["data"]["quality_fault"]
    ev = [json.loads(l) for l in open(tmp_path / "svc" / "feed" / "data_events.jsonl")]; assert any(e["kind"] == "source_switch" for e in ev)
    sig = [json.loads(l) for l in open(tmp_path / "svc" / "fw" / "A_BTC_SMA200" / "events.jsonl") if json.loads(l)["kind"] == "signal"]
    assert sig and sig[-1]["market_source"]["BTCUSDT"] in ("other_exchange", "synthetic_history", "fake_exchange")          # 信号记录行情来源
    # 切到偏差 2% 的来源（重叠 K 线偏差 > 0.5%）：标记数据质量异常，信号记录为 missed_signal（data_quality_fault），不下单
    svc.feed.sources = [other_big]; clk.t = pd.Timestamp("2024-08-03 00:30", tz="UTC"); out = svc.cycle()
    assert out["data"]["quality_fault"] and all(v["fills"] == 0 for v in out["accounts"].values())
    stA = json.loads((tmp_path / "svc" / "fw" / "A_BTC_SMA200" / "state.json").read_text())
    assert all(pd.Timestamp(o["exec_after"]) >= pd.Timestamp("2024-08-03 00:00", tz="UTC") and o.get("deferred_reason") == "data_quality_fault" for o in stA["pending"])   # 挂单顺延，不回填
    evA = [json.loads(l) for l in open(tmp_path / "svc" / "fw" / "A_BTC_SMA200" / "events.jsonl")]
    assert any(e["kind"] == "missed_signal" and e["missed_reason"] == "data_quality_fault" and e["signal_ts"].startswith("2024-08-03") for e in evA)
    assert any(json.loads(l)["kind"] == "data_quality" for l in open(svc.alerts_path)) and "data quality fault" in svc.health()["reasons"]
    # 既有 K 线不被新来源覆盖
    h = store.load("BTCUSDT", "1h", allow_fallback=False); old = h[h["ts"] <= pd.Timestamp("2024-08-01 04:00", tz="UTC")]       # 切换前已入库的 K 线
    assert np.allclose(old["close"].to_numpy(), bars["BTCUSDT"]["1h"].set_index("ts").loc[old["ts"], "close"].to_numpy())
