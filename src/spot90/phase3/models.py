"""多周期模型：LightGBM / CatBoost（目标 profit, mae10/15/20, 收益 Huber 均值 + 5 分位数）与 E 基准（趋势×波动条件频率）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg
from ..models.common import QUANTILES

TARGETS = ["profit", "mae10", "mae15", "mae20"]
OUT_COLS = [f"p_{t}" for t in TARGETS] + ["ret_mean"] + [f"ret_q{int(q*100):02d}" for q in QUANTILES]


class GBM:
    def __init__(self, kind: str, features: list[str], quick: bool = False, threads: int = 1):
        self.kind, self.features, self.quick, self.threads = kind, list(features), quick, threads
        self.models: dict = {}

    def _clf(self):
        if self.kind == "lightgbm":
            import lightgbm as lgb
            p = dict(cfg()["models"]["lightgbm"]); p["n_jobs"] = self.threads
            if self.quick: p["n_estimators"] = 150
            return lgb.LGBMClassifier(**p)
        from catboost import CatBoostClassifier
        p = dict(cfg()["models"]["catboost"]);
        if self.quick: p["iterations"] = 150
        return CatBoostClassifier(**p, allow_writing_files=False, thread_count=self.threads)

    def _reg(self, q: float | None = None):
        if self.kind == "lightgbm":
            import lightgbm as lgb
            p = dict(cfg()["models"]["lightgbm"]); p["n_jobs"] = self.threads
            if self.quick: p["n_estimators"] = 150
            p["objective"] = "huber" if q is None else "quantile"
            if q is not None: p["alpha"] = q
            return lgb.LGBMRegressor(**p)
        from catboost import CatBoostRegressor
        p = dict(cfg()["models"]["catboost"])
        if self.quick: p["iterations"] = 150
        loss = "Huber:delta=0.3" if q is None else "MultiQuantile:alpha=" + ",".join(str(x) for x in QUANTILES)
        return CatBoostRegressor(**p, loss_function=loss, allow_writing_files=False, thread_count=self.threads)

    def fit(self, X: pd.DataFrame, y: pd.DataFrame):
        F = X[self.features]
        for t in TARGETS:
            self.models[t] = self._clf().fit(F, y[t])
        self.models["ret"] = self._reg().fit(F, y["ret"])
        if self.kind == "lightgbm":
            for q in QUANTILES:
                self.models[f"q{q}"] = self._reg(q).fit(F, y["ret"])
        else:
            self.models["mq"] = self._reg(0.5).fit(F, y["ret"])
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        F = X[self.features]
        out = pd.DataFrame(index=X.index, columns=OUT_COLS, dtype=float)
        for t in TARGETS:
            out[f"p_{t}"] = self.models[t].predict_proba(F)[:, 1]
        out["ret_mean"] = self.models["ret"].predict(F)
        if self.kind == "lightgbm":
            qs = np.sort(np.column_stack([self.models[f"q{q}"].predict(F) for q in QUANTILES]), axis=1)
        else:
            qs = np.sort(np.asarray(self.models["mq"].predict(F)), axis=1)
        for k, q in enumerate(QUANTILES):
            out[f"ret_q{int(q*100):02d}"] = qs[:, k]
        return out

    def importance(self) -> pd.Series:
        m = self.models["profit"]
        g = m.booster_.feature_importance("gain") if self.kind == "lightgbm" else m.get_feature_importance()
        return pd.Series(g, index=self.features)


class TrendVolBaseline:
    """E 基准：按决策日可知的 4 个状态（价格在 MA200 上/下 × rv20 低/高于过去 365 天中位数）在训练窗内统计条件频率与收益分位数。"""
    name = "E_trendvol"

    @staticmethod
    def state(X: pd.DataFrame) -> pd.Series:
        up = (X["ma200_dist"] > 0).astype(int)
        rv_med = X.groupby("symbol")["rv20"].transform(lambda s: s.rolling(365, min_periods=120).median())
        lowv = (X["rv20"] < rv_med).astype(int)
        return up * 2 + lowv

    def fit(self, X: pd.DataFrame, y: pd.DataFrame):
        st = self.state(X)
        d = y.copy(); d["state"] = st.to_numpy(); d["symbol"] = X["symbol"].to_numpy()
        self.table = d.groupby(["symbol", "state"]).agg(**{f"p_{t}": (t, "mean") for t in TARGETS}, ret_mean=("ret", "mean"),
                                                        **{f"ret_q{int(q*100):02d}": ("ret", lambda s, q=q: s.quantile(q)) for q in QUANTILES})
        self.fallback = d.groupby("symbol").agg(**{f"p_{t}": (t, "mean") for t in TARGETS}, ret_mean=("ret", "mean"),
                                                **{f"ret_q{int(q*100):02d}": ("ret", lambda s, q=q: s.quantile(q)) for q in QUANTILES})
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        st = self.state(X)
        k = pd.DataFrame({"symbol": X["symbol"].to_numpy(), "state": st.to_numpy()}, index=X.index)
        out = k.merge(self.table, left_on=["symbol", "state"], right_index=True, how="left")
        fb = k.merge(self.fallback, left_on="symbol", right_index=True, how="left")
        out = out[OUT_COLS].fillna(fb[OUT_COLS])
        out.index = X.index
        return out


class BaseRate:
    name = "E_baserate"

    def fit(self, X, y):
        d = y.copy(); d["symbol"] = X["symbol"].to_numpy()
        self.t = d.groupby("symbol").agg(**{f"p_{t}": (t, "mean") for t in TARGETS}, ret_mean=("ret", "mean"),
                                         **{f"ret_q{int(q*100):02d}": ("ret", lambda s, q=q: s.quantile(q)) for q in QUANTILES})
        return self

    def predict(self, X):
        out = pd.DataFrame({"symbol": X["symbol"].to_numpy()}, index=X.index).merge(self.t, left_on="symbol", right_index=True, how="left")[OUT_COLS]
        out.index = X.index
        return out
