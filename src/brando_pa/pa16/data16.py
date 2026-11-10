"""第十六阶段数据层（预登记 §1–§3）：
- BTC 日线拼接：2012–2015-07-19 CoinMetrics PriceUSD（只有收盘价，开盘 = 前一日收盘，无高低量）→ 2015-07-20 起 Coinbase BTC-USD OHLCV → 2017-08-17 起第十阶段数据集（Binance / OKX）。逐行记录来源。
- 矿工现金成本：价格无关的“矿机代际”模型（只用算力、矿机公开规格、电价假设与日产出）。
- 信号：成本比、MVRV、Puell、算力价格（hashprice）、周线 20/40 周 SMA、减半阶段；链上与算力类滞后 1 日。
- 宏观（FRED WALCL / M2SL）只作背景，不进入规则。
只读；不联网（抓取已在快照中完成）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..holding.data import load_dataset
from ..pa15 import data15

RAW16 = PROJECT_ROOT / "data_store" / "brando_pa" / "phase16_minercycle" / "raw"
HALVINGS = data15.HALVINGS
SUBSIDY = [(pd.Timestamp("2009-01-03", tz="UTC"), 50.0), (HALVINGS[0], 25.0), (HALVINGS[1], 12.5), (HALVINGS[2], 6.25), (HALVINGS[3], 3.125)]

# 矿机公开规格（发布日, 型号, J/TH）。凭公开资料整理，未逐一核对原始规格书；误差纳入成本区间（预登记 §2）。
HARDWARE = [("2013-01-30", "Avalon Gen1", 9400.0), ("2013-11-01", "Antminer S1", 2000.0), ("2014-07-01", "Antminer S3", 770.0), ("2014-12-01", "Antminer S5", 510.0),
            ("2015-09-01", "Antminer S7", 273.0), ("2016-06-01", "Antminer S9", 98.0), ("2018-11-01", "Antminer S15", 57.0), ("2019-04-01", "Antminer S17 Pro", 40.0),
            ("2020-05-01", "Antminer S19", 34.0), ("2022-07-01", "Antminer S19 XP", 21.5), ("2024-01-01", "Antminer S21", 17.5), ("2024-11-01", "Antminer S21 XP", 13.5)]
LEGACY_JTH = 500_000.0            # 2013-01 之前的 GPU / FPGA 存量
DELIVERY_LAG_DAYS = 90
PUE = 1.10
ELEC = {"low": 0.04, "mid": 0.06, "high": 0.08}
EFF_MULT = {"low": 0.85, "mid": 1.0, "high": 1.30}
OBSOLETE_RATIO = 10.0
MAX_AGE_DAYS = 5 * 365
ALLIN_CASH_SHARE = 0.6
RELIABILITY = [("2014-01-01", "低"), ("2016-01-01", "中"), ("2020-01-01", "中")]


def _norm(ts) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).normalize()


def load_btc_daily(start: str = "2012-01-01") -> pd.DataFrame:
    """拼接 BTC 日线；返回 ts/open/high/low/close/volume/source/ohlc_ok。只含已收盘日。"""
    p10 = load_dataset()["BTCUSDT"].copy(); p10["ts"] = _norm(p10["ts"]); p10["source"] = p10.get("source", "phase10_dataset"); p10["source"] = "phase10(binance/okx)"
    cb = pd.read_csv(RAW16 / "coinbase_btc_usd_1d.csv"); cb["ts"] = _norm(cb["ts"]); cb["source"] = "coinbase_btc_usd"
    cm = data15.load_coinmetrics("btc")["PriceUSD"].dropna(); cm = pd.DataFrame({"ts": cm.index, "close": cm.values}); cm["source"] = "coinmetrics_close_only"
    cut_cb, cut_p10 = pd.Timestamp("2015-07-20", tz="UTC"), p10["ts"].min()
    a = cm[(cm.ts >= pd.Timestamp(start, tz="UTC")) & (cm.ts < cut_cb)].copy()
    a["open"] = a["close"].shift(1); a.loc[a.index[0], "open"] = a["close"].iloc[0]; a["high"] = a[["open", "close"]].max(axis=1); a["low"] = a[["open", "close"]].min(axis=1); a["volume"] = np.nan; a["ohlc_ok"] = False
    b = cb[(cb.ts >= cut_cb) & (cb.ts < cut_p10)].copy(); b["ohlc_ok"] = True
    c = p10[p10.ts >= cut_p10][["ts", "open", "high", "low", "close", "volume", "source"]].copy(); c["ohlc_ok"] = True
    d = pd.concat([a, b, c], ignore_index=True)[["ts", "open", "high", "low", "close", "volume", "source", "ohlc_ok"]].sort_values("ts").reset_index(drop=True)
    last_complete = p10["ts"].max(); d = d[d.ts <= last_complete].reset_index(drop=True)
    return d


def subsidy_at(dates: pd.DatetimeIndex) -> pd.Series:
    out = pd.Series(np.nan, index=dates)
    for t, s in SUBSIDY:
        out[dates >= t] = s
    return out


def vintage_fleet(hashrate_ths: pd.Series) -> pd.DataFrame:
    """矿机代际模型（预登记 §2）。输入：逐日算力（TH/s，原始日值）；输出：逐日机队效率 J/TH（基于 ≤ t 的算力）与最新可交付机型。"""
    hw = [(pd.Timestamp(d, tz="UTC") + pd.Timedelta(days=DELIVERY_LAG_DAYS), n, e) for d, n, e in HARDWARE]
    h30 = hashrate_ths.rolling(30, min_periods=1).mean()
    stock: list[list] = []          # [efficiency, ths, added_date]
    rows = []
    for t, target in h30.items():
        avail = [x for x in hw if x[0] <= t]; best = avail[-1] if avail else (None, "GPU/FPGA", LEGACY_JTH)
        best_eff, best_name = best[2], best[1]
        if not stock:
            stock.append([LEGACY_JTH if not avail else best_eff, float(target), t])
        # 淘汰：效率劣于最新机型 10 倍以上，或年龄满 5 年 → 等量替换为最新机型
        repl = 0.0; keep = []
        for e, q, d0 in stock:
            if e > OBSOLETE_RATIO * best_eff or (t - d0).days >= MAX_AGE_DAYS:
                repl += q
            else:
                keep.append([e, q, d0])
        stock = keep + ([[best_eff, repl, t]] if repl > 0 else [])
        cur = sum(q for _, q, _ in stock); delta = float(target) - cur
        if delta > 0:
            stock.append([best_eff, delta, t])
        elif delta < 0:
            need = -delta
            for row in sorted(stock, key=lambda r: -r[0]):          # 先移除效率最差的
                take = min(row[1], need); row[1] -= take; need -= take
                if need <= 1e-12:
                    break
            stock = [r for r in stock if r[1] > 1e-12]
        tot = sum(q for _, q, _ in stock)
        eff = sum(e * q for e, q, _ in stock) / tot if tot > 0 else np.nan
        rows.append({"date": t, "hashrate_30d_ths": float(target), "fleet_jth": eff, "best_model": best_name, "best_jth": best_eff})
    return pd.DataFrame(rows).set_index("date")


def miner_cost(panel: pd.DataFrame | None = None, variant: str = "corrected") -> pd.DataFrame:
    """逐日现金成本区间（原始日值，未滞后）。只用算力、矿机规格、电价假设与日产出；不使用价格。"""
    p = panel if panel is not None else data15.build_panel("btc")
    hr = p["bci_hashrate_ths"].combine_first(p["HashRate"]).dropna(); hr = hr[hr.index >= pd.Timestamp("2012-06-01", tz="UTC")]
    fleet = vintage_fleet(hr); out = fleet.copy()
    btc_raw = (p["IssTotNtv"] + p["FeeTotNtv"])
    # 【事后修正 2026-11-10，任何策略回测之前】预登记原版用逐日产出作分母（分子是 30 日算力），日出块数波动 ±40% 造成成本单日跳变 > 50%，
    # 未通过预登记可靠性检查。修正：分母改为 30 日平均出块数 × 当日协议补贴 + 30 日平均手续费，与分子平滑口径一致且减半即时生效。原版结果见 reliability_checks(variant="preregistered")。
    # 平滑方式：30 日平均出块数 × 当日协议补贴 + 30 日平均手续费（减半当天补贴即时减半，不被平滑拖后）
    subs = subsidy_at(p.index); blocks = (p["IssTotNtv"] / subs).rolling(30, min_periods=1).mean(); fees = p["FeeTotNtv"].rolling(30, min_periods=1).mean()
    btc_day = (btc_raw if variant == "preregistered" else (blocks * subs + fees)).reindex(out.index)
    for k in ("low", "mid", "high"):
        kwh = out["hashrate_30d_ths"] * out["fleet_jth"] * EFF_MULT[k] * PUE * 86400.0 / 3.6e6
        out[f"cost_{k}"] = kwh * ELEC[k] / btc_day.replace(0, np.nan)
    out["cost_allin_mid"] = out["cost_mid"] / ALLIN_CASH_SHARE
    out["network_power_gw"] = out["hashrate_30d_ths"] * out["fleet_jth"] * PUE / 1e9
    out["btc_per_day"] = btc_day
    rel = pd.Series("—", index=out.index)
    for d, lab in RELIABILITY:
        rel[out.index >= pd.Timestamp(d, tz="UTC")] = lab
    out["reliability"] = rel
    return out


def weekly_frame(d: pd.DataFrame) -> pd.DataFrame:
    """周线（UTC 周一开始，周日收盘）；20/40 周 SMA；只在周日收盘那一天有值。"""
    s = d.set_index("ts")["close"]; w = s.resample("W-SUN", label="right", closed="right").last().dropna()
    w = pd.DataFrame({"w_close": w}); w["sma20w"] = w["w_close"].rolling(20).mean(); w["sma40w"] = w["w_close"].rolling(40).mean(); w["sma20w_4ago"] = w["sma20w"].shift(4)
    return w


def signal_frame(d: pd.DataFrame | None = None) -> pd.DataFrame:
    """按日线索引对齐的全部输入（链上 / 算力 / 成本已滞后 1 日；周线只在周日有值）。"""
    d = d if d is not None else load_btc_daily(); idx = pd.DatetimeIndex(d["ts"])
    panel = data15.build_panel("btc"); cost = miner_cost(panel)
    lag = lambda s: s.shift(1, freq="D")
    f = pd.DataFrame(index=idx)
    for col in ("cost_low", "cost_mid", "cost_high", "cost_allin_mid", "fleet_jth", "network_power_gw", "hashrate_30d_ths"):
        f[col] = lag(cost[col]).reindex(idx)
    f["reliability"] = cost["reliability"].reindex(idx).ffill()
    f["best_model"] = lag(cost["best_model"]).reindex(idx)
    f["mvrv"] = lag(panel["CapMVRVCur"]).reindex(idx); f["realized_price"] = lag(panel["PriceUSD"] / panel["CapMVRVCur"]).reindex(idx)
    f["puell"] = lag(panel["IssTotUSD"] / panel["IssTotUSD"].rolling(365, min_periods=300).mean()).reindex(idx)
    f["hashprice_usd_per_ph_day"] = lag(panel["bci_miners_revenue_usd"] / (panel["bci_hashrate_ths"] / 1000.0)).reindex(idx)
    f["subsidy"] = subsidy_at(idx).values; f["halving_phase"] = data15.halving_phase(idx).values
    f["close"] = d["close"].to_numpy(float); f["r"] = f["close"] / f["cost_mid"]
    sma = d["close"].rolling(200).mean(); f["sma200"] = sma.to_numpy()
    w = weekly_frame(d); f = f.join(w, how="left")
    f["is_week_close"] = f["w_close"].notna()
    return f


def macro_context() -> pd.DataFrame:
    """背景（不进入规则）：WALCL 周三数据周四公布 → 可用日 = 周三 + 2；M2SL 月度，按月末 + 28 天可用（修订风险，无历史快照）。"""
    w = pd.read_csv(RAW16 / "fred_WALCL.csv"); w["date"] = pd.to_datetime(w.iloc[:, 0], utc=True) + pd.Timedelta(days=2); w = w.set_index("date").iloc[:, -1].astype(float).rename("walcl_musd")
    m = pd.read_csv(RAW16 / "fred_M2SL.csv"); m["date"] = pd.to_datetime(m.iloc[:, 0], utc=True) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=28); m = m.set_index("date").iloc[:, -1].astype(float).rename("m2_busd")
    out = pd.concat([w, m], axis=1).sort_index().ffill()
    out["walcl_chg_13w"] = w.pct_change(13).reindex(out.index).ffill(); out["m2_yoy"] = m.pct_change(12).reindex(out.index).ffill()
    return out


def reliability_checks(cost: pd.DataFrame | None = None, variant: str = "corrected") -> dict:
    """预登记 §2 可靠性检查：与第十五阶段锚点模型对比；中值成本无跳变。"""
    from ..pa15 import factors15
    cost = cost if cost is not None else miner_cost(variant=variant); p15 = factors15.btc_factors()["miner_cost_mid"]
    j = pd.concat([cost["cost_mid"].rename("v"), p15.rename("a")], axis=1).dropna(); j = j[(j.index >= "2018-01-01")]
    corr = float(np.corrcoef(np.log(j["v"]), np.log(j["a"]))[0, 1]); ratio = float((j["v"] / j["a"]).median())
    cm = cost["cost_mid"].dropna(); cm = cm[cm.index >= "2014-01-01"]; ch = cm.pct_change().abs()
    halving_days = set()
    for h in HALVINGS:
        for k in range(0, 32):
            halving_days.add((h + pd.Timedelta(days=k)).normalize())
    jumps = [str(t.date()) for t, v in ch.items() if v > 0.5 and t.normalize() not in halving_days]
    ok = corr >= 0.9 and 0.7 <= ratio <= 1.4 and (cm > 0).all() and not jumps
    return {"variant": variant, "vs_phase15_log_corr": corr, "vs_phase15_median_ratio": ratio, "n_overlap_days": int(len(j)), "positive": bool((cm > 0).all()), "jump_days_gt50pct_excl_halving": jumps, "verifiable": bool(ok),
            "note": "第十五阶段锚点模型同样是估计，此检查只能说明两种独立建模方法一致，不能证明与真实成本一致（真实披露与 CBECI 均不可得）"}


def audit(d: pd.DataFrame | None = None) -> dict:
    d = d if d is not None else load_btc_daily(); out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "price_segments": {}, "overlaps": {}, "unavailable": {}, "lags": {}}
    for src, g in d.groupby("source"):
        idx = pd.DatetimeIndex(g["ts"]); full = pd.date_range(idx.min(), idx.max(), freq="1D")
        out["price_segments"][src] = {"start": str(idx.min().date()), "end": str(idx.max().date()), "n": int(len(g)), "missing_days": int(len(full.difference(idx))), "ohlcv": bool(g["ohlc_ok"].all()),
                                      "bad_ohlc_rows": int(((g["high"] < g[["open", "close"]].max(axis=1) - 1e-9) | (g["low"] > g[["open", "close"]].min(axis=1) + 1e-9)).sum())}
    cb = pd.read_csv(RAW16 / "coinbase_btc_usd_1d.csv"); cb["ts"] = _norm(cb["ts"]); p10 = load_dataset()["BTCUSDT"].copy(); p10["ts"] = _norm(p10["ts"])
    j = cb.merge(p10, on="ts", suffixes=("_cb", "_bn")); rel = (j["close_cb"] / j["close_bn"] - 1).abs()
    out["overlaps"]["coinbase_vs_phase10_close"] = {"n": int(len(j)), "median_abs_rel": float(rel.median()), "p99_abs_rel": float(rel.quantile(.99)), "note": "USD vs USDT 与交易所差异；拼接点 2017-08-17 之后统一用第十阶段数据"}
    cm = data15.load_coinmetrics("btc")["PriceUSD"]; k = cb.set_index("ts")["close"]; jj = pd.concat([k, cm], axis=1).dropna(); jj = jj[jj.index < "2017-08-17"]; r2 = (jj.iloc[:, 0] / jj.iloc[:, 1] - 1).abs()
    out["overlaps"]["coinbase_vs_coinmetrics_2015_2017"] = {"n": int(len(jj)), "median_abs_rel": float(r2.median()), "p99_abs_rel": float(r2.quantile(.99))}
    out["unavailable"] = {"miner_disclosed_cash_cost": "矿企季报（SEC 10-Q/10-K）中的每 BTC 现金成本：需人工从文件提取，本阶段未获得；不使用任何凭记忆的数字",
                          "cbeci": "剑桥 CBECI 网络功耗 / 铸币成本：网站拒绝自动访问（返回 400/404），未绕过；因此成本模型没有外部真实校准",
                          "miner_onchain_flows": "矿工钱包流出 / 矿工持币：CoinMetrics 社区层无该指标；付费数据未接入",
                          "lth_sth_cost": "长期 / 短期持有者链上成本：付费数据，未接入",
                          "ohlcv_2014_2015": "2014-01-01 → 2015-07-19 只有收盘价（CoinMetrics 参考价），无开高低与成交量 → C 实验在该段不可验证",
                          "usdt_pre_2017": "2017-08 之前用 USD 报价代替 USDT（当时 USDT 市场很小）"}
    out["lags"] = {"price": "第 t 日收盘后可用", "hashrate/issuance/fees/mvrv": "滞后 1 日", "hardware_specs": f"发布日 + {DELIVERY_LAG_DAYS} 天", "halvings": "协议事实，不滞后", "walcl": "周三数据 + 2 日（只作背景）", "m2": "月末 + 28 日（只作背景，修订风险）"}
    out["hardware_table"] = [{"release": a, "model": b, "j_per_th": c} for a, b, c in HARDWARE]
    out["cost_model"] = {"pue": PUE, "electricity_usd_kwh": ELEC, "efficiency_multiplier": EFF_MULT, "obsolete_ratio": OBSOLETE_RATIO, "max_age_days": MAX_AGE_DAYS, "allin_cash_share": ALLIN_CASH_SHARE, "uses_price": False}
    return out
