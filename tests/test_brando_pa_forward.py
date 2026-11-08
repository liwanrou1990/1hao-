"""第八阶段：K 线收盘确认与规范化、数据断线补齐与来源回退、历史预热与前瞻记录分离、重启状态恢复、重复订单去重、风险状态恢复、账户 USDT 计算、
BTC/ETH 组合净值、前瞻日志不可回写、数据过期禁止开仓、跨周期一致性。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.datafeed import MarketDataService, consistency_1h_vs_1d, normalize
from brando_pa.trendsys.paper import verify_chain

SYMS = ["BTCUSDT", "ETHUSDT"]


def _synth(n_days=260, start="2024-01-01", eth_turn=170):
    out = {}
    for k, s in enumerate(SYMS):
        d = np.concatenate([np.linspace(100, 160, 230), np.linspace(160, 90 if k == 0 else eth_turn, n_days - 230)]) * (1 if k == 0 else 0.05)
        ts = pd.date_range(start, periods=n_days * 24, freq="1h", tz="UTC"); p = np.repeat(d, 24) * (1 + 0.001 * np.sin(np.arange(n_days * 24) + k))
        h = pd.DataFrame({"ts": ts, "open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1.0})
        d1 = h.set_index("ts").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).reset_index()
        out[s] = {"1h": h, "1d": d1}
    return out


def _store(tmp, bars, upto=None, source="synthetic_history"):
    st = BarStore(root=tmp / "bars_root", fallback_root=None)
    for s, bb in bars.items():
        for tf, df in bb.items():
            d = df if upto is None else df[df["ts"] + pd.Timedelta(seconds=3600 if tf == "1h" else 86400) <= upto]
            st.upsert(d.assign(symbol=s, tf=tf, quote_volume=np.nan, source=source, ingested_at=pd.Timestamp("2024-01-01", tz="UTC")))
    return st


class _FakeSource:
    """模拟交易所：可控失败次数、乱序/重复/未收盘返回。"""
    name = "fake_exchange"; host = "fake"

    def __init__(self, bars, fail_first=0):
        self.bars, self.fail, self.calls = bars, fail_first, 0

    def fetch(self, symbol, tf, start, end):
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1; raise ConnectionError("simulated disconnect")
        df = self.bars[symbol][tf]; df = df[(df["ts"] >= start)]
        df = pd.concat([df.iloc[::-1], df.tail(2)])                       # 乱序 + 重复（最后两根也可能未收盘）
        return df[["ts", "open", "high", "low", "close", "volume"]]


def test_normalize_closed_only_dedupe_order_and_consistency():
    bars = _synth(); now = pd.Timestamp("2024-06-01 12:30", tz="UTC")
    raw = pd.concat([bars["BTCUSDT"]["1h"].iloc[::-1], bars["BTCUSDT"]["1h"].tail(3)])
    d, rep = normalize(raw, "1h", now)
    assert rep["duplicates"] == 3 and rep["out_of_order"] > 0 and (d["ts"].diff().dropna() > pd.Timedelta(0)).all()
    assert (d["ts"] + pd.Timedelta(hours=1) <= now).all() and d["ts"].iloc[-1] == pd.Timestamp("2024-06-01 11:00", tz="UTC")   # 12:00 那根未收盘被剔除
    d1, _ = normalize(bars["BTCUSDT"]["1d"], "1d", now)
    assert d1["ts"].iloc[-1] == pd.Timestamp("2024-05-31", tz="UTC")
    c = consistency_1h_vs_1d(d, d1); assert c["ok"] and c["checked_days"] == 10
    bad = d1.copy(); bad.loc[bad.index[-1], "close"] *= 1.05
    assert consistency_1h_vs_1d(d, bad)["ok"] is False


def test_datafeed_reconnect_backfill_and_source_fallback(tmp_path):
    bars = _synth(); t_old = pd.Timestamp("2024-05-20", tz="UTC"); now = pd.Timestamp("2024-06-01 12:30", tz="UTC")
    store = _store(tmp_path, bars, upto=t_old)
    src = _FakeSource(bars, fail_first=2)
    svc = MarketDataService(cfg(), store=store, log_dir=tmp_path / "log", sources=[src], max_retries=3, seed_from=None)
    svc.max_retries = 3
    import brando_pa.forward.datafeed as df_mod
    df_mod.time.sleep = lambda *_: None
    st = svc.update(SYMS, ["1h", "1d"], now=now)
    assert st.ok and st.source == "fake_exchange" and not st.stale
    h = store.load("BTCUSDT", "1h", allow_fallback=False)
    assert h["ts"].iloc[-1] == pd.Timestamp("2024-06-01 11:00", tz="UTC") and not h["ts"].duplicated().any() and (h["ts"].diff().dropna() == pd.Timedelta(hours=1)).all()   # 断线后补齐，无缺口
    assert set(h["source"].unique()) == {"synthetic_history", "fake_exchange"} and st.consistency["BTCUSDT"]["ok"]
    assert json.loads((tmp_path / "log" / "forward_data_status.json").read_text())["last_success_source"] == "fake_exchange"
    # 全部来源失败 → ok=False，状态保留上次成功时间，不伪造数据
    svc2 = MarketDataService(cfg(), store=store, log_dir=tmp_path / "log", sources=[_FakeSource(bars, fail_first=99)], max_retries=2, seed_from=None)
    st2 = svc2.update(SYMS, ["1h", "1d"], now=now + pd.Timedelta(hours=5))
    assert not st2.ok and store.load("BTCUSDT", "1h", allow_fallback=False)["ts"].iloc[-1] == pd.Timestamp("2024-06-01 11:00", tz="UTC")
    assert json.loads((tmp_path / "log" / "forward_data_status.json").read_text())["last_success_source"] == "fake_exchange"
    assert verify_chain(tmp_path / "log" / "data_events.jsonl")["ok"]


def test_forward_accounts_warmup_separation_restart_dedupe_and_usdt(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars)
    c = cfg(); c["paths"] = dict(c["paths"])
    start = pd.Timestamp("2024-08-01 05:00", tz="UTC")
    r = ForwardRunner(c, log_dir=tmp_path / "fw", store=store)
    out = r.run_once(now=start)
    for n, a in out["accounts"].items():
        assert a["forward_start_utc"] == str(start) and a["last_daily_ts"] is None and a["nav"]["cost"]["equity_usdt"] == 10000.0        # 预热不产生前瞻信号
        ev = [json.loads(l) for l in open(tmp_path / "fw" / n / "events.jsonl")]; assert ev[0]["kind"] == "warmup" and not [e for e in ev if e["kind"] in ("signal", "order", "fill")]
    # 次日 00:30：日线收盘 > forward_start → 产生信号与订单；00:00 开盘的 1h 尚未收盘 → 挂起（只用已收盘 K 线）
    out = r.run_once(now=pd.Timestamp("2024-08-02 00:30", tz="UTC"))
    a = out["accounts"]["A_BTC_SMA200"]; assert a["pending"] and a["nav"]["cost"]["holdings"]["BTCUSDT"] == 0
    n_pending = len(a["pending"]); n_orders = sum(1 for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl") if json.loads(l)["kind"] == "order")
    out = r.run_once(now=pd.Timestamp("2024-08-02 00:35", tz="UTC"))                      # 同一信号日重复运行：不产生重复订单
    assert len(out["accounts"]["A_BTC_SMA200"]["pending"]) == n_pending and sum(1 for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl") if json.loads(l)["kind"] == "order") == n_orders
    # exec_after = 01:00；引擎 02:10 处理 → 以当时可观察的最新已收盘收盘价（02:00）成交，执行时间 02:10（late）；两本账（含成本更低）
    out = r.run_once(now=pd.Timestamp("2024-08-02 02:10", tz="UTC"))
    a = out["accounts"]["A_BTC_SMA200"]; b = out["accounts"]["B_modelB_state_machine"]
    assert a["nav"]["ideal"]["holdings"]["BTCUSDT"] > 0 and a["nav"]["cost"]["holdings"]["BTCUSDT"] > 0 and a["nav"]["cost"]["equity_usdt"] < a["nav"]["ideal"]["equity_usdt"] and not a["pending"]
    assert abs(b["nav"]["ideal"]["weights"]["BTCUSDT"] - 0.5) < 0.02 and abs(b["nav"]["ideal"]["weights"]["ETHUSDT"] - 0.5) < 0.02
    fills_a = [json.loads(l) for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl") if json.loads(l)["kind"] == "fill"]
    assert fills_a and all(f["execution_time"] == "2024-08-02 02:10:00+00:00" and f["price_time"] == "2024-08-02 02:00:00+00:00" and pd.Timestamp(f["price_time"]) > pd.Timestamp(f["decision_time"]) for f in fills_a)
    for n, v in out["accounts"].items():
        for k, nav in v["nav"].items():
            assert nav["cash"] >= 0 and all(q >= 0 for q in nav["holdings"].values())
    n_fills = len(fills_a)
    r.run_once(now=pd.Timestamp("2024-08-02 02:15", tz="UTC"))                            # 重复运行：不产生重复成交
    assert sum(1 for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl") if json.loads(l)["kind"] == "fill") == n_fills
    # 重启恢复：新实例状态与账本一致
    st_before = json.loads((tmp_path / "fw" / "B_modelB_state_machine" / "state.json").read_text())
    r2 = ForwardRunner(c, log_dir=tmp_path / "fw", store=store)
    assert r2.accounts["B_modelB_state_machine"].state["ledgers"] == st_before["ledgers"] and r2.accounts["B_modelB_state_machine"].state["processed_orders"] == st_before["processed_orders"]
    # 服务"离线"一个多月后恢复：期间的日线信号只能标记 missed（不补成交）；当日收盘（宽限期内）正常执行
    out = r2.run_once(now=pd.Timestamp("2024-09-16 00:30", tz="UTC"))
    evA = [json.loads(l) for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl")]
    missed = [e for e in evA if e["kind"] == "missed_signal"]; assert len(missed) >= 40 and all(not e["executed"] and "late" in e["missed_reason"] for e in missed)
    assert not [e for e in evA if e["kind"] == "fill" and pd.Timestamp(e["fill_ts"]) > pd.Timestamp("2024-08-02 03:00", tz="UTC")]       # 离线期间没有任何回填成交
    live = [e for e in evA if e["kind"] == "signal" and e["signal_ts"].startswith("2024-09-16")]; assert live and live[0]["executed"]
    out = r2.run_once(now=pd.Timestamp("2024-09-16 02:10", tz="UTC"))                     # 01:00 那根 1h 收盘 → 成交：A 卖出 BTC，B 切到 ETH（状态机限仓）
    a = out["accounts"]["A_BTC_SMA200"]["nav"]["cost"]; b = out["accounts"]["B_modelB_state_machine"]["nav"]["cost"]
    assert a["holdings"]["BTCUSDT"] == 0 and a["cash"] > 0 and b["holdings"]["BTCUSDT"] == 0
    assert out["accounts"]["B_modelB_state_machine"]["risk_state"] == "RISK_REDUCED" and abs(b["weights"]["ETHUSDT"] - 0.5) < 0.03     # 离线期间回撤 > 10% → 状态机限仓 50%（ETH 目标 100% × 0.5）
    px = {s: float(store.load(s, "1h")[store.load(s, "1h")["ts"] + pd.Timedelta(hours=1) <= pd.Timestamp("2024-09-16 02:10", tz="UTC")]["close"].iloc[-1]) for s in SYMS}
    assert abs(b["equity_usdt"] - (b["cash"] + sum(b["holdings"][s] * px[s] for s in SYMS))) < 1e-6
    assert all(v["ok"] for v in r2.verify_all().values())
    # 前瞻日志不可回写：篡改/删除任一行 → 哈希链失败
    p = tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl"; lines = p.read_text().splitlines(); rec = json.loads(lines[2]); rec["kind"] = "signal_x"; lines[2] = json.dumps(rec, ensure_ascii=False); p.write_text("\n".join(lines) + "\n")
    assert not verify_chain(p)["ok"]


def test_forward_stale_data_blocks_new_buys_and_risk_recovery(tmp_path):
    bars = _synth(); store = _store(tmp_path, bars, upto=pd.Timestamp("2024-08-02 00:00", tz="UTC"))
    r = ForwardRunner(cfg(), log_dir=tmp_path / "fw", store=store)
    r.run_once(now=pd.Timestamp("2024-08-01 05:00", tz="UTC"))
    out = r.run_once(now=pd.Timestamp("2024-08-02 03:30", tz="UTC"))          # 1h 数据只到 00:00 收盘 → 落后 3.5 小时 > 3 根 → 过期（仍在信号宽限期内）
    assert not out["data_check"]["ok"] and any("过期" in x for x in out["data_check"]["issues"])
    for n, v in out["accounts"].items():
        assert v["nav"]["cost"]["exposure"] == 0.0 and not v["fills"]       # 数据过期：禁止开仓
    ev = [json.loads(l) for l in open(tmp_path / "fw" / "A_BTC_SMA200" / "events.jsonl")]
    sig = [e for e in ev if e["kind"] == "signal"]; assert sig and sig[-1]["data_fault"] and sig[-1]["max_exposure"] == 0.0
    # 风险状态机：紧急停机 ≠ 普通冷却；恢复逻辑可测（复用第六阶段状态机）
    from brando_pa.trendsys.risk_layer import CircuitBreaker, COOLDOWN, EMERGENCY_HALT, NORMAL, RECOVERY
    cb = CircuitBreaker(cfg()["trendsys"]["risk"]["state_machine"]); t = pd.Timestamp("2024-01-01", tz="UTC")
    cb.evaluate(t, 10000); d = cb.evaluate(t + pd.Timedelta(days=1), 7900); assert d.state == EMERGENCY_HALT and d.force_exit
    d = cb.evaluate(t + pd.Timedelta(days=2), 7900); assert d.state == COOLDOWN and not d.force_exit and d.max_exposure == 0.0
    snap = cb.snapshot(); cb2 = CircuitBreaker(cfg()["trendsys"]["risk"]["state_machine"]); cb2.restore(snap)          # 重启后恢复状态机
    assert cb2.state == COOLDOWN and cb2.evaluate(t + pd.Timedelta(days=32), 7900).state == RECOVERY and cb2.evaluate(t + pd.Timedelta(days=92), 8000).state == NORMAL
