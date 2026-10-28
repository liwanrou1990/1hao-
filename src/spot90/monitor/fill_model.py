"""14 天限价单成交概率模型（保留自第一阶段，重新验证）。

模型：逻辑回归，特征 = [折价 d 相对 14 天波动的标准化距离 z = d / (rv20·√(14/365))、log(rv20)、d、趋势状态哑变量、30 日收益]。
      全部因果；Walk-Forward（同 14 折，训练集只含 ts ≤ 测试起点 − 14 天 − 10 天）。对照：第一阶段 LightGBM 成交模型（原始概率）。
验证：AUC / Brier / ECE / 可靠性表，按币种、按趋势状态、按波动分位、按年份分别给出，检查跨市场状态的稳定性。
重要：成交概率只回答“14 天内价格会不会碰到这个价位”，与成交后是否盈利无关；成交概率高的价位往往就是价格正在下跌的时候。"""
from __future__ import annotations

import json
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from ..backtest.walkforward import fold_schedule
from ..config import cfg, model_dir, report_dir
from ..labels import build_labels
from .rules import STATES, enrich

FEATS = ["z", "log_rv20", "d_pct", "ret_30d"] + [f"st_{s}" for s in STATES]


def _features(enriched: pd.DataFrame, d_pct: np.ndarray) -> pd.DataFrame:
    e = enriched
    sig14 = e["rv20"].to_numpy() * np.sqrt(14 / 365)
    f = pd.DataFrame({"z": (d_pct / 100) / np.where(sig14 > 0, sig14, np.nan), "log_rv20": np.log(e["rv20"].to_numpy()), "d_pct": d_pct, "ret_30d": e["ret_30d"].to_numpy()})
    for s in STATES:
        f[f"st_{s}"] = (e["state"].to_numpy() == s).astype(float)
    return f.replace([np.inf, -np.inf], np.nan)


class FillModel:
    def __init__(self):
        self.lr = LogisticRegression(C=1.0, max_iter=500)
        self.med = None

    def fit(self, F: pd.DataFrame, y: np.ndarray):
        self.med = F.median()
        X = F.fillna(self.med)
        self.lr.fit(X[FEATS], y)
        return self

    def predict(self, F: pd.DataFrame) -> np.ndarray:
        return self.lr.predict_proba(F.fillna(self.med)[FEATS])[:, 1]


def dataset(bars: dict) -> pd.DataFrame:
    rows = []
    for sym, b in bars.items():
        e = enrich(b["1d"])
        _, long = build_labels(b["1d"])
        m = long.merge(e[["ts", "rv20", "ret_30d", "state", "rv20_pct365"]], on="ts", how="left")
        F = _features(m, m["d_pct"].to_numpy())
        m = m.reset_index(drop=True)
        m = pd.concat([m.drop(columns=[c for c in F.columns if c in m.columns]), F], axis=1)
        rows.append(m)
    return pd.concat(rows, ignore_index=True)


