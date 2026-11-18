"""预测模型。统一接口：

    model.prepare(ctx)          # 只计算因果特征（t 行只依赖 ≤t 的结果）
    model.predict(t) -> (49,)   # 预测第 t+1 期；只能使用结果下标 ≤ t 的数据

所有输出为 49 个号码的概率，总和为 1。生肖概率由号码概率按目标期映射求和得到。
防泄漏由测试保证：修改第 k 期及以后的特别号码，t < k 的预测必须完全不变。
"""
from __future__ import annotations

import json
import warnings

import numpy as np

from .context import Ctx
from .patterns import RULES, ZONE_OF, dir_of, shape_of, _lag

EPS = 1e-6
UNIFORM = np.full(49, 1 / 49)


def _normalize(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, None)
    return p / p.sum()


class Model:
    name = "base"
    params: dict = {}

    def prepare(self, ctx: Ctx):
        self.ctx = ctx

    def predict(self, t: int) -> np.ndarray:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name} {json.dumps(self.params, ensure_ascii=False)}"


class M0Uniform(Model):
    """M0：49 个号码均匀概率。"""
    name = "M0"

    def __init__(self):
        self.params = {}

    def predict(self, t):
        return UNIFORM.copy()


class M1Frequency(Model):
    """M1：固定窗口历史频率 + 加性平滑：p_j = (c_j + α)/(W + 49α)。"""
    name = "M1"

    def __init__(self, window: int, alpha: float):
        self.params = {"window": window, "alpha": alpha}

    def prepare(self, ctx):
        super().prepare(ctx)
        onehot = np.zeros((ctx.L + 1, 49))
        onehot[np.arange(1, ctx.L + 1), ctx.s - 1] = 1
        self.cum = onehot.cumsum(axis=0)   # cum[k] = 前 k 期计数

    def predict(self, t):
        W, a = self.params["window"], self.params["alpha"]
        lo = max(0, t + 1 - W)
        c = self.cum[t + 1] - self.cum[lo]
        return _normalize((c + a) / (c.sum() + 49 * a))


class M2PatternEvent(Model):
    """M2：单条登记形态的条件事件频率，向随机基准收缩。

    训练统计只用结果已知的触发（触发下标 ≤ t−1，其目标期 ≤ t）：
      ρ = (O + κ)/(E + κ)，κ 以“期望次数”为单位，未见形态时 ρ=1（即均匀分布）。
    若 t 期触发且事件集合为 A_t：P(A_t)=clip(ρ·|A_t|/49)，A_t 内部与外部分别均匀分配。
    只使用一条规则，不把重叠形态相乘。
    """
    name = "M2"

    def __init__(self, rule_id: str, kappa: float):
        self.params = {"rule": rule_id, "kappa": kappa}

    def prepare(self, ctx):
        super().prepare(ctx)
        rule = RULES[self.params["rule"]]
        self.cond, self.E = rule.compute(ctx)
        L = ctx.L
        p = self.E.sum(axis=1) / 49.0
        hit = np.zeros(L)
        idx = np.where(self.cond[:-1])[0]
        hit[idx] = self.E[idx, ctx.s[idx + 1] - 1]
        c = self.cond.astype(float)
        c[-1] = 0  # 最后一期结果未知
        self.p = p
        self.cumO = np.concatenate([[0], np.cumsum(hit * c)])
        self.cumE = np.concatenate([[0], np.cumsum(p * c)])

    def predict(self, t):
        if not self.cond[t]:
            return UNIFORM.copy()
        A = self.E[t]
        k = A.sum()
        if k == 0 or k == 49:
            return UNIFORM.copy()
        O, Ee = self.cumO[t], self.cumE[t]   # 触发下标 < t（目标期 ≤ t）
        kap = self.params["kappa"]
        rho = (O + kap) / (Ee + kap)
        pa = float(np.clip(rho * k / 49.0, 0.01, 0.99))
        out = np.where(A, pa / k, (1 - pa) / (49 - k))
        return _normalize(out)


STATE_FAMILIES = ["三期方向", "三期区间", "方向×区间", "生肖形态"]


def state_keys(ctx: Ctx, family: str) -> np.ndarray:
    """t 期的离散形态状态（−1 表示无法计算）。只依赖 ≤t 的结果。"""
    s = ctx.s
    a, b, c = _lag(s, 2, 1), _lag(s, 1, 1), s
    valid = np.arange(ctx.L) >= 2
    if family == "三期方向":
        k = dir_of(a, b, c)
    elif family == "三期区间":
        k = shape_of(ZONE_OF[a - 1], ZONE_OF[b - 1], ZONE_OF[c - 1])
    elif family == "方向×区间":
        k = dir_of(a, b, c) * 5 + shape_of(ZONE_OF[a - 1], ZONE_OF[b - 1], ZONE_OF[c - 1])
    elif family == "生肖形态":
        z = ctx.z
        k = shape_of(_lag(z, 2, -1), _lag(z, 1, -1), z)
        valid &= _lag(ctx.zok, 2, False) & _lag(ctx.zok, 1, False) & ctx.zok
    else:
        raise ValueError(family)
    return np.where(valid, k, -1)


