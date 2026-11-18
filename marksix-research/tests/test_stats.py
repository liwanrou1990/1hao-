"""统计：精确 p 值、多重比较、maxT 模拟、指标。"""
import numpy as np
import pandas as pd
from scipy import stats as sst

from marksix import zodiac
from marksix.context import build_ctx
from marksix.metrics import hac_mean_ci, topk_hit
from marksix.stats import bh, holm, maxT_adjust, poibin_pvalue, rule_stats


def test_poibin_matches_binom():
    ps = np.full(200, 0.1)
    assert np.isclose(poibin_pvalue(30, ps), sst.binomtest(30, 200, 0.1).pvalue, rtol=1e-6)
    ps2 = np.r_[np.full(100, 0.1), np.full(100, 0.1 + 1e-9)]
    assert np.isclose(poibin_pvalue(30, ps2), poibin_pvalue(30, ps), rtol=1e-4)


def test_holm_bh():
    p = np.array([0.01, 0.04, 0.03, 0.5])
    assert np.allclose(holm(p), [0.04, 0.09, 0.09, 0.5])
    assert np.allclose(bh(p), [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.5])


def _ctx(s):
    dates = pd.date_range("2012-01-03", periods=len(s), freq="3D")
    df = pd.DataFrame({"draw_id": [f"{d.year}/{i+1:03d}" for i, d in enumerate(dates)], "date": dates, "special": s})
    return build_ctx(df, zodiac.ZodiacMap.builtin())


def test_maxT_detects_planted_signal_and_not_null():
    rng = np.random.default_rng(7)
    s = [int(rng.integers(1, 50))]
    for _ in range(999):
        s.append(s[-1] if rng.random() < 0.08 else int(rng.integers(1, 50)))
    adj, _ = maxT_adjust(_ctx(np.array(s)), 3, 1000, n_sims=200, seed=1)
    assert adj["N01"] < 0.05
    adj0, mx = maxT_adjust(_ctx(rng.integers(1, 50, 1000)), 3, 1000, n_sims=200, seed=1)
    assert min(adj0.values()) > 0.01   # 零假设下极少出现很小的族校正 p
    assert (mx > 2).mean() > 0.5         # 35 个检验的 max|z| 通常大于 2，说明需校正


def test_rule_stats_flags_small_samples():
    tab = rule_stats(_ctx(np.random.default_rng(0).integers(1, 50, 200)), 3, 200,
                     {"tiny": 30, "small": 100, "min_expected": 5})
    assert (tab.loc[tab["触发次数n"] < 30, "状态"] == "样本极小，不可解读").all()
    assert {"Holm校正p", "BH校正q"} <= set(tab.columns)


def test_topk_ties_give_random_baseline():
    P = np.full((5, 49), 1 / 49)
    y = np.array([1, 10, 20, 30, 49])
    for k in (1, 5, 10):
        assert np.allclose(topk_hit(P, y, k), k / 49)


def test_hac_ci_covers_zero_for_noise():
    d = np.random.default_rng(0).normal(0, 1, 2000)
    r = hac_mean_ci(d)
    assert r["lo"] < 0 < r["hi"]
