"""第七阶段：分配器（比例和为 1、模型 B 满额、模型 C 现金储备硬上限、风险上限缩放、强制退出、最小再平衡金额、买入不超过现金）、
引擎现金/持仓约束与期末清算、时间对齐与无未来数据（截断一致）、前瞻模拟追加记录与状态恢复。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.usdt.allocator import PortfolioAllocator
from brando_pa.usdt.paper import UsdtPaper
from brando_pa.trendsys.paper import verify_chain

HAVE = Path("data_store/brando_pa/bars/BTCUSDT_1h.parquet").exists()
SYMS = ["BTCUSDT", "ETHUSDT"]


class R:
    def __init__(self, cap=1.0, force=False, state="NORMAL"):
        self.max_exposure, self.force_exit, self.state = cap, force, state


def test_allocator_weights_models_and_constraints():
    c = cfg()["usdt"]; px = {"BTCUSDT": 50000.0, "ETHUSDT": 2500.0}
    for m, both, single in (("A_equal_weight", 1.0, 0.5), ("B_full_single", 1.0, 1.0), ("C_cash_reserve", 0.8, 0.8)):
        al = PortfolioAllocator(c["models"][m], SYMS, c["allocator"])
        for tr in ({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": True, "ETHUSDT": False}, {"BTCUSDT": False, "ETHUSDT": True}, {"BTCUSDT": False, "ETHUSDT": False}):
            a = al.allocate(tr, {s: 0.0 for s in SYMS}, 10000.0, px, R())
            w = a.target_weights; assert abs(sum(w.values()) - 1) < 1e-12 and all(v >= 0 for v in w.values())
            tot = w["BTCUSDT"] + w["ETHUSDT"]
            if tr["BTCUSDT"] and tr["ETHUSDT"]:
                assert abs(tot - both) < 1e-12 and abs(w["BTCUSDT"] - w["ETHUSDT"]) < 1e-12
            elif tr["BTCUSDT"] or tr["ETHUSDT"]:
                assert abs(tot - single) < 1e-12
            else:
                assert tot == 0 and w["USDT"] == 1.0
            assert sum(o.get("amount", 0) for o in a.orders if o["side"] == "BUY") <= 10000.0 * (1 - c["allocator"]["cash_buffer"]) + 1e-9
    # 风险上限按比例缩放；强制退出 → 全部 USDT 且卖出全部持仓
    al = PortfolioAllocator(c["models"]["B_full_single"], SYMS, c["allocator"])
    a = al.allocate({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": 0.1, "ETHUSDT": 2.0}, 0.0, px, R(cap=0.5, state="RISK_REDUCED"))
    assert abs(a.target_weights["BTCUSDT"] - 0.25) < 1e-12 and abs(a.target_weights["USDT"] - 0.5) < 1e-12 and all(o["side"] == "SELL" for o in a.orders)
    a = al.allocate({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": 0.1, "ETHUSDT": 2.0}, 0.0, px, R(cap=0.0, force=True, state="EMERGENCY_HALT"))
    assert a.target_weights["USDT"] == 1.0 and sorted(o["symbol"] for o in a.orders) == SYMS and all(abs(o["qty"] - {"BTCUSDT": 0.1, "ETHUSDT": 2.0}[o["symbol"]]) < 1e-12 for o in a.orders)
    # 最小再平衡金额：微小偏差不下单；漂移 ≥ 10pp 才再平衡
    al = PortfolioAllocator(c["models"]["A_equal_weight"], SYMS, c["allocator"])
    al.allocate({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": 0.1, "ETHUSDT": 2.0}, 0.0, px, R())
    a = al.allocate({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": 0.1, "ETHUSDT": 2.0}, 0.0, {"BTCUSDT": 50100.0, "ETHUSDT": 2500.0}, R())
    assert not a.rebalance and a.orders == []
    a = al.allocate({"BTCUSDT": True, "ETHUSDT": True}, {"BTCUSDT": 0.1, "ETHUSDT": 2.0}, 0.0, {"BTCUSDT": 80000.0, "ETHUSDT": 2500.0}, R())
    assert a.rebalance and {o["side"] for o in a.orders} == {"SELL", "BUY"}
    # 买入不得超过卖出后的可用 USDT：模型 B 从 BTC 100% 切到 ETH 100%
    al = PortfolioAllocator(c["models"]["B_full_single"], SYMS, c["allocator"], 10, 5)
    a = al.allocate({"BTCUSDT": False, "ETHUSDT": True}, {"BTCUSDT": 0.2, "ETHUSDT": 0.0}, 0.0, px, R())
    sells = [o for o in a.orders if o["side"] == "SELL"]; buys = [o for o in a.orders if o["side"] == "BUY"]
    assert sells and buys and buys[0]["amount"] <= sells[0]["est_notional"] * (1 - 5 / 1e4) * (1 - 10 / 1e4) * (1 - c["allocator"]["cash_buffer"]) + 1e-9


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_engine_constraints_liquidation_and_no_lookahead():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.engine.context import MarketContext
    from brando_pa.usdt.engine import AllocationBacktester
    c = cfg(); ctxs = load_contexts(SYMS, "1h", c)
    for m in ("A_equal_weight", "B_full_single", "C_cash_reserve"):
        r = AllocationBacktester(m, "state_machine", c, "2023-01-01", "2024-12-31", 10, 5).run(ctxs); e = r.equity
        assert (e["cash"] >= -1e-6).all() and (e["qty_BTCUSDT"] >= 0).all() and (e["qty_ETHUSDT"] >= 0).all() and (e["exposure"] <= 1 + 1e-9).all()
        assert abs((e["w_BTCUSDT"] + e["w_ETHUSDT"] + e["w_USDT"] - 1).abs().max()) < 1e-9
        assert r.final["liquidation_usdt"] <= r.final["equity"] + 1e-6                       # 含成本清算 ≤ 权益
        if m == "C_cash_reserve":
            # 硬上限：任一日线收盘后的下一开盘已回到 ≤ 80%；逐根最大值允许日内漂移
            al = r.alloc_log; assert (al["w_cur_BTCUSDT"] + al["w_cur_ETHUSDT"] <= 0.8 + 0.12).all() and e["exposure"].max() < 0.95
        if m == "B_full_single":
            assert e["w_BTCUSDT"].max() > 0.9 or e["w_ETHUSDT"].max() > 0.9
        ft = pd.to_datetime(r.fills["ts"], utc=True); assert ((ft.dt.hour == 0) & (ft.dt.minute == 0)).all()     # 下一根 1h 开盘成交
    r0 = AllocationBacktester("A_equal_weight", "none", c, "2023-01-01", "2024-12-31").run(ctxs)
    assert abs(r0.final["liquidation_usdt"] - r0.final["equity"]) < 1e-6                     # 零成本清算 = 权益
    # 无未来数据：截断历史后重跑，截断点之前的成交完全相同
    cut = pd.Timestamp("2024-06-30", tz="UTC"); tr = {}
    for s in SYMS:
        bars = {tf: df[pd.to_datetime(df["ts"], utc=True) < cut].reset_index(drop=True) for tf, df in ctxs[s].bars.items()}
        tr[s] = MarketContext(s, bars, "1h", c)
    rt = AllocationBacktester("B_full_single", "state_machine", c, "2023-01-01", None).run(tr)
    rf = AllocationBacktester("B_full_single", "state_machine", c, "2023-01-01", None).run(ctxs)
    a = rt.fills[["ts", "symbol", "side", "qty"]].reset_index(drop=True); b = rf.fills[pd.to_datetime(rf.fills["ts"], utc=True) < cut - pd.Timedelta(days=1)][["ts", "symbol", "side", "qty"]].reset_index(drop=True)
    n = len(b); assert n > 5 and a.head(n).astype(str).equals(b.astype(str))


class _FakeStore:
    def __init__(self, bars):
        self.bars = bars

    def load(self, symbol, tf, allow_fallback=True):
        return self.bars[symbol][tf].copy()


def _bars(n_days=260, seed=0):
    out = {}
    for k, s in enumerate(SYMS):
        d = np.concatenate([np.linspace(100, 160, 230), np.linspace(160, 90 if k == 0 else 170, n_days - 230)]) * (1 if k == 0 else 0.05)
        ts1h = pd.date_range("2024-01-01", periods=n_days * 24, freq="1h", tz="UTC"); p = np.repeat(d, 24) * (1 + 0.001 * np.sin(np.arange(n_days * 24) + k))
        h = pd.DataFrame({"ts": ts1h, "open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1.0})
        d1 = h.set_index("ts").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).reset_index()
        out[s] = {"1d": d1, "1h": h}
    return out


def test_usdt_paper_chain_recovery_and_weights(tmp_path):
    c = cfg(); store = _FakeStore(_bars())
    p = UsdtPaper(c, log_dir=tmp_path, model="B_full_single", risk_mode="state_machine", store=store)
    out = p.replay("2024-08-01", "2024-08-15"); assert out["steps"] == 15 and all(v["ok"] for v in out["chains"].values())
    nav = [json.loads(l) for l in open(tmp_path / "nav.jsonl")]
    assert nav and all(abs(sum(n["weights"].values()) + n["cash"] / n["equity"] - 1) < 1e-6 and n["cash"] >= 0 for n in nav)
    assert abs(nav[-1]["weights"]["BTCUSDT"] - 0.5) < 0.02 and abs(nav[-1]["weights"]["ETHUSDT"] - 0.5) < 0.02      # 两趋势有效 → 50/50
    p2 = UsdtPaper(c, log_dir=tmp_path, model="B_full_single", risk_mode="state_machine", store=store)
    assert p2.state["n_runs"] == 15 and p2.alloc.last_target is not None
    p2.replay("2024-08-16", "2024-09-17")
    nav = [json.loads(l) for l in open(tmp_path / "nav.jsonl")]
    assert nav[-1]["weights"]["ETHUSDT"] > 0.9 and nav[-1]["weights"]["BTCUSDT"] < 0.01                                 # BTC 趋势失效 → ETH 100%
    assert all(v["ok"] for v in p2.verify_all().values())
    with pytest.raises(RuntimeError):
        UsdtPaper(c, log_dir=tmp_path / "x", store=store, live=True)
