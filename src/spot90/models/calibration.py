"""概率校准与评估。校准器只用“更早折”的样本外预测拟合（因果），再作用于当前折。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def fit_isotonic(p: np.ndarray, y: np.ndarray, min_n: int = 300) -> IsotonicRegression | None:
    m = np.isfinite(p) & np.isfinite(y)
    if m.sum() < min_n or len(np.unique(y[m])) < 2:
        return None
    return IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p[m], y[m])


def apply_cal(cal: IsotonicRegression | None, p: np.ndarray) -> np.ndarray:
    if cal is None:
        return p
    out = np.asarray(p, dtype=float).copy()
    m = np.isfinite(out)
    out[m] = cal.predict(out[m])
    return out


def classification_report(p: np.ndarray, y: np.ndarray, base_rate: float | None = None, bins: int = 10) -> dict:
    m = np.isfinite(p) & np.isfinite(y)
    p, y = np.clip(p[m], 1e-6, 1 - 1e-6), y[m].astype(int)
    if len(y) < 30 or len(np.unique(y)) < 2:
        return {"n": int(len(y)), "available": False}
    br = brier_score_loss(y, p)
    base = base_rate if base_rate is not None else y.mean()
    br_base = brier_score_loss(y, np.full_like(p, base))
    edges = np.linspace(0, 1, bins + 1)
    rel = []
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        k = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if k.sum() > 0:
            rel.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(k.sum()), "pred_mean": float(p[k].mean()), "obs_rate": float(y[k].mean())})
            ece += k.sum() / len(p) * abs(p[k].mean() - y[k].mean())
    return {"n": int(len(y)), "available": True, "auc": float(roc_auc_score(y, p)), "brier": float(br), "brier_base": float(br_base),
            "brier_skill": float(1 - br / br_base) if br_base > 0 else 0.0, "log_loss": float(log_loss(y, p)), "ece": float(ece),
            "pred_mean": float(p.mean()), "obs_rate": float(y.mean()), "reliability": rel}


def quantile_report(q05: np.ndarray, q25: np.ndarray, q50: np.ndarray, q75: np.ndarray, q95: np.ndarray, y: np.ndarray) -> dict:
    m = np.isfinite(y) & np.isfinite(q50)
    if m.sum() < 30:
        return {"available": False}
    y = y[m]
    cov90 = float(((y >= q05[m]) & (y <= q95[m])).mean())
    cov50 = float(((y >= q25[m]) & (y <= q75[m])).mean())

    def pinball(q, pred):
        e = y - pred[m]
        return float(np.mean(np.maximum(q * e, (q - 1) * e)))
    return {"available": True, "n": int(m.sum()), "coverage_90": cov90, "coverage_50": cov50,
            "pinball_05": pinball(0.05, q05), "pinball_50": pinball(0.5, q50), "pinball_95": pinball(0.95, q95),
            "median_abs_err_q50": float(np.median(np.abs(y - q50[m])))}
