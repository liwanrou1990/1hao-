"""Walk-Forward 滚动样本外：
* 测试块长度 test_block_days；训练集只含“标签窗口完全结束于测试块开始之前”的决策日：
  ts ≤ test_start − (W + H + embargo) 天  → 杜绝 90 天重叠标签泄漏（purge + embargo）。
* 四类模型每折独立训练：基准频率、波动率自助、LightGBM、CatBoost；另给 LightGBM+CatBoost 平均（ensemble）。
* 概率校准：对折 k，用折 <k 的样本外预测拟合等渗回归（因果）。"""
from __future__ import annotations

import time
from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import cfg
from ..models.baselines import BaseRateModel, VolBootstrapModel
from ..models.calibration import apply_cal, fit_isotonic
from ..models.common import A_COLS, B_COLS
from ..models.ml import CatBoostModel, LightGBMModel, ensemble

MODEL_NAMES = ["baserate", "volboot", "lightgbm", "catboost", "ensemble"]


def fold_schedule(ts: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    c = cfg()["backtest"]
    first = pd.Timestamp(c["first_test_date"], tz="UTC")
    last = ts.max()
    block = timedelta(days=int(c["test_block_days"]))
    out, s = [], first
    while s <= last:
        out.append((s, min(s + block, last + timedelta(days=1))))
        s = s + block
    return out


def run_walkforward(ds: dict, bars: dict, quick: bool = False, log=print) -> dict:
    c = cfg()
    W, H, emb = int(c["wait_days"]), int(c["horizon_days"]), int(c["backtest"]["embargo_days"])
    gap = timedelta(days=W + H + emb)
    X, wide, long, XB = ds["X"], ds["wide"], ds["long"], ds["XB"]
    folds = fold_schedule(wide["ts"])
    min_train = int(c["backtest"]["min_train_days"])
    predA = {m: [] for m in MODEL_NAMES}
    predB = {m: [] for m in MODEL_NAMES}
    fold_log = []
    for k, (s, e) in enumerate(folds):
        tr = wide["ts"] <= s - gap
        te = (wide["ts"] >= s) & (wide["ts"] < e)
        if tr.sum() < min_train or te.sum() == 0:
            continue
        trB = long["ts"] <= s - gap
        teB = (long["ts"] >= s) & (long["ts"] < e)
        t0 = time.time()
        # 基准
        br = BaseRateModel().fit(wide[tr], long[trB])
        pa, pb = br.predict(X[te], XB[teB])
        predA["baserate"].append(pa.assign(fold=k)); predB["baserate"].append(pb.assign(fold=k))
        vb = VolBootstrapModel(n_paths=600 if quick else 1500).fit({s_: b["1d"] for s_, b in bars.items()}, s - timedelta(days=1))
        pa, pb = vb.predict(X[te], XB[teB])
        predA["volboot"].append(pa.assign(fold=k)); predB["volboot"].append(pb.assign(fold=k))
        # ML
        preds = {}
        for M in (LightGBMModel, CatBoostModel):
            m = M(quick=quick).fit(X[tr], wide[tr], XB[trB], long[trB])
            preds[m.name] = m.predict(X[te], XB[teB])
            predA[m.name].append(preds[m.name][0].assign(fold=k)); predB[m.name].append(preds[m.name][1].assign(fold=k))
        pa, pb = ensemble(preds)
        predA["ensemble"].append(pa.assign(fold=k)); predB["ensemble"].append(pb.assign(fold=k))
        fold_log.append({"fold": k, "test_start": s.date().isoformat(), "test_end": e.date().isoformat(), "train_rows": int(tr.sum()),
                         "train_last_ts": wide.loc[tr, "ts"].max().date().isoformat(), "test_rows": int(te.sum()), "seconds": round(time.time() - t0, 1)})
        log(f"折 {k}: 测试 {s.date()}→{e.date()} 训练 {int(tr.sum())} 行(截至 {wide.loc[tr, 'ts'].max().date()}) 测试 {int(te.sum())} 行 {time.time()-t0:.0f}s")
    outA, outB = {}, {}
    for m in MODEL_NAMES:
        if not predA[m]:
            continue
        a = pd.concat(predA[m]); b = pd.concat(predB[m])
        a = pd.concat([wide.loc[a.index, ["ts", "symbol", "close"]], a], axis=1)
        b = pd.concat([long.loc[b.index, ["ts", "symbol", "d_pct"]], b], axis=1)
        outA[m], outB[m] = a, b
    # ---- 因果校准 ----
    min_n = int(c["thresholds"]["min_calibration_samples"])
    for m in outA:
        a, b = outA[m], outB[m]
        yA = {"p_profit": wide.loc[a.index, "profit_A"].to_numpy(), "p_dd": wide.loc[a.index, "dd_A_flag"].to_numpy()}
        yB = {"p_fill": long.loc[b.index, "filled"].to_numpy(), "p_profit": long.loc[b.index, "profit_B"].to_numpy(), "p_dd": long.loc[b.index, "dd_B_flag"].to_numpy()}
        for col in ("p_profit", "p_dd"):
            a[col + "_cal"] = a[col].to_numpy(dtype=float)
        for col in ("p_fill", "p_profit", "p_dd"):
            b[col + "_cal"] = b[col].to_numpy(dtype=float)
        for k in sorted(a["fold"].unique()):
            prevA, curA = (a["fold"] < k).to_numpy(), (a["fold"] == k).to_numpy()
            prevB, curB = (b["fold"] < k).to_numpy(), (b["fold"] == k).to_numpy()
            for col in ("p_profit", "p_dd"):
                cal = fit_isotonic(a.loc[prevA, col].to_numpy(dtype=float), yA[col][prevA], min_n)
                a.loc[curA, col + "_cal"] = apply_cal(cal, a.loc[curA, col].to_numpy(dtype=float))
            for col in ("p_fill", "p_profit", "p_dd"):
                cal = fit_isotonic(b.loc[prevB, col].to_numpy(dtype=float), yB[col][prevB], min_n)
                b.loc[curB, col + "_cal"] = apply_cal(cal, b.loc[curB, col].to_numpy(dtype=float))
    return {"predA": outA, "predB": outB, "folds": fold_log}
