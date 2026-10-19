"""LightGBM 多周期模型：purged / embargo walk-forward、概率校准、阈值选择、置信区间。

核心约束：
  * 不允许仅用一天数据训练：min_train_days 默认 20。
  * 按交易日切分；训练集结束与测试集开始之间留 embargo_days（默认 1 天），避免重叠标签泄漏。
  * 阈值在训练窗口尾部的校准/验证切片上选择，样本外只做评估，不回头调参。
  * 若验证集上没有任何阈值能给出正期望，则该模型输出“不交易”。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ..costs.model import CostModel
from .features import build_features
from .labels import HORIZONS, build_labels

warnings.filterwarnings("ignore", category=UserWarning)


@dataclass
class HorizonModel:
    horizon: int
    booster: object
    regressor: object
    calibrator: IsotonicRegression | None
    threshold: float | None            # None = 不交易
    feature_names: list[str]
    val_metrics: dict = field(default_factory=dict)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.booster.predict(X[self.feature_names])
        return self.calibrator.predict(raw) if self.calibrator is not None else raw

    def predict_ev(self, X: pd.DataFrame) -> np.ndarray:
        return self.regressor.predict(X[self.feature_names])

    def decide(self, p: np.ndarray, ev: np.ndarray) -> np.ndarray:
        if self.threshold is None:
            return np.zeros(len(p), dtype=int)
        return ((p >= self.threshold) & (ev > 0)).astype(int)


@dataclass
class FoldReport:
    fold: int
    train_days: tuple[str, str]
    test_days: tuple[str, str]
    horizon: int
    n_train: int
    n_test: int
    auc: float | None
    brier: float | None
    logloss: float | None
    threshold: float | None
    n_signals: int
    mean_net_ret_when_trading: float | None
    ci95: tuple[float, float] | None
    reliability: list[dict] = field(default_factory=list)
    regimes_in_test: dict = field(default_factory=dict)


def _lgb():
    import lightgbm as lgb
    return lgb


def purged_walk_forward_splits(days: list[date], train_days: int, test_days: int, embargo_days: int = 1,
                               min_train_days: int = 20):
    if train_days < min_train_days:
        raise ValueError(f"训练窗口 {train_days} 天 < 最小要求 {min_train_days} 天：禁止用过短（如仅昨日）样本训练")
    i = 0
    while i + train_days + embargo_days + test_days <= len(days):
        tr = days[i:i + train_days]
        te = days[i + train_days + embargo_days:i + train_days + embargo_days + test_days]
        yield tr, te
        i += test_days


def _reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum() > 0:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "pred": float(p[m].mean()), "obs": float(y[m].mean())})
    return out


def _bootstrap_ci(x: np.ndarray, n: int = 1000, seed: int = 0) -> tuple[float, float] | None:
    if len(x) < 5:
        return None
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(x, size=len(x), replace=True).mean() for _ in range(n)])
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _choose_threshold(p: np.ndarray, r: np.ndarray, grid=None, min_n: int = 30) -> tuple[float | None, dict]:
    """在验证集上选择使“交易时平均净收益”最大且为正的概率阈值；候选为 p 的高分位数（适应低基准率）。"""
    if grid is None:
        qs = np.quantile(p, np.linspace(0.5, 0.99, 25)) if len(p) else np.array([])
        grid = np.unique(np.round(np.concatenate([qs, np.linspace(0.5, 0.8, 7)]), 4))
    best_t, best_ev = None, 0.0
    table = []
    for t in grid:
        m = p >= t
        if m.sum() < min_n:
            continue
        ev = float(r[m].mean())
        table.append({"threshold": float(t), "n": int(m.sum()), "mean_net_ret": ev})
        if ev > best_ev:
            best_t, best_ev = float(t), ev
    return best_t, {"threshold_table": table, "best_ev": best_ev}


def fit_horizon_model(X_tr: pd.DataFrame, y_tr: pd.Series, r_tr: pd.Series, X_val: pd.DataFrame, y_val: pd.Series,
                      r_val: pd.Series, horizon: int, seed: int = 42) -> HorizonModel:
    lgb = _lgb()
    params = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, seed=seed, num_threads=4)
    dtr = lgb.Dataset(X_tr, y_tr)
    booster = lgb.train(params, dtr, num_boost_round=200)
    reg = lgb.train({**params, "objective": "huber", "alpha": 0.9}, lgb.Dataset(X_tr, r_tr), num_boost_round=200)
    raw_val = booster.predict(X_val)
    cal = None
    if len(np.unique(y_val)) > 1 and len(y_val) >= 200:
        cal = IsotonicRegression(out_of_bounds="clip").fit(raw_val, y_val)
    p_val = cal.predict(raw_val) if cal is not None else raw_val
    thr, thr_info = _choose_threshold(p_val, r_val.to_numpy())
    vm = {"n_val": int(len(y_val)), "auc_val": float(roc_auc_score(y_val, p_val)) if len(np.unique(y_val)) > 1 else None,
          "brier_val": float(brier_score_loss(y_val, p_val)), **thr_info}
    return HorizonModel(horizon, booster, reg, cal, thr, list(X_tr.columns), vm)


@dataclass
class WalkForwardOutput:
    horizons: tuple[int, ...]
    folds: list[FoldReport]
    oos_predictions: pd.DataFrame        # 样本外逐bar预测（含 p, ev, decision, ret）
    final_models: dict[int, HorizonModel]
    data_is_simulated: bool
    note: str = ("所有指标均来自样本外折；阈值/校准在训练窗口内部验证切片上确定。"
                 "阈值为 None 表示验证集上不存在正期望阈值，模型输出不交易。")

    def summary(self) -> pd.DataFrame:
        rows = []
        for h in self.horizons:
            fs = [f for f in self.folds if f.horizon == h]
            oos = self.oos_predictions[self.oos_predictions["horizon"] == h]
            traded = oos[oos["decision"] == 1]
            rows.append({"horizon_min": h, "folds": len(fs),
                         "auc_oos_mean": float(np.nanmean([f.auc for f in fs if f.auc is not None])) if any(f.auc is not None for f in fs) else None,
                         "brier_oos_mean": float(np.nanmean([f.brier for f in fs if f.brier is not None])) if fs else None,
                         "n_oos_bars": int(len(oos)), "n_signals": int(len(traded)),
                         "mean_net_ret_when_trading": float(traded["ret"].mean()) if len(traded) else None,
                         "ci95": _bootstrap_ci(traded["ret"].to_numpy()) if len(traded) else None,
                         "hit_rate": float((traded["ret"] > 0).mean()) if len(traded) else None,
                         "has_edge": bool(len(traded) and traded["ret"].mean() > 0 and (_bootstrap_ci(traded["ret"].to_numpy()) or (0, 0))[0] > 0)})
        return pd.DataFrame(rows)


def walk_forward_train(bars: pd.DataFrame, cost: CostModel, capital_usd: float, train_days: int = 40, test_days: int = 5,
                       embargo_days: int = 1, horizons=HORIZONS, min_train_days: int = 20,
                       regime_fn=None) -> WalkForwardOutput:
    bars = bars.reset_index(drop=True)
    X = build_features(bars)
    Y = build_labels(bars, cost, capital_usd, horizons)
    day = pd.to_datetime(bars["ts_ny"]).dt.date
    days = sorted(day.unique())
    folds: list[FoldReport] = []
    oos_rows = []
    final_models: dict[int, HorizonModel] = {}
    k = 0
    for tr_days, te_days in purged_walk_forward_splits(days, train_days, test_days, embargo_days, min_train_days):
        k += 1
        n_val = max(3, int(len(tr_days) * 0.2))
        fit_days, val_days = tr_days[:-n_val], tr_days[-n_val:]
        m_fit, m_val, m_te = day.isin(fit_days), day.isin(val_days), day.isin(te_days)
        for h in horizons:
            y, r = Y[f"up_{h}"], Y[f"ret_{h}"]
            ok = X.notna().all(axis=1) & y.notna()
            fi, va, te = ok & m_fit, ok & m_val, ok & m_te
            if fi.sum() < 500 or va.sum() < 100 or te.sum() < 50:
                continue
            model = fit_horizon_model(X[fi], y[fi], r[fi], X[va], y[va], r[va], h)
            p = model.predict_proba(X[te])
            ev = model.predict_ev(X[te])
            dec = model.decide(p, ev)
            y_te, r_te = y[te].to_numpy(), r[te].to_numpy()
            traded = r_te[dec == 1]
            regimes = {}
            if regime_fn is not None:
                sym = str(bars["symbol"].iloc[0])
                regimes = pd.Series([regime_fn(sym, d) for d in te_days]).value_counts().to_dict()
            folds.append(FoldReport(k, (str(tr_days[0]), str(tr_days[-1])), (str(te_days[0]), str(te_days[-1])), h,
                                    int(fi.sum()), int(te.sum()),
                                    float(roc_auc_score(y_te, p)) if len(np.unique(y_te)) > 1 else None,
                                    float(brier_score_loss(y_te, p)), float(log_loss(y_te, np.clip(p, 1e-6, 1 - 1e-6))) if len(np.unique(y_te)) > 1 else None,
                                    model.threshold, int(dec.sum()), float(traded.mean()) if len(traded) else None,
                                    _bootstrap_ci(traded), _reliability(p, y_te), regimes))
            oos_rows.append(pd.DataFrame({"idx": X[te].index, "horizon": h, "p": p, "ev": ev, "decision": dec,
                                          "ret": r_te, "y": y_te, "fold": k}))
            final_models[h] = model
    oos = pd.concat(oos_rows, ignore_index=True) if oos_rows else pd.DataFrame(columns=["idx", "horizon", "p", "ev", "decision", "ret", "y", "fold"])
    sim = bool((bars["quality"].astype(int) & 128).any())
    return WalkForwardOutput(tuple(horizons), folds, oos, final_models, sim)


def explain_prediction(model: HorizonModel, X_row: pd.DataFrame, top: int = 5) -> list[dict]:
    """用 LightGBM 的 pred_contrib 给出单条预测的主要特征贡献（原始分数空间）。"""
    contrib = model.booster.predict(X_row[model.feature_names], pred_contrib=True)[0]
    names = model.feature_names + ["bias"]
    pairs = sorted(zip(names, contrib), key=lambda t: -abs(t[1]))[:top]
    return [{"feature": n, "contribution": float(v)} for n, v in pairs]
