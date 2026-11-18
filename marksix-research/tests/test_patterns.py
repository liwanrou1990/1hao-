"""固定形态：定义正确性、因果性（无未来信息）、命中与基准一致。"""
import numpy as np
import pandas as pd
import pytest

from marksix import zodiac
from marksix.context import build_ctx
from marksix.patterns import REGISTRY, RULES, dir_of, registry_hash, shape_of
from marksix.stats import rule_arrays


def _ctx(specials, start="2015-03-03"):
    dates = pd.date_range(start, periods=len(specials), freq="3D")
    df = pd.DataFrame({"draw_id": [f"{d.year}/{i+1:03d}" for i, d in enumerate(dates)], "date": dates,
                       "special": specials})
    return build_ctx(df, zodiac.ZodiacMap.builtin())


def test_shapes():
    assert shape_of(1, 1, 1) == 0 and shape_of(1, 1, 2) == 1 and shape_of(1, 2, 1) == 2
    assert shape_of(1, 2, 2) == 3 and shape_of(1, 2, 3) == 4
    assert dir_of(1, 2, 3) == 0 and dir_of(3, 2, 1) == 1 and dir_of(1, 3, 2) == 2 and dir_of(3, 1, 2) == 3
    assert dir_of(1, 1, 2) == 4 and dir_of(1, 2, 2) == 4


@pytest.mark.parametrize("rid,seq,expect_hit,expect_k", [
    ("N01", [10, 10], True, 1),
    ("N02", [10, 20, 10], True, 1),       # 下一期＝前两期且不同于本期
    ("N03", [10, 11], True, 2),
    ("N03", [49, 48], True, 1),           # 边界只有一个邻号
    ("N04", [12, 42], True, 5),           # 尾数 2：2,12,22,32,42
    ("N04", [20, 40], True, 4),           # 尾数 0：10,20,30,40
    ("N05-0", [1, 5, 9, 30], True, 40),   # 连升后继续更大：>9 共 40 个
    ("N05-1", [30, 20, 10, 3], True, 9),
    ("N06-AAB", [1, 5, 15, 19], True, 10),
    ("N07", [10, 12, 14, 11], True, 5),   # 区间 [10,14]
])
def test_number_rules(rid, seq, expect_hit, expect_k):
    ctx = _ctx(seq)
    t = len(seq) - 2
    cond, E = RULES[rid].compute(ctx)
    assert cond[t]
    assert E[t].sum() == expect_k
    assert E[t, seq[-1] - 1] == expect_hit


def test_registry_is_fixed():
    assert len(REGISTRY) == 35 and len({r.rule_id for r in REGISTRY}) == 35
    assert all(r.horizon == 1 for r in REGISTRY)
    assert registry_hash() == registry_hash()


@pytest.mark.parametrize("rule", REGISTRY, ids=lambda r: r.rule_id)
def test_rules_are_causal(rule):
    """修改第 k 期及以后的特别号码，t < k 的条件与事件集合不变（t 行只用 ≤t 的结果）。"""
    rng = np.random.default_rng(3)
    s = rng.integers(1, 50, 400)
    ctx = _ctx(s)
    k = 250
    s2 = s.copy()
    s2[k:] = rng.integers(1, 50, 400 - k)
    c1, E1 = rule.compute(ctx)
    c2, E2 = rule.compute(ctx.with_specials(s2))
    assert (c1[:k] == c2[:k]).all() and (E1[:k] == E2[:k]).all()


@pytest.mark.parametrize("rule", REGISTRY, ids=lambda r: r.rule_id)
def test_hit_and_baseline_consistent(rule):
    rng = np.random.default_rng(5)
    ctx = _ctx(rng.integers(1, 50, 300))
    t, hit, p = rule_arrays(rule, ctx, 3, 300)
    _, E = rule.compute(ctx)
    assert ((t + 1) < 300).all() and (t + 1 >= 3).all()
    assert np.array_equal(hit, E[t, ctx.s[t + 1] - 1])
    assert np.allclose(p, E[t].sum(1) / 49) and ((p >= 0) & (p < 1)).all()
    assert not hit[p == 0].any()   # 不可能事件（如 49 号后“更大”）不会命中
