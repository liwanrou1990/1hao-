"""第六阶段：趋势层确认语义与截断一致性、熔断状态机（有限冷静/恢复、再触发、计数、崩盘、数据失败）、原规则永久停机、三层引擎复现第五阶段 SMA200 基准与现货约束、前瞻模拟追加记录与状态恢复、卖出分类。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.trendsys.frequency import analyze_sells
from brando_pa.trendsys.paper import TrendSysPaper, verify_chain
from brando_pa.trendsys.risk_layer import COOLDOWN, EMERGENCY_HALT, NORMAL, RECOVERY, RISK_REDUCED, CircuitBreaker, OriginalPermanentHalt
from brando_pa.trendsys.trend_layer import trend_signal

HAVE = Path("data_store/brando_pa/bars/BTCUSDT_1h.parquet").exists()


def _d1(prices, start="2020-01-01"):
    ts = pd.date_range(start, periods=len(prices), freq="1D", tz="UTC"); p = np.asarray(prices, float)
    return pd.DataFrame({"ts": ts, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1.0})


def test_trend_layer_confirmation_and_truncation():
    base = np.linspace(100, 200, 260).tolist()
    dip2 = base + [50, 50] + [300] * 10                       # 2 天跌破 SMA200 后回到上方
    a = trend_signal(_d1(dip2), 200, 1); b = trend_signal(_d1(dip2), 200, 3)
    assert (~a["on"].iloc[:199]).all() and a["on"].iloc[259] and b["on"].iloc[259]
    assert not a["on"].iloc[260] and not a["on"].iloc[261]     # A：首个收盘跌破即 OFF
    assert b["on"].iloc[260] and b["on"].iloc[261]             # B：2 天不够 3 个确认 → 仍 ON
    dip3 = base + [50, 50, 50] + [300] * 10
    b3 = trend_signal(_d1(dip3), 200, 3)
    assert b3["on"].iloc[261] and not b3["on"].iloc[262]        # 第 3 个收盘才切换
    assert not b3["on"].iloc[263] and not b3["on"].iloc[264] and b3["on"].iloc[265]   # 回到上方也需 3 个收盘
    full = trend_signal(_d1(dip3), 200, 3); cut = trend_signal(_d1(dip3[:-5]), 200, 3)
    assert (full["on"].iloc[:len(cut)].to_numpy() == cut["on"].to_numpy()).all()      # 截断不改变此前信号


def test_circuit_breaker_is_finite_and_resets_hwm():
    p = cfg()["trendsys"]["risk"]["state_machine"]; cb = CircuitBreaker(p); t = pd.Timestamp("2024-01-01", tz="UTC")
    d = cb.evaluate(t, 10000); assert d.state == NORMAL and d.max_exposure == 1.0
    d = cb.evaluate(t + pd.Timedelta(days=1), 8900); assert d.state == RISK_REDUCED and d.max_exposure == 0.5 and d.reduce_to == 0.5 and d.changed
    d = cb.evaluate(t + pd.Timedelta(days=2), 9600); assert d.state == NORMAL                      # dd −4% > −5% → 恢复
    d = cb.evaluate(t + pd.Timedelta(days=3), 7900); assert d.state == EMERGENCY_HALT and d.force_exit and d.max_exposure == 0.0 and cb.halt_count == 1
    d = cb.evaluate(t + pd.Timedelta(days=4), 7900); assert d.state == COOLDOWN and d.max_exposure == 0.0
    d = cb.evaluate(t + pd.Timedelta(days=4 + 29), 7900); assert d.state == COOLDOWN                # 冷静期 30 日未满
    d = cb.evaluate(t + pd.Timedelta(days=4 + 30), 7900); assert d.state == RECOVERY and d.max_exposure == 0.5 and cb.hwm_ref == 7900   # 高水位重置
    d = cb.evaluate(t + pd.Timedelta(days=4 + 30 + 60), 8000); assert d.state == NORMAL and d.max_exposure == 1.0   # 恢复期满 → NORMAL（不是永久禁买）
    # 恢复期内再触发：相对恢复期高点回撤 ≥ 10% → 再次熔断，冷静期翻倍为 60 日
    cb2 = CircuitBreaker(p); t2 = pd.Timestamp("2025-01-01", tz="UTC")
    cb2.evaluate(t2, 10000); cb2.evaluate(t2 + pd.Timedelta(days=1), 7900); cb2.evaluate(t2 + pd.Timedelta(days=2), 7900); cb2.evaluate(t2 + pd.Timedelta(days=32), 7900)
    assert cb2.state == RECOVERY
    cb2.evaluate(t2 + pd.Timedelta(days=33), 8500); d = cb2.evaluate(t2 + pd.Timedelta(days=34), 7600)
    assert d.state == EMERGENCY_HALT and cb2.halt_count == 2
    cb2.evaluate(t2 + pd.Timedelta(days=35), 7600); assert cb2.state == COOLDOWN and (cb2.cooldown_until - (t2 + pd.Timedelta(days=35))).days == 60
    # 崩盘：空仓不触发，持仓触发；数据失败：禁买但不改状态
    cb3 = CircuitBreaker(p); cb3.evaluate(t, 10000)
    assert cb3.evaluate(t + pd.Timedelta(days=1), 10000, daily_drop=-0.2, exposure=0.0).state == NORMAL
    assert cb3.evaluate(t + pd.Timedelta(days=2), 10000, daily_drop=-0.2, exposure=1.0).state == EMERGENCY_HALT
    cb4 = CircuitBreaker(p); cb4.evaluate(t, 10000); cb4.set_data_fault(True, "stale"); d = cb4.evaluate(t + pd.Timedelta(days=1), 10000)
    assert d.state == NORMAL and d.max_exposure == 0.0 and d.data_fault and not d.force_exit


def test_original_rule_is_permanent_when_flat():
    o = OriginalPermanentHalt({"halt_dd": 0.20, "resume_dd": 0.10}); t = pd.Timestamp("2024-01-01", tz="UTC")
    o.evaluate(t, 10000); d = o.evaluate(t + pd.Timedelta(days=1), 7900); assert d.max_exposure == 0.0
    for k in range(2, 400):
        d = o.evaluate(t + pd.Timedelta(days=k), 7900)            # 空仓权益不变 → 回撤永不收窄
    assert d.max_exposure == 0.0 and o.halted


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_engine_reproduces_phase5_sma200_and_keeps_spot_constraints():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.trend.engine import sma200_benchmark
    from brando_pa.trendsys.engine import TrendSystemBacktester
    c = cfg(); ctxs = load_contexts(["BTCUSDT", "ETHUSDT"], "1h", c)
    eq5, f5 = sma200_benchmark(ctxs["BTCUSDT"], "2023-01-01", "2024-12-31", 10000)
    r = TrendSystemBacktester("A_sma200", "none", c, ["BTCUSDT"], "1h", "2023-01-01", "2024-12-31").run(ctxs)
    assert len(eq5) == len(r.equity) and np.allclose(eq5["equity"].to_numpy(), r.equity["equity"].to_numpy(), rtol=1e-12)
    assert int((f5["side"] == "BUY").sum()) == int((r.fills["side"] == "BUY").sum())
    for mode in ("state_machine", "original_permanent"):
        rr = TrendSystemBacktester("B_sma200_confirm3", mode, c, ["BTCUSDT", "ETHUSDT"], "1h", "2023-01-01", "2024-12-31").run(ctxs)
        e = rr.equity
        assert (e["cash"] >= -1e-6).all() and (e["exposure"] <= 1 + 1e-9).all() and (e["qty_BTCUSDT"] >= 0).all() and (e["qty_ETHUSDT"] >= 0).all()
        assert set(rr.trades["exit_reason"]) <= {"trend_exit", "emergency_halt"} | {x for x in rr.trades["exit_reason"] if x.startswith("risk_reduce_to")}
        # 成交都在日线收盘后的首根 1h 开盘
        ft = pd.to_datetime(rr.fills["ts"], utc=True); assert ((ft.dt.hour == 0) & (ft.dt.minute == 0)).all()
    # 状态机：任何禁买段都在 91 日内结束
    rs = TrendSystemBacktester("A_sma200", "state_machine", c, ["BTCUSDT"], "1h", "2022-04-01", None).run(ctxs)
    cap0 = (rs.equity["cap"] <= 0).to_numpy(); run = longest = 0
    for v in cap0:
        run = run + 1 if v else 0; longest = max(longest, run)
    assert longest <= 91 * 24 + 24


class _FakeStore:
    def __init__(self, bars):
        self.bars = bars

    def load(self, symbol, tf, allow_fallback=True):
        return self.bars[symbol][tf].copy()


def _synthetic_bars(n_days=260):
    d = np.concatenate([np.linspace(100, 160, 230), np.linspace(160, 90, n_days - 230)])
    ts1h = pd.date_range("2024-01-01", periods=n_days * 24, freq="1h", tz="UTC"); p1h = np.repeat(d, 24) * (1 + 0.001 * np.sin(np.arange(n_days * 24)))
    h = pd.DataFrame({"ts": ts1h, "open": p1h, "high": p1h * 1.001, "low": p1h * 0.999, "close": p1h, "volume": 1.0})
    d1 = h.set_index("ts").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).reset_index()
    return {"BTCUSDT": {"1d": d1, "1h": h}}


def test_paper_append_only_chain_and_state_recovery(tmp_path):
    c = cfg(); store = _FakeStore(_synthetic_bars())
    p = TrendSysPaper(c, log_dir=tmp_path, strategy="A_sma200", risk_mode="state_machine", symbols=["BTCUSDT"], store=store)
    with pytest.raises(RuntimeError):
        TrendSysPaper(c, log_dir=tmp_path / "x", symbols=["BTCUSDT"], store=store, live=True)
    out = p.replay("2024-08-01", "2024-08-20")                 # 第 213 天起：SMA200 已形成且价格在上方 → 买入；挂单在下一日补成交
    assert out["steps"] == 20 and all(v["ok"] for v in out["chains"].values())
    fills = [json.loads(l) for l in open(tmp_path / "fills.jsonl")]
    assert fills and fills[0]["side"] == "BUY" and fills[0]["fill_ts"].startswith("2024-08-01 00:00") and fills[0]["recorded_at"] > fills[0]["fill_ts"]   # 成交在日线收盘后的首根 1h 开盘，记录在其后
    st = json.loads((tmp_path / "state.json").read_text()); assert st["holdings"]["BTCUSDT"] > 0 and st["cash"] >= 0
    # 状态恢复：新实例从 state.json 继续，继续重放到下跌段 → 趋势退出卖出；权益连续
    p2 = TrendSysPaper(c, log_dir=tmp_path, strategy="A_sma200", risk_mode="state_machine", symbols=["BTCUSDT"], store=store)
    assert p2.state["holdings"]["BTCUSDT"] == st["holdings"]["BTCUSDT"] and p2.state["n_runs"] == 20
    out2 = p2.replay("2024-08-21", "2024-09-17")
    fills = [json.loads(l) for l in open(tmp_path / "fills.jsonl")]
    assert any(f["side"] == "SELL" for f in fills)
    nav = [json.loads(l) for l in open(tmp_path / "nav.jsonl")]; assert len(nav) >= 40 and all(n["cash"] >= 0 for n in nav)
    # 篡改一行 → 哈希链失败
    lines = open(tmp_path / "signals.jsonl").read().splitlines(); rec = json.loads(lines[3]); rec["trend_on"] = not rec["trend_on"]; lines[3] = json.dumps(rec, ensure_ascii=False)
    (tmp_path / "signals.jsonl").write_text("\n".join(lines) + "\n")
    assert not verify_chain(tmp_path / "signals.jsonl")["ok"]
    # 删除一行同样失败
    (tmp_path / "nav.jsonl").write_text("\n".join(open(tmp_path / "nav.jsonl").read().splitlines()[1:]) + "\n")
    assert not verify_chain(tmp_path / "nav.jsonl")["ok"]


def test_sell_classification():
    ts = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    px = pd.Series(np.r_[np.full(50, 100.0), np.linspace(100, 80, 50), np.full(50, 80.0), np.linspace(80, 120, 50)], index=ts)
    fills = pd.DataFrame([{"ts": ts[0], "symbol": "X", "side": "BUY", "qty": 1, "price": 100.0, "reason": ""}, {"ts": ts[49], "symbol": "X", "side": "SELL", "qty": 1, "price": 100.0, "reason": "trend_exit", "net_pnl": 0.0},
                          {"ts": ts[150], "symbol": "X", "side": "BUY", "qty": 1, "price": 80.0, "reason": ""}, {"ts": ts[160], "symbol": "X", "side": "SELL", "qty": 1, "price": 88.0, "reason": "trend_exit", "net_pnl": 8.0}, {"ts": ts[170], "symbol": "X", "side": "BUY", "qty": 1, "price": 96.0, "reason": ""}])
    s = analyze_sells(fills, {"X": px})
    assert list(s["category"]) == ["protective", "whipsaw"] and s["rebought"].all() and s["rebuy_vs_sell"].iloc[1] > 0