def _report(p: np.ndarray, y: np.ndarray) -> dict:
    m = np.isfinite(p) & np.isfinite(y)
    p, y = np.clip(p[m], 1e-6, 1 - 1e-6), y[m]
    if len(y) < 50 or len(np.unique(y)) < 2:
        return {"n": int(len(y))}
    bins = np.linspace(0, 1, 11); e = 0.0; rel = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        k = (p >= lo) & (p < hi) if hi < 1 else (p >= lo)
        if k.any():
            e += k.mean() * abs(p[k].mean() - y[k].mean()); rel.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(k.sum()), "pred": float(p[k].mean()), "obs": float(y[k].mean())})
    return {"n": int(len(y)), "auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)), "ece": float(e), "pred_mean": float(p.mean()), "obs_rate": float(y.mean()), "reliability": rel}


def validate(bars: dict) -> dict:
    ds = dataset(bars)
    ds = ds[ds["z"].notna()].reset_index(drop=True)
    folds = fold_schedule(ds["ts"])
    gap = timedelta(days=14 + 10)
    ds["p_lr"] = np.nan
    for k, (s, e) in enumerate(folds):
        tr = ds["ts"] <= s - gap; te = (ds["ts"] >= s) & (ds["ts"] < e)
        if tr.sum() < 1000 or te.sum() == 0:
            continue
        m = FillModel().fit(ds.loc[tr, FEATS], ds.loc[tr, "filled"].to_numpy())
        ds.loc[te, "p_lr"] = m.predict(ds.loc[te, FEATS])
        ds.loc[te, "fold"] = k
    oos = ds[ds["p_lr"].notna()].copy()
    # 对照：第一阶段 LightGBM 成交模型（原始概率，同一日期）
    lg = report_dir() / "backtest" / "predB_lightgbm.parquet"
    if lg.exists():
        pb = pd.read_parquet(lg); pb["ts"] = pd.to_datetime(pb["ts"], utc=True)
        oos = oos.merge(pb[["ts", "symbol", "d_pct", "p_fill"]].rename(columns={"p_fill": "p_lgbm"}), on=["ts", "symbol", "d_pct"], how="left")
    oos["vol_bucket"] = pd.cut(oos["rv20_pct365"], [-0.01, 0.33, 0.66, 1.01], labels=["低波动", "中波动", "高波动"]).astype(str)
    oos["year"] = oos["ts"].dt.year
    y = oos["filled"].to_numpy(float)
    res = {"n_oos": int(len(oos)), "oos_range": [str(oos["ts"].min().date()), str(oos["ts"].max().date())], "overall": {}, "by_symbol": {}, "by_state": {}, "by_vol": {}, "by_year": {}, "by_d": {}}
    for name, col in (("logistic", "p_lr"), ("lightgbm_phase1", "p_lgbm")):
        if col not in oos or oos[col].isna().all():
            continue
        res["overall"][name] = _report(oos[col].to_numpy(float), y)
        for key, gcol in (("by_symbol", "symbol"), ("by_state", "state"), ("by_vol", "vol_bucket"), ("by_year", "year"), ("by_d", "d_pct")):
            res[key][name] = {}
            for g, sub in oos.groupby(gcol):
                r = _report(sub[col].to_numpy(float), sub["filled"].to_numpy(float)); r.pop("reliability", None)
                res[key][name][str(g)] = r
    # 成交 ≠ 盈利：按折价给出 成交率 与 成交后盈利率、成交后 90 天收益均值（历史事实）
    g = oos.groupby("d_pct")
    res["fill_vs_profit"] = {str(d): {"real_fill_rate": float(x["filled"].mean()), "profit_rate_given_fill": float(x.loc[x.filled == 1, "profit_B"].mean()),
                                      "mean_ret_given_fill": float(x.loc[x.filled == 1, "ret_B"].mean()), "mean_ret_buy_now_same_days": float(x["ret_A_counterfactual"].mean())} for d, x in g}
    # 最终模型：全部数据
    final = FillModel().fit(ds[FEATS], ds["filled"].to_numpy())
    joblib.dump({"model": final, "feats": FEATS, "trained_rows": int(len(ds)), "last_ts": str(ds["ts"].max().date())}, model_dir() / "fill_model_logistic.joblib")
    res["coefficients"] = dict(zip(FEATS, [float(c) for c in final.lr.coef_[0]])); res["intercept"] = float(final.lr.intercept_[0])
    out = report_dir() / "monitor"; out.mkdir(parents=True, exist_ok=True)
    (out / "fill_model_validation.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=float))
    return res


def predict_table(enriched_row: pd.Series, grid=(1, 2, 3, 5, 8, 10)) -> list[dict]:
    p = model_dir() / "fill_model_logistic.joblib"
    if not p.exists():
        return []
    pack = joblib.load(p)
    e = pd.DataFrame([enriched_row] * len(grid)).reset_index(drop=True)
    F = _features(e, np.array(grid, dtype=float))
    probs = pack["model"].predict(F)
    return [{"折价%": d, "限价": float(enriched_row["close"]) * (1 - d / 100), "14天成交概率": float(pr)} for d, pr in zip(grid, probs)]
