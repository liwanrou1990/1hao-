"""泄漏与数据完整性审计（代码级 + 实证级）。
1. 特征因果性：把第 i 天之后的 K 线全部删掉重新算特征，第 i 天的特征必须完全一致。
2. 标签正确性：随机抽样决策日，用最朴素的循环重算 A/B 标签并与向量化结果核对。
3. 折间隔离：每折 训练最后决策日 + (W+H+embargo) ≤ 测试起点；测试块互不重叠、连续。
4. 最后 90 天：最后一个有完整标签的决策日 = 最后 K 线 − (W+H)；之后的决策日没有标签，不会进入训练/评估。
5. 数据源：来源、范围、缺口、交叉校验偏差最大的日期。"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import cfg, data_dir, report_dir
from ..features import FEATURES, build_features
from ..labels import build_labels, cost_fractions


def feature_causality_check(bars_1d: pd.DataFrame, bars_4h: pd.DataFrame, market_1d: pd.DataFrame, n_checks: int = 8, seed: int = 0) -> dict:
    full = build_features(bars_1d, bars_4h, market_1d)
    rng = np.random.default_rng(seed)
    idxs = rng.integers(400, len(bars_1d) - 120, size=n_checks)
    worst = 0.0
    details = []
    for i in idxs:
        cut_ts = bars_1d["ts"].iloc[i]
        trunc = build_features(bars_1d[bars_1d["ts"] <= cut_ts], bars_4h[bars_4h["ts"] < cut_ts + timedelta(days=1)], market_1d[market_1d["ts"] <= cut_ts])
        a = full.loc[full["ts"] == cut_ts, FEATURES].iloc[0].to_numpy(dtype=float)
        b = trunc.loc[trunc["ts"] == cut_ts, FEATURES].iloc[0].to_numpy(dtype=float)
        m = np.isfinite(a) & np.isfinite(b)
        diff = float(np.max(np.abs(a[m] - b[m]))) if m.any() else 0.0
        nan_mismatch = int((np.isfinite(a) != np.isfinite(b)).sum())
        worst = max(worst, diff)
        details.append({"ts": cut_ts.date().isoformat(), "max_abs_diff": diff, "nan_mismatch": nan_mismatch})
    return {"passed": bool(worst < 1e-9 and all(d["nan_mismatch"] == 0 for d in details)), "worst_abs_diff": worst, "checks": details}


def label_recompute_check(bars_1d: pd.DataFrame, n_checks: int = 30, seed: int = 1) -> dict:
    c = cfg(); H, W = int(c["horizon_days"]), int(c["wait_days"]); cost = cost_fractions()
    wide, long = build_labels(bars_1d)
    df = bars_1d.sort_values("ts").reset_index(drop=True)
    o, l, cl = df["open"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    rng = np.random.default_rng(seed)
    errs = []
    for i in rng.integers(0, len(wide), size=n_checks):
        entry = o[i + 1]
        exit_ = cl[i + H]
        ret = exit_ * (1 - cost["out"]) / (entry * (1 + cost["market_in"])) - 1
        peak = entry; mdd = 0.0; mae = 0.0
        for j in range(i + 1, i + H + 1):
            mdd = min(mdd, l[j] / peak - 1); mae = min(mae, l[j] / entry - 1); peak = max(peak, cl[j])
        errs.append(abs(ret - wide.loc[i, "ret_A"]) + abs(mdd - wide.loc[i, "mdd_A"]) + abs(mae - wide.loc[i, "mae_A"]))
        for d in (3.0, 8.0):
            limit = cl[i] * (1 - d / 100)
            fill_j = next((j for j in range(i + 1, i + W + 1) if l[j] <= limit), None)
            row = long[(long["ts"] == wide.loc[i, "ts"]) & (long["d_pct"] == d)].iloc[0]
            if fill_j is None:
                errs.append(0.0 if row["filled"] == 0 else 1.0)
            else:
                fp = min(limit, o[fill_j]); ex = cl[fill_j + H - 1]
                rb = ex * (1 - cost["out"]) / (fp * (1 + cost["limit_in"])) - 1
                errs.append(abs(rb - row["ret_B"]) + abs(fill_j - i - row["fill_offset"]) + (0.0 if row["filled"] == 1 else 1.0))
    return {"passed": bool(max(errs) < 1e-9), "max_err": float(max(errs)), "n_checks": n_checks,
            "last_bar": df["ts"].iloc[-1].date().isoformat(), "last_labeled_decision_day": wide["ts"].iloc[-1].date().isoformat(),
            "days_between": int((df["ts"].iloc[-1] - wide["ts"].iloc[-1]).days), "required_gap_W_plus_H": W + H}


def fold_isolation_check(folds: list[dict]) -> dict:
    c = cfg(); gap = int(c["wait_days"]) + int(c["horizon_days"]) + int(c["backtest"]["embargo_days"])
    rows, ok = [], True
    prev_end = None
    for f in folds:
        s = pd.Timestamp(f["test_start"]); last_tr = pd.Timestamp(f["train_last_ts"])
        g = (s - last_tr).days
        good = g >= gap and (prev_end is None or pd.Timestamp(f["test_end"]) > s and s == prev_end)
        ok &= bool(good)
        rows.append({"fold": f["fold"], "test": f"{f['test_start']}→{f['test_end']}", "train_last": f["train_last_ts"], "gap_days": g, "ok": bool(good)})
        prev_end = pd.Timestamp(f["test_end"])
    return {"passed": bool(ok), "required_gap_days": gap, "folds": rows}


def data_source_audit(store, symbols: list[str]) -> dict:
    st = json.loads((data_dir() / "data_status.json").read_text())
    out = {}
    for s in symbols:
        b = store.load(s, "1d")
        ref_p = store.root / "bars" / f"{s}_ref_daily_close.parquet"
        worst = []
        if ref_p.exists():
            ref = pd.read_parquet(ref_p); ref["date"] = pd.to_datetime(ref["date"], utc=True)
            a = b[["ts", "close"]].copy(); a["date"] = a["ts"].dt.floor("D") + pd.Timedelta(days=1)
            m = a.merge(ref, on="date"); m["dev_pct"] = (m["close"] / m["close_ref"] - 1) * 100
            worst = m.reindex(m["dev_pct"].abs().sort_values(ascending=False).index).head(5)[["ts", "close", "close_ref", "dev_pct"]].assign(ts=lambda x: x["ts"].dt.date.astype(str)).to_dict(orient="records")
        e1, e4 = st["symbols"][s]["1d"], st["symbols"][s]["4h"]
        out[s] = {"source_1d": e1["continuity"]["sources"], "range_1d": [e1["continuity"]["first"][:10], e1["continuity"]["last"][:10]], "years": e1["continuity"]["years"],
                  "gaps_1d": e1["continuity"]["n_gap_bars"], "gaps_4h_bars": e4["continuity"]["n_gap_bars"], "gaps_4h_detail": e4["continuity"]["gaps"][:20],
                  "duplicates_1d": e1["continuity"]["duplicates"], "zero_volume_days": int((b["volume"] <= 0).sum()),
                  "cross_check": e1.get("cross_check"), "worst_crosscheck_days": worst,
                  "bitget_probe": st["probes"]["bitget"], "note": "主数据为 GitHub 公开仓库 finom/static-klines 提交的 Binance 现货 K 线缓存（非 Bitget）；Bitget 在本环境被出网策略拒绝。"}
    return out


def run_audit(bars: dict, folds: list[dict], store) -> dict:
    syms = list(bars)
    res = {"feature_causality": {s: feature_causality_check(bars[s]["1d"], bars[s]["4h"], bars["BTCUSDT"]["1d"]) for s in syms},
           "label_recompute": {s: label_recompute_check(bars[s]["1d"]) for s in syms},
           "fold_isolation": fold_isolation_check(folds), "data_sources": data_source_audit(store, syms)}
    res["all_passed"] = bool(all(v["passed"] for v in res["feature_causality"].values()) and all(v["passed"] for v in res["label_recompute"].values()) and res["fold_isolation"]["passed"])
    return res