class M2bStateFrequency(Model):
    """M2b：按形态状态统计下一期 49 号码频率，向均匀分布收缩：p=(c_kj+κ/49)/(n_k+κ)。未见状态→均匀。"""
    name = "M2b"

    def __init__(self, family: str, kappa: float):
        self.params = {"family": family, "kappa": kappa}

    def prepare(self, ctx):
        super().prepare(ctx)
        self.keys = state_keys(ctx, self.params["family"])
        self._t = -1
        self.counts: dict[int, np.ndarray] = {}

    def predict(self, t):
        # 增量累计：加入所有目标期 ≤ t 的样本（触发下标 ≤ t−1）
        if t < self._t:
            self.prepare(self.ctx)
        for u in range(self._t + 1, t):
            k = self.keys[u]
            if k >= 0:
                self.counts.setdefault(int(k), np.zeros(49))[self.ctx.s[u + 1] - 1] += 1
        self._t = max(self._t, t - 1)
        k = self.keys[t]
        if k < 0 or int(k) not in self.counts:
            return UNIFORM.copy()
        c = self.counts[int(k)]
        kap = self.params["kappa"]
        return _normalize((c + kap / 49) / (c.sum() + kap))


def m3_features(ctx: Ctx) -> np.ndarray:
    """M3 特征（t 行只依赖 ≤t）：上期号码独热、方向/区间/生肖形态独热、区间独热、近 100 期频率。"""
    L = ctx.L
    X = [np.eye(49)[ctx.s - 1]]
    for fam, n in [("三期方向", 5), ("三期区间", 5), ("生肖形态", 5)]:
        k = state_keys(ctx, fam)
        oh = np.zeros((L, n))
        oh[k >= 0, k[k >= 0]] = 1
        X.append(oh)
    X.append(np.eye(5)[ZONE_OF[ctx.s - 1]])
    onehot = np.zeros((L + 1, 49))
    onehot[np.arange(1, L + 1), ctx.s - 1] = 1
    cum = onehot.cumsum(axis=0)
    lo = np.maximum(0, np.arange(L) + 1 - 100)
    freq = (cum[np.arange(L) + 1] - cum[lo]) / 100.0 * 49  # 以均匀期望为 1 的尺度
    X.append(freq)
    return np.hstack(X)


class M3Logistic(Model):
    """M3：L2 正则多分类逻辑回归（可选对照）。每 refit_every 期只用已知结果重新拟合。"""
    name = "M3"

    def __init__(self, C: float, refit_every: int = 200):
        self.params = {"C": C, "refit_every": refit_every}

    def prepare(self, ctx):
        super().prepare(ctx)
        self.X = m3_features(ctx)
        self.fit_at = None
        self.clf = None

    def _fit(self, t):
        from sklearn.linear_model import LogisticRegression

        Xtr, ytr = self.X[:t], self.ctx.s[1:t + 1]   # 样本 u：特征 u，标签 u+1（≤t）
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.clf = LogisticRegression(C=self.params["C"], max_iter=300).fit(Xtr, ytr)
        self.fit_at = t

    def predict(self, t):
        if t < 50:
            return UNIFORM.copy()
        if self.fit_at is None or t - self.fit_at >= self.params["refit_every"] or t < self.fit_at:
            self._fit(t)
        pr = self.clf.predict_proba(self.X[t:t + 1])[0]
        out = np.full(49, EPS)
        out[self.clf.classes_ - 1] = pr
        return _normalize(out)


def make_model(name: str, params: dict) -> Model:
    if name == "M0":
        return M0Uniform()
    if name == "M1":
        return M1Frequency(int(params["window"]), float(params["alpha"]))
    if name == "M2":
        return M2PatternEvent(params["rule"], float(params["kappa"]))
    if name == "M2b":
        return M2bStateFrequency(params["family"], float(params["kappa"]))
    if name == "M3":
        return M3Logistic(float(params["C"]), int(params.get("refit_every", 200)))
    raise ValueError(name)


def zodiac_probs(p49: np.ndarray, zm_row: np.ndarray) -> np.ndarray:
    return np.bincount(zm_row, weights=p49, minlength=12)
