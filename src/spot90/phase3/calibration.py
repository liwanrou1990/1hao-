"""Platt（逻辑回归）校准：2 个参数，比等渗回归稳定；只用更早折的样本外预测拟合。"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


class Platt:
    def __init__(self):
        self.lr = None

    def fit(self, p, y, min_n: int = 300):
        m = np.isfinite(p) & np.isfinite(y)
        if m.sum() < min_n or len(np.unique(y[m])) < 2:
            return None
        self.lr = LogisticRegression(C=1.0).fit(_logit(p[m]).reshape(-1, 1), y[m].astype(int))
        return self

    def predict(self, p):
        if self.lr is None:
            return np.asarray(p, float)
        out = np.asarray(p, float).copy(); m = np.isfinite(out)
        out[m] = self.lr.predict_proba(_logit(out[m]).reshape(-1, 1))[:, 1]
        return out
