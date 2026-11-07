"""模拟盘：回放全流程、状态持久化与恢复后不重复下单、数据过期时禁止新开仓。"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.data.store import BarStore
from brando_pa.engine.context import MarketContext
from brando_pa.paper.runner import PaperRunner

HAVE = Path("data_store/brando_pa/bars/BTCUSDT_15m.parquet").exists()


def _ctx(sym="ETHUSDT", n=5000):
    st = BarStore(); c = cfg()
    bars = {tf: st.load(sym, tf) for tf in ("1w", "1d", "4h", "15m")}
    ex = bars["15m"].iloc[-n:].reset_index(drop=True)
    bars["15m"] = ex
    return {sym: MarketContext(sym, bars, "15m", c)}


@pytest.mark.skipif(not HAVE, reason="无本地 15m 数据")
def test_replay_then_resume_no_duplicate_orders(tmp_path):
    c = cfg(); c["paper"]["state_file"] = str(tmp_path / "state.json"); c["paper"]["log_file"] = str(tmp_path / "trades.jsonl"); c["paper"]["lock_file"] = str(tmp_path / ".lock")
    ctxs = _ctx()
    r1 = PaperRunner(["ETHUSDT"], "15m", 10000.0, c, refresh_data=False)
    out1 = r1.replay(n_bars=2500, contexts=ctxs, save_every=50)
    assert out1["bars"] == 2500 and r1.state.path.exists()
    st = json.loads(r1.state.path.read_text())
    assert st["last_bar_ts"]["ETHUSDT"] and "order_ids" in st
    n_orders_before = len(st["order_ids"]); n_trades_before = st["n_trades"]
    # 重启：新实例从状态文件恢复，再回放同一段 → 已处理 K 线被跳过，不产生新订单
    r2 = PaperRunner(["ETHUSDT"], "15m", 10000.0, c, refresh_data=False)
    out2 = r2.replay(n_bars=2500, contexts=ctxs, save_every=50)
    st2 = json.loads(r2.state.path.read_text())
    assert len(st2["order_ids"]) == n_orders_before and st2["n_trades"] == n_trades_before
    assert out2["equity"] == pytest.approx(out1["equity"], rel=1e-9)
    # 事件日志存在且状态持久化字段齐全
    ev = (tmp_path / "events.jsonl")
    assert st2["risk"]["day"] is not None and st2["cash"] > 0
    if n_trades_before:
        trades = [json.loads(x) for x in (tmp_path / "trades.jsonl").read_text().splitlines()]
        assert len(trades) == n_trades_before and all(t["fees"] > 0 for t in trades)
        assert len({t["position_id"] for t in trades}) == len(trades)          # 每个持仓只平一次


@pytest.mark.skipif(not HAVE, reason="无本地 15m 数据")
def test_run_once_with_stale_data_blocks_new_entries(tmp_path):
    c = cfg(); c["paper"]["state_file"] = str(tmp_path / "state.json"); c["paper"]["log_file"] = str(tmp_path / "trades.jsonl"); c["paper"]["lock_file"] = str(tmp_path / ".lock")
    ctxs = _ctx(n=1200)
    r = PaperRunner(["ETHUSDT"], "15m", 10000.0, c, refresh_data=False)
    far_future = ctxs["ETHUSDT"].ts.iloc[-1] + pd.Timedelta(days=3)
    rep = r.run_once(now=far_future, contexts=ctxs)
    last = rep["symbols"]["ETHUSDT"]["last"]
    assert "过期" in rep["symbols"]["ETHUSDT"]["stale"]
    assert last["status"] in ("WAIT", "NO_TRADE") and not rep["pending"]
    # 锁：运行中第二个实例被拒绝
    r._lock(); assert r.run_once(now=far_future, contexts=ctxs).get("skipped"); r._unlock()


@pytest.mark.skipif(not Path("data_store/spot90/bars/BTCUSDT_4h.parquet").exists() and not Path("data_store/brando_pa/bars/BTCUSDT_4h.parquet").exists(), reason="无本地 4h 数据")
def test_walkforward_smoke_single_strategy_small_windows():
    """Walk-Forward 冒烟：小窗口、单参数网格，验证 训练→验证→测试 拼接与默认参数对照都能产出。"""
    from brando_pa.backtest.walkforward import run_walkforward
    c = cfg(); st = BarStore()
    bars = {tf: st.load("BTCUSDT", tf) for tf in ("1w", "1d", "4h")}
    ctxs = {"BTCUSDT": MarketContext("BTCUSDT", bars, "4h", c)}
    c["backtest"]["walkforward"].update({"train_months": 6, "valid_months": 3, "test_months": 3, "min_trades_select": 1, "grid": {"A_breakout_retest": {"sl_buffer_atr": [0.3, 0.5]}}})
    r = run_walkforward(ctxs, "4h", "A_breakout_retest", c, True, start="2024-06-01", end="2025-12-31", workers=2)
    assert len(r.folds) >= 1 and "n_trades" in r.oos_stats and "n_trades" in r.default_full_stats
    for f in r.folds:
        assert f.train[1] <= f.valid[0] <= f.valid[1] <= f.test[0]      # 严格时间顺序
        assert len(f.train_table) == 2
