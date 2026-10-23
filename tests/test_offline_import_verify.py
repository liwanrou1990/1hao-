"""离线导入（Bitget 导出 / Tardis / 通用 OHLCV）、分级与逐笔校验：fixture 为人工构造的 CSV（标记为 csv 来源，仅用于验证解析与校验逻辑）。"""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bgw_quant.data.offline_import import SOURCE_PREFIX, import_dir, import_file, parse_depth_csv, parse_kline_csv, parse_trades_csv
from bgw_quant.data.crypto import crypto_day_bounds_utc

DAY = date(2026, 10, 22)


def _kline_rows(n=1440, p0=100.0):
    start, _ = crypto_day_bounds_utc(DAY)
    ts = [int(start.timestamp() * 1000) + i * 60_000 for i in range(n)]
    rng = np.random.default_rng(1)
    close = p0 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    rows = []
    for i in range(n):
        o = close[i - 1] if i else p0
        rows.append([ts[i], o, max(o, close[i]) * 1.0005, min(o, close[i]) * 0.9995, close[i], 10.0, 10.0 * close[i], 10.0 * close[i]])
    return rows


def test_parse_bitget_export_kline_and_generic(tmp_path):
    rows = _kline_rows()
    f = tmp_path / "kline.csv"
    pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close", "volume", "amount", "usdt_volume"]).to_csv(f, index=False)
    bars = parse_kline_csv(f, "BTC")
    assert len(bars) == 1440 and bars["source"].iloc[0].startswith(SOURCE_PREFIX) and int(bars["quality"].iloc[0]) & 128 == 0
    assert bars["ts_ny"].iloc[0] == "2026-10-22 00:00:00"
    g = tmp_path / "generic.csv"                                   # 秒级时间戳 + 不同列名 → column_map
    pd.DataFrame({"Open Time": [r[0] // 1000 for r in rows], "O": [r[1] for r in rows], "H": [r[2] for r in rows], "L": [r[3] for r in rows], "C": [r[4] for r in rows]}).to_csv(g, index=False)
    b2 = parse_kline_csv(g, "ETH", column_map={"ts": "open_time", "open": "o", "high": "h", "low": "l", "close": "c"})
    assert len(b2) == 1440 and np.isnan(b2["amount"].iloc[0])        # 无成交量 → 流动性未知（不伪造）


def test_parse_trades_bitget_and_tardis(tmp_path):
    start, _ = crypto_day_bounds_utc(DAY)
    base = int(start.timestamp() * 1000) + 60_000
    f = tmp_path / "trades.csv"
    pd.DataFrame({"tradeId": range(100), "ts_ms": [base + i * 500 for i in range(100)], "price": 100.0, "size": 0.2, "side": ["buy", "sell"] * 50}).to_csv(f, index=False)
    tx = parse_trades_csv(f, "BTC")
    assert len(tx) == 100 and tx["value"].sum() == pytest.approx(2000.0) and tx["ts_ny"].iloc[0].startswith("2026-10-22 00:01")
    t = tmp_path / "tardis_trades.csv"
    pd.DataFrame({"exchange": "bitget", "symbol": "BTCUSDT", "timestamp": [base * 1000 + i * 500_000 for i in range(10)], "local_timestamp": 0,
                  "id": range(10), "side": "buy", "price": 100.0, "amount": 1.0}).to_csv(t, index=False)
    tx2 = parse_trades_csv(t, "BTC")
    assert len(tx2) == 10 and tx2["amount"].sum() == 10.0


def test_parse_depth_tardis_and_bitget_json(tmp_path):
    start, _ = crypto_day_bounds_utc(DAY)
    base = int(start.timestamp() * 1000)
    cols = {"timestamp": [base * 1000, (base + 60_000) * 1000]}
    for i in range(3):
        cols[f"asks[{i}].price"] = [100.01 + i * 0.01] * 2; cols[f"asks[{i}].amount"] = [10.0] * 2
        cols[f"bids[{i}].price"] = [99.99 - i * 0.01] * 2; cols[f"bids[{i}].amount"] = [10.0] * 2
    f = tmp_path / "depth.csv"
    pd.DataFrame(cols).to_csv(f, index=False)
    dp = parse_depth_csv(f, "BTC")
    assert len(dp) == 2 and dp["mid"].iloc[0] == pytest.approx(100.0) and dp["ask_usd_25bp"].iloc[0] > 0
    g = tmp_path / "depth2.csv"
    pd.DataFrame({"ts_ms": [base], "bids": [json.dumps([["99.9", "5"], ["99.8", "5"]])], "asks": [json.dumps([["100.1", "5"]])]}).to_csv(g, index=False)
    dp2 = parse_depth_csv(g, "BTC")
    assert len(dp2) == 1 and dp2["spread_bps"].iloc[0] == pytest.approx(0.2 / 100 * 1e4, rel=1e-3)


def test_import_dir_then_playbook_status(tmp_path, monkeypatch):
    """端到端：导入真实格式的 K 线 + 逐笔（无深度）→ 分级 EXECUTION_ESTIMATED；补足深度快照 → REAL_HISTORICAL；逐笔校验全部通过。"""
    monkeypatch.setenv("BGWQ_DB_PATH", str(tmp_path / "x.duckdb"))
    from bgw_quant.config import get_settings
    get_settings.cache_clear()
    from bgw_quant.pipeline import Pipeline
    pl = Pipeline(probe=False)
    root = tmp_path / "data_in" / "BTC" / DAY.isoformat()
    root.mkdir(parents=True)
    rows = _kline_rows(p0=100.0)
    pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close", "volume", "amount", "usdt_volume"]).to_csv(root / "kline.csv", index=False)
    start, _ = crypto_day_bounds_utc(DAY)
    base = int(start.timestamp() * 1000)
    tr = pd.DataFrame({"tradeId": range(1440 * 4), "ts_ms": [base + i * 15_000 for i in range(1440 * 4)], "price": [r[4] for r in rows for _ in range(4)], "size": 50.0, "side": "buy"})
    tr.to_csv(root / "trades.csv", index=False)
    out = import_dir(pl.store, tmp_path / "data_in", DAY)
    assert {o["kind"] for o in out} == {"kline", "trades"} and all("error" not in o for o in out)
    prov = pl.data_provenance(DAY, ["BTC"])["BTC"]
    assert prov["simulated"] is False and prov["status"] == "EXECUTION_ESTIMATED" and prov["trades"] == 5760
    pl.require_real(DAY, ["BTC"])                                                # 真实数据 → 严格模式通过
    r = pl.playbook_tier(DAY, 60_000, 540, ["BTC"], tier="T2", mode="single:BTC")
    assert not r.data_is_simulated
    v = pl.verify_playbook(r, DAY)
    assert v["status"] == "EXECUTION_ESTIMATED" and v["n_ok"] == v["n_trades"] and v["liquidity_source"]["BTC"] == "transactions"
    # 深度快照覆盖全天（每分钟一条，25bp 内 1e6 USD）→ REAL_HISTORICAL，且深度校验执行
    dp = pd.DataFrame({"ts": pd.to_datetime([base + i * 60_000 for i in range(1440)], unit="ms", utc=True), "mid": 100.0, "spread_bps": 1.0,
                       "bid_usd_10bp": 5e5, "ask_usd_10bp": 5e5, "bid_usd_25bp": 1e6, "ask_usd_25bp": 1e6, "bid_usd_50bp": 2e6, "ask_usd_50bp": 2e6,
                       "bid_usd_100bp": 4e6, "ask_usd_100bp": 4e6, "levels": 10, "source": "test", "ingested_at": pd.Timestamp.now(tz="UTC")})
    dp["ts_ny"] = dp["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S"); dp["symbol"] = "BTC"
    pl.store.upsert_depth(dp)
    v2 = pl.verify_playbook(r, DAY)
    assert v2["status"] == "REAL_HISTORICAL" and v2["n_depth_checked"] == v2["n_trades"] and v2["n_depth_failed"] == 0
    get_settings.cache_clear()


def test_verify_flags_violations():
    """人为构造违规交易：超成交量、用最低价成交、成本不足 → 全部被标记。"""
    from bgw_quant.costs.model import load_cost_profile
    from bgw_quant.decision.capability import capability_for
    from bgw_quant.data.simulated import SimulatedSource
    from bgw_quant.data.quality import apply_quality_flags
    from bgw_quant.rotation.universe import make_asset_day
    from bgw_quant.playbook.verify import verify_trades
    a = make_asset_day("BTC", apply_quality_flags(SimulatedSource().fetch_crypto_day("BTC", DAY)), load_cost_profile("crypto_spot"), capability_for("BTC"), DAY)
    i, j = 100, 160
    c = a.bars["close"].to_numpy(float); lo = a.bars["low"].to_numpy(float)
    gi, gj = int(a.grid_of_bar[i]), int(a.grid_of_bar[j])
    bad = {"no": 1, "symbol": "BTC", "direction": "long", "entry_grid": gi, "exit_grid": gj, "entry_price": lo[i], "exit_price": c[j] * 1.01,
           "size_usd": float(a.bars["amount"].iloc[i]) * 5, "qty": 1.0, "cost_usd": 0.0, "gas_usd": 0.5, "exit_ref": c[j]}
    v = verify_trades([bad], {"BTC": a}, 0.10)
    ck = v.checks[0]
    assert not ck.volume_ok and not ck.no_high_low_fill_ok and not ck.cost_ok and v.status == "SIMULATED"
