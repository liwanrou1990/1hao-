"""模型 A：LightGBM；模型 B：CatBoost。相同接口、独立训练、独立评估。
A 目标：profit_A(二分类)、dd_A_flag(二分类)、ret_A(均值回归 + 分位数回归)。
B 目标（按 (决策日, d) 展开，d_pct 作为特征）：filled、profit_B|filled、dd_B_flag|filled、ret_B|filled(均值 + 中位数)。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg
from ..features import FEATURES
from .common import A_COLS, B_COLS, QUANTILES

B_FEATURES = FEATURES + ["d_pct"]
DD_EXTRA = ("mae10_A", "mae20_A")


def _lgb_params(extra: dict | None = None) -> dict:
    p = dict(cfg()["models"]["lightgbm"])
    p.update(extra or {})
    return p


def _cb_params(extra: dict | None = None) -> dict:
    p = dict(cfg()["models"]["catboost"])
    p.update(extra or {})
    return p


class LightGBMModel:
    name = "lightgbm"

    def __init__(self, quick: bool = False, features: list[str] | None = None):
        self.quick = quick
        self.models: dict = {}
        self.features = list(features) if features is not None else list(FEATURES)
        self.b_features = self.features + ["d_pct"]

    def _clf(self):
        import lightgbm as lgb
        p = _lgb_params({"n_estimators": 150} if self.quick else None)
        return lgb.LGBMClassifier(**p)

    def _reg(self, objective: str = "l2", alpha: float | None = None):
        import lightgbm as lgb
        p = _lgb_params({"n_estimators": 150} if self.quick else None)
        p["objective"] = objective
        if alpha is not None:
            p["alpha"] = alpha
        return lgb.LGBMRegressor(**p)

    def fit(self, X: pd.DataFrame, wide: pd.DataFrame, XB: pd.DataFrame, long: pd.DataFrame) -> "LightGBMModel":
        F = X[self.features]
        self.models["A_profit"] = self._clf().fit(F, wide["profit_A"])
        self.models["A_dd"] = self._clf().fit(F, wide["dd_A_flag"])
        for t in DD_EXTRA:
            if t in wide:
                self.models[f"A_{t}"] = self._clf().fit(F, wide[t])
        self.models["A_ret"] = self._reg("huber").fit(F, wide["ret_A"])
        for q in QUANTILES:
            self.models[f"A_q{q}"] = self._reg("quantile", q).fit(F, wide["ret_A"])
        FB = XB[self.b_features]
        self.models["B_fill"] = self._clf().fit(FB, long["filled"])
        f = long["filled"] == 1
        self.models["B_profit"] = self._clf().fit(FB[f], long.loc[f, "profit_B"].astype(int))
        self.models["B_dd"] = self._clf().fit(FB[f], long.loc[f, "dd_B_flag"].astype(int))
        self.models["B_ret"] = self._reg("huber").fit(FB[f], long.loc[f, "ret_B"])
        self.models["B_q0.5"] = self._reg("quantile", 0.5).fit(FB[f], long.loc[f, "ret_B"])
        return self

    def predict(self, X: pd.DataFrame, XB: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        F, FB = X[self.features], XB[self.b_features]
        pa = pd.DataFrame(index=X.index, columns=A_COLS, dtype=float)
        pa["p_profit"] = self.models["A_profit"].predict_proba(F)[:, 1]
        pa["p_dd"] = self.models["A_dd"].predict_proba(F)[:, 1]
        for t in DD_EXTRA:
            if f"A_{t}" in self.models:
                pa[f"p_{t}"] = self.models[f"A_{t}"].predict_proba(F)[:, 1]
        pa["ret_mean"] = self.models["A_ret"].predict(F)
        qs = np.column_stack([self.models[f"A_q{q}"].predict(F) for q in QUANTILES])
        qs = np.sort(qs, axis=1)                                   # 分位数交叉修正
        for k, q in enumerate(QUANTILES):
            pa[f"ret_q{int(q*100):02d}"] = qs[:, k]
        pb = pd.DataFrame(index=XB.index, columns=B_COLS, dtype=float)
        pb["p_fill"] = self.models["B_fill"].predict_proba(FB)[:, 1]
        pb["p_profit"] = self.models["B_profit"].predict_proba(FB)[:, 1]
        pb["p_dd"] = self.models["B_dd"].predict_proba(FB)[:, 1]
        pb["ret_mean"] = self.models["B_ret"].predict(FB)
        pb["ret_q50"] = self.models["B_q0.5"].predict(FB)
        return pa, pb

    def feature_importance(self) -> pd.DataFrame:
        m = self.models["A_profit"]
        return pd.DataFrame({"feature": self.features, "gain": m.booster_.feature_importance("gain")}).sort_values("gain", ascending=False)


class CatBoostModel:
    name = "catboost"

    def __init__(self, quick: bool = False, features: list[str] | None = None):
        self.quick = quick
        self.models: dict = {}
        self.features = list(features) if features is not None else list(FEATURES)
        self.b_features = self.features + ["d_pct"]

    def _clf(self):
        from catboost import CatBoostClassifier
        p = _cb_params({"iterations": 150} if self.quick else None)
        return CatBoostClassifier(**p, allow_writing_files=False, thread_count=4)

    def _reg(self, loss: str = "RMSE"):
        from catboost import CatBoostRegressor
        p = _cb_params({"iterations": 150} if self.quick else None)
        return CatBoostRegressor(**p, loss_function=loss, allow_writing_files=False, thread_count=4)

    def fit(self, X: pd.DataFrame, wide: pd.DataFrame, XB: pd.DataFrame, long: pd.DataFrame) -> "CatBoostModel":
        F = X[self.features]
        self.models["A_profit"] = self._clf().fit(F, wide["profit_A"])
        self.models["A_dd"] = self._clf().fit(F, wide["dd_A_flag"])
        for t in DD_EXTRA:
            if t in wide:
                self.models[f"A_{t}"] = self._clf().fit(F, wide[t])
        self.models["A_ret"] = self._reg("Huber:delta=0.3").fit(F, wide["ret_A"])
        self.models["A_q"] = self._reg("MultiQuantile:alpha=" + ",".join(str(q) for q in QUANTILES)).fit(F, wide["ret_A"])
        FB = XB[self.b_features]
        self.models["B_fill"] = self._clf().fit(FB, long["filled"])
        f = long["filled"] == 1
        self.models["B_profit"] = self._clf().fit(FB[f], long.loc[f, "profit_B"].astype(int))
        self.models["B_dd"] = self._clf().fit(FB[f], long.loc[f, "dd_B_flag"].astype(int))
        self.models["B_ret"] = self._reg("Huber:delta=0.3").fit(FB[f], long.loc[f, "ret_B"])
        self.models["B_q0.5"] = self._reg("Quantile:alpha=0.5").fit(FB[f], long.loc[f, "ret_B"])
        return self

    def predict(self, X: pd.DataFrame, XB: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        F, FB = X[self.features], XB[self.b_features]
        pa = pd.DataFrame(index=X.index, columns=A_COLS, dtype=float)
        pa["p_profit"] = self.models["A_profit"].predict_proba(F)[:, 1]
        pa["p_dd"] = self.models["A_dd"].predict_proba(F)[:, 1]
        for t in DD_EXTRA:
            if f"A_{t}" in self.models:
                pa[f"p_{t}"] = self.models[f"A_{t}"].predict_proba(F)[:, 1]
        pa["ret_mean"] = self.models["A_ret"].predict(F)
        qs = np.sort(np.asarray(self.models["A_q"].predict(F)), axis=1)
        for k, q in enumerate(QUANTILES):
            pa[f"ret_q{int(q*100):02d}"] = qs[:, k]
        pb = pd.DataFrame(index=XB.index, columns=B_COLS, dtype=float)
        pb["p_fill"] = self.models["B_fill"].predict_proba(FB)[:, 1]
        pb["p_profit"] = self.models["B_profit"].predict_proba(FB)[:, 1]
        pb["p_dd"] = self.models["B_dd"].predict_proba(FB)[:, 1]
        pb["ret_mean"] = self.models["B_ret"].predict(FB)
        pb["ret_q50"] = self.models["B_q0.5"].predict(FB)
        return pa, pb

    def feature_importance(self) -> pd.DataFrame:
        m = self.models["A_profit"]
        return pd.DataFrame({"feature": self.features, "gain": m.get_feature_importance()}).sort_values("gain", ascending=False)


def ensemble(preds: dict[str, tuple[pd.DataFrame, pd.DataFrame]], members: tuple[str, ...] = ("lightgbm", "catboost")) -> tuple[pd.DataFrame, pd.DataFrame]:
    pas = [preds[m][0] for m in members if m in preds]
    pbs = [preds[m][1] for m in members if m in preds]
    return sum(pas) / len(pas), sum(pbs) / len(pbs)
