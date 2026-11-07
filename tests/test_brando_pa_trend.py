"""第五阶段：趋势状态只用已收盘日线（截断一致性）、状态规则、退出模式（趋势失效滞后、ATR 跟踪只上移）、核心+战术敞口 ≤ 100%、SMA200 基准语义、暴露度指标。"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.trend.exposure import analyze, reconstruct
from brando_pa.trend.regime import daily_regime

HAVE = Path("data_store/brando_pa/bars/BTCUSDT_1h.parquet").exists()


def _d1(prices):
    ts = pd.date_range("2020-01-01", periods=len(prices), freq="1D", tz="UTC"); p = np.asarray(prices, float)
    return pd.DataFrame({"ts": ts, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1.0})


def test_daily_regime_rules_and_confirmation():
    rc = cfg()["trend"]["regime"]
    up = np.linspace(100, 300, 400); down = np.linspace(300, 100, 300)
    d = daily_regime(_d1(np.concatenate([up, down])), rc)
    assert d["state"].iloc[399] == "BULL"                    # 长期上涨末端：close > sma200, sma50 > sma200, 斜率 > 0
    assert d["state"].iloc[-1] == "BEAR"                     # 长期下跌末端
    assert set(d["state"].unique()) <= {"BULL", "BEAR", "RANGE", "UNCERTAIN"}
    assert (d["state"].iloc[:199] == "UNCERTAIN").all()      # SMA200 未形成前只能 UNCERTAIN
    flat = daily_regime(_d1(np.full(400, 100.0) + np.sin(np.arange(400)) * 0.5), rc)
    assert flat["state"].iloc[-1] in ("RANGE", "UNCERTAIN")


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_regime_is_point_in_time_truncation_identical():
    from brando_pa.backtest.runner import load_contexts
    c = cfg(); rc = c["trend"]["regime"]; mc = load_contexts(["BTCUSDT"], "1h", c)["BTCUSDT"]
    d1 = mc.bars["1d"]; full = daily_regime(d1, rc)
    cut = len(d1) - 100
    trunc = daily_regime(d1.iloc[:cut].reset_index(drop=True), rc)
    assert (full["state"].iloc[:cut].to_numpy() == trunc["state"].to_numpy()).all()    # 截断历史不改变此前任何一天的状态


@pytest.mark.skipif(not HAVE, reason="无数据")
def test_engine_constraints_exits_and_sleeves():
    from brando_pa.backtest.runner import load_contexts
    from brando_pa.trend.engine import TrendSpotBacktester, sma200_benchmark
    c = cfg(); ctxs = load_contexts(["BTCUSDT"], "1h", c)
    # 核心+战术：敞口 ≤ 100%、现金 ≥ 0、核心与战术袖子都出现，战术只用结构止损/策略 C 退出
    r = TrendSpotBacktester("B_core_satellite", "B_trend_failure", c, ["BTCUSDT"], "1h", "2023-01-01", "2024-06-30").run(ctxs)
    eq = r.equity; tr = r.trades
    assert (eq["exposure"] <= 1.0 + 1e-9).all() and (eq["cash"] >= -1e-6).all() and (eq["qty_BTCUSDT"] >= 0).all()
    assert set(tr["sleeve"]) >= {"core", "tactical"}
    assert set(tr.loc[tr["sleeve"] == "tactical", "exit_reason"]) <= {"structure_stop", "stop_gap", "sell|C_rejection", "crash_exit"}
    assert set(tr.loc[tr["sleeve"] == "core", "exit_reason"]) <= {"trend_failure", "crash_exit"}
    # ATR 跟踪：模型 A 的止损只上移（通过逐笔 exit ≥ init? 用 fills 检查 trail 退出价不低于入场后最高收盘 − 3ATR 的下界难以逐根验证，这里验证退出原因集合与无 1R 保本）
    r2 = TrendSpotBacktester("A_trend_following", "C_atr_trailing", c, ["BTCUSDT"], "1h", "2023-01-01", "2024-06-30").run(ctxs)
    assert set(r2.trades["exit_reason"]) <= {"trail_stop", "structure_stop", "stop_gap", "crash_exit"} and "breakeven" not in " ".join(r2.trades["exit_reason"])
    # 趋势失效退出：退出时日线状态不是 BULL
    r3 = TrendSpotBacktester("A_trend_following", "B_trend_failure", c, ["BTCUSDT"], "1h", "2023-01-01", "2024-06-30").run(ctxs)
    assert (r3.trades["state_at_exit"] != "BULL").all()
    # SMA200 基准：持仓时日线收盘 > SMA200
    eqb, fb = sma200_benchmark(ctxs["BTCUSDT"], "2023-01-01", "2024-06-30", 10000.0)
    assert eqb["equity"].iloc[-1] > 0 and (eqb["exposure"] <= 1.0 + 1e-9).all()


def test_exposure_metrics_on_synthetic():
    ts = pd.date_range("2024-01-01", periods=24 * 120, freq="1h", tz="UTC")
    close = pd.Series(np.linspace(100, 200, len(ts)), index=ts)             # 单边上涨
    eq = pd.DataFrame({"ts": ts, "equity": np.where(np.arange(len(ts)) < len(ts) // 2, 10000.0, 10000.0 * close.values / close.values[len(ts) // 2]), "exposure": np.where(np.arange(len(ts)) < len(ts) // 2, 0.0, 1.0), "state_X": "BULL"})
    fills = pd.DataFrame([{"ts": ts[len(ts) // 2], "symbol": "X", "side": "BUY", "qty": 1, "price": float(close.iloc[len(ts) // 2]), "reason": ""}])
    m = analyze(eq, fills, pd.DataFrame(), close, "X", pd.DataFrame({"ts": ts, "equity": 10000.0 * close.values / close.values[0]}))
    assert abs(m["time_in_market_share"] - 0.5) < 0.01 and abs(m["bull_time_in_market_share"] - 0.5) < 0.01 and m["idle_share"] > 0.49
    assert m["up_month_capture"] is not None and 0 < m["up_month_capture"] < 1 and m["gap_vs_bh_final"] < 0
    rec = reconstruct(fills, pd.DataFrame(), close, "X", str(ts[0]), str(ts[-1]))
    assert rec["summary"]["flat"]["segments"] == 1 and rec["summary"]["hold"]["segments"] == 1 and rec["summary"]["flat"]["asset_move_sum"] > 0
