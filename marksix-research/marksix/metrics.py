"""评分指标。主要指标（预先固定）：log loss（自然对数，越小越好）。

辅助：Brier、Top-1/5/10（并列按随机打破并列取期望命中）、生肖 log loss / Brier、校准。
配对损失差：d_t = 模型 log loss − M0 log loss；均值置信区间用 Newey–West HAC 标准误
（考虑滚动窗口带来的序列相关），并给出移动分块自助法区间作为对照。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sst


def logloss(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(P[np.arange(len(y)), y - 1])


def brier(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y - 1] = 1
    return ((P - Y) ** 2).sum(axis=1)


def topk_hit(P: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """期望命中：严格更高者数 g、并列数 m（含自身），命中 = clip((k−g)/m, 0, 1)。"""
    pa = P[np.arange(len(y)), y - 1][:, None]
    g = (P > pa + 1e-12).sum(axis=1)
    m = (np.abs(P - pa) <= 1e-12).sum(axis=1)
    return np.clip((k - g) / m, 0, 1)


def calibration(P: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y - 1] = 1
    p, o = P.ravel(), Y.ravel()
    if np.ptp(p) < 1e-9:
        return pd.DataFrame({"分箱": ["全部"], "平均预测概率": [p.mean()], "实际频率": [o.mean()], "数量": [len(p)]})
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.any():
            rows.append({"分箱": b + 1, "平均预测概率": p[m].mean(), "实际频率": o[m].mean(), "数量": int(m.sum())})
    return pd.DataFrame(rows)


def ece(cal: pd.DataFrame) -> float:
    w = cal["数量"] / cal["数量"].sum()
    return float((w * (cal["平均预测概率"] - cal["实际频率"]).abs()).sum())


def nw_lag(n: int) -> int:
    return max(1, int(np.floor(4 * (n / 100) ** (2 / 9))))


def hac_mean_ci(d: np.ndarray, alpha: float = 0.05, lag: int | None = None) -> dict:
    d = np.asarray(d, float)
    n = len(d)
    if n < 2:
        return {"mean": float(d.mean()) if n else np.nan, "se": np.nan, "lo": np.nan, "hi": np.nan, "p": np.nan, "lag": 0}
    lag = lag or nw_lag(n)
    e = d - d.mean()
    v = e @ e / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        v += 2 * w * (e[k:] @ e[:-k]) / n
    se = np.sqrt(max(v, 0) / n)
    zq = sst.norm.ppf(1 - alpha / 2)
    stat = d.mean() / se if se > 0 else np.nan
    return {"mean": float(d.mean()), "se": float(se), "lo": float(d.mean() - zq * se),
            "hi": float(d.mean() + zq * se), "p": float(2 * sst.norm.sf(abs(stat))) if se > 0 else np.nan, "lag": lag}


def block_bootstrap_ci(d: np.ndarray, block: int = 10, n_boot: int = 2000, seed: int = 0, alpha: float = 0.05):
    d = np.asarray(d, float)
    n = len(d)
    if n < block * 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = d[idx].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def summarize(P: np.ndarray, y: np.ndarray, Zp: np.ndarray | None, zy: np.ndarray | None,
              Zbase: np.ndarray | None) -> dict:
    ll = logloss(P, y)
    out = {"n": int(len(y)), "logloss": float(ll.mean()), "logloss_uniform": float(np.log(49)),
           "brier": float(brier(P, y).mean()), "brier_uniform": float(1 - 1 / 49),
           "top1": float(topk_hit(P, y, 1).mean()), "top5": float(topk_hit(P, y, 5).mean()),
           "top10": float(topk_hit(P, y, 10).mean()), "top1_base": 1 / 49, "top5_base": 5 / 49, "top10_base": 10 / 49}
    cal = calibration(P, y)
    out["ece"] = ece(cal)
    if Zp is not None and len(zy):
        zl = -np.log(np.clip(Zp[np.arange(len(zy)), zy], 1e-12, None))
        out["zodiac_logloss"] = float(zl.mean())
        out["zodiac_logloss_base"] = float(-np.log(Zbase[np.arange(len(zy)), zy]).mean())
        Y = np.zeros_like(Zp)
        Y[np.arange(len(zy)), zy] = 1
        out["zodiac_brier"] = float(((Zp - Y) ** 2).sum(axis=1).mean())
        out["zodiac_brier_base"] = float(((Zbase - Y) ** 2).sum(axis=1).mean())
        out["n_zodiac"] = int(len(zy))
    return out
