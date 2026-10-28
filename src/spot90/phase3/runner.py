"""第三阶段：单一周期 H 的 Purged Walk-Forward。用法：python -m spot90.phase3.runner --horizon 30 [--quick] [--threads 1]
* 折：与前两阶段相同（2020-01-01 起每 180 天一折，共 14 折），训练集只含 ts ≤ 测试起点 − (H + 10 天 embargo) 的决策日。
* 配置：A（技术指标）、B（A + 交易所流量/储备）、C（A + MVRV 估值 + 链上活跃度）× {LightGBM, CatBoost}，以及 E 基准（无条件频率、趋势×波动条件频率）。
* D（衍生品）：本环境无真实历史数据，不运行。
* 校准：Platt，只用更早折的样本外预测拟合（因果），同时保留原始概率。"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

from ..backtest.dataset import build_dataset, load_bars
from ..backtest.experiments import attach_onchain
from ..backtest.walkforward import fold_schedule
from ..config import report_dir
from ..features import FEATURES
from .calibration import Platt
from .labels import build_multi_labels
from .models import GBM, BaseRate, TrendVolBaseline, OUT_COLS, TARGETS

warnings.filterwarnings("ignore")
EMBARGO = 10
SELECTION_END = pd.Timestamp("2025-06-03", tz="UTC")     # 之后为留出区间，不参与任何模型/特征选择


def feature_sets(groups: dict) -> dict[str, list[str]]:
    g = {k: v["cols"] for k, v in groups.items()}
    return {"A": list(FEATURES), "B": FEATURES + g["exchange_flows"], "C": FEATURES + g["chain_valuation"] + g["chain_activity"]}


def prepare(H: int):
    bars = load_bars()
    ds0 = build_dataset(bars)
    ds, groups = attach_onchain(ds0)
    labs = {s: build_multi_labels(b["1d"], horizons=(H,))[H] for s, b in bars.items() if not b["1d"].empty}
    y = pd.concat(labs.values(), ignore_index=True)
    X = y[["ts", "symbol"]].merge(ds["feats_all"], on=["ts", "symbol"], how="left"); X.index = y.index
    return X, y, feature_sets(groups), bars


def run_horizon(H: int, quick: bool = False, threads: int = 1, log=print) -> dict:
    t0 = time.time()
    X, y, fsets, bars = prepare(H)
    folds = fold_schedule(y["ts"])
    gap = timedelta(days=H + EMBARGO)
    configs = {"E_baserate": None, "E_trendvol": None}
    for s in fsets:
        for m in ("lightgbm", "catboost"):
            configs[f"{s}_{m}"] = (s, m)
    preds = {k: [] for k in configs}
    imps = {k: [] for k in configs if k.startswith(("A_", "B_", "C_")) and k.endswith("lightgbm")}
    fold_log = []
    for k, (s, e) in enumerate(folds):
        tr = (y["ts"] <= s - gap).to_numpy(); te = ((y["ts"] >= s) & (y["ts"] < e)).to_numpy()
        if tr.sum() < 500 or te.sum() == 0:
            continue
        t1 = time.time()
        for name, spec in configs.items():
            if spec is None:
                m = (BaseRate() if name == "E_baserate" else TrendVolBaseline()).fit(X[tr], y[tr])
            else:
                m = GBM(spec[1], fsets[spec[0]], quick=quick, threads=threads).fit(X[tr], y[tr])
                if name in imps:
                    imps[name].append(m.importance())
            p = m.predict(X[te]); p["fold"] = k
            preds[name].append(p)
        fold_log.append({"fold": k, "test_start": str(s.date()), "test_end": str(e.date()), "train_last": str(y.loc[tr, "ts"].max().date()),
                         "gap_days": int((s - y.loc[tr, "ts"].max()).days), "n_train": int(tr.sum()), "n_test": int(te.sum()), "seconds": round(time.time() - t1)})
        log(f"H={H} 折 {k} {s.date()} 训练截至 {y.loc[tr, 'ts'].max().date()} 间隔 {(s - y.loc[tr, 'ts'].max()).days} 天 {time.time()-t1:.0f}s")
    out = report_dir() / "phase3" / "preds"; out.mkdir(parents=True, exist_ok=True)
    for name, lst in preds.items():
        p = pd.concat(lst)
        p = pd.concat([y.loc[p.index, ["ts", "symbol"]], p], axis=1)
        # 因果 Platt 校准
        for t in TARGETS:
            col = f"p_{t}"; p[col + "_cal"] = p[col].to_numpy(float)
            yy = y.loc[p.index, t].to_numpy(float)
            for f in sorted(p["fold"].unique()):
                prev = (p["fold"] < f).to_numpy(); cur = (p["fold"] == f).to_numpy()
                cal = Platt().fit(p.loc[prev, col].to_numpy(float), yy[prev])
                if cal is not None:
                    p.loc[cur, col + "_cal"] = cal.predict(p.loc[cur, col].to_numpy(float))
        p.to_parquet(out / f"H{H}_{name}.parquet")
    imp_out = {k: pd.concat(v, axis=1).mean(axis=1).sort_values(ascending=False).head(25).round(1).to_dict() for k, v in imps.items() if v}
    meta = {"H": H, "gap_days": H + EMBARGO, "folds": fold_log, "configs": list(configs), "feature_sets": {k: len(v) for k, v in fsets.items()},
            "importance": imp_out, "seconds": round(time.time() - t0), "selection_end": str(SELECTION_END.date())}
    (report_dir() / "phase3" / f"meta_H{H}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    y.to_parquet(out / f"labels_H{H}.parquet")
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--horizon", type=int, required=True); ap.add_argument("--quick", action="store_true"); ap.add_argument("--threads", type=int, default=1)
    a = ap.parse_args()
    m = run_horizon(a.horizon, quick=a.quick, threads=a.threads)
    print("done", a.horizon, m["seconds"], "s")
