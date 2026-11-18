"""模型：概率归一、生肖求和、目标泄漏。"""
import numpy as np
import pandas as pd
import pytest

from marksix import zodiac
from marksix.backtest import rolling_predict
from marksix.context import build_ctx
from marksix.models import make_model, zodiac_probs

CONFIGS = [("M0", {}), ("M1", {"window": 50, "alpha": 1.0}), ("M2", {"rule": "N01", "kappa": 5.0}),
           ("M2", {"rule": "Z-ABC-ret", "kappa": 5.0}), ("M2", {"rule": "N05-0", "kappa": 5.0}),
           ("M2b", {"family": "方向×区间", "kappa": 100.0}), ("M2b", {"family": "生肖形态", "kappa": 100.0}),
           ("M3", {"C": 0.01, "refit_every": 100})]


def _ctx(s):
    dates = pd.date_range("2012-01-03", periods=len(s), freq="3D")
    df = pd.DataFrame({"draw_id": [f"{d.year}/{i+1:03d}" for i, d in enumerate(dates)], "date": dates, "special": s})
    return build_ctx(df, zodiac.ZodiacMap.builtin())


@pytest.mark.parametrize("name,params", CONFIGS, ids=lambda x: str(x))
def test_probabilities_normalized(name, params):
    rng = np.random.default_rng(0)
    ctx = _ctx(rng.integers(1, 50, 400))
    P, y = rolling_predict(make_model(name, params), ctx, 300, 400)
    assert P.shape == (100, 49) and np.allclose(P.sum(1), 1) and (P > 0).all()
    for i, t in enumerate(range(300, 400)):
        zp = zodiac_probs(P[i], ctx.zm[t])
        assert zp.shape == (12,) and np.isclose(zp.sum(), 1)


def test_uniform_zodiac_probs_match_counts():
    arr = zodiac.ZodiacMap.builtin().lookup("2024-06-01")
    zp = zodiac_probs(np.full(49, 1 / 49), arr)
    assert np.allclose(zp, zodiac.zodiac_counts(arr) / 49)


@pytest.mark.parametrize("name,params", CONFIGS, ids=lambda x: str(x))
def test_no_target_leakage(name, params):
    """把第 k 期及以后的结果全部改掉，目标期 ≤ k 的预测必须完全相同。"""
    rng = np.random.default_rng(1)
    s = rng.integers(1, 50, 400)
    k = 330
    s2 = s.copy()
    s2[k:] = (s[k:] % 49) + 1   # 每个未来结果都被改变
    P1, _ = rolling_predict(make_model(name, params), _ctx(s), 250, 400)
    P2, _ = rolling_predict(make_model(name, params), _ctx(s2), 250, 400)
    n_safe = k - 250 + 1        # 目标期 250..k（预测时只知道 ≤ k−1）
    assert np.array_equal(P1[:n_safe], P2[:n_safe])
    assert not np.array_equal(P1, P2) or name == "M0"


def test_m2_learns_planted_signal():
    """功效检查：重号概率被植入到 30% 时，M2(N01) 应明显优于均匀分布。"""
    rng = np.random.default_rng(2)
    s = [int(rng.integers(1, 50))]
    for _ in range(999):
        s.append(s[-1] if rng.random() < 0.3 else int(rng.integers(1, 50)))
    ctx = _ctx(np.array(s))
    P, y = rolling_predict(make_model("M2", {"rule": "N01", "kappa": 5.0}), ctx, 300, 1000)
    ll = -np.log(P[np.arange(len(y)), y - 1]).mean()
    assert ll < np.log(49) - 0.1
