"""形态统计检验与多重比较。

对每条规则，在指定期间（只取目标期落在该期间内的触发）计算：
  n 触发次数、O 事件实际发生次数、E=Σp_t 随机期望次数、效应 (O−E)/n、比率 O/E、
  z=(O−E)/sqrt(Σp_t(1−p_t))、精确双侧 p 值（泊松二项分布）、观测率的 Wilson 95% 区间。
多重比较：Holm、Benjamini–Hochberg，以及蒙特卡洛 maxT（每次模拟重新生成独立均匀的特别号码，
在同样日期和映射上重跑全部已登记规则，取 |z| 最大值，得到族错误率校正 p 值）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sst

from .context import Ctx
from .patterns import REGISTRY, Rule


def rule_arrays(rule: Rule, ctx: Ctx, target_lo: int, target_hi: int):
    """返回 (触发下标 t, 命中, 基准 p)。只包含目标期 t+1 ∈ [target_lo, target_hi) 的触发。"""
    cond, E = rule.compute(ctx)
    t = np.where(cond)[0]
    t = t[(t + 1 >= target_lo) & (t + 1 < target_hi) & (t + 1 < ctx.L)]
    p = E[t].sum(axis=1) / 49.0
    hit = E[t, ctx.s[t + 1] - 1]
    return t, hit, p


def poibin_pvalue(k: int, ps: np.ndarray) -> float:
    """泊松二项分布双侧精确 p 值：P(X 的概率 ≤ P(X=k))。"""
    if len(ps) == 0:
        return float("nan")
    if np.allclose(ps, ps[0]):
        pmf = sst.binom.pmf(np.arange(len(ps) + 1), len(ps), ps[0])
    else:
        pmf = np.zeros(len(ps) + 1)
        pmf[0] = 1.0
        for i, q in enumerate(ps):
            pmf[1:i + 2] = pmf[1:i + 2] * (1 - q) + pmf[0:i + 1] * q
            pmf[0] *= (1 - q)
    return float(min(1.0, pmf[pmf <= pmf[k] * (1 + 1e-7)].sum()))


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def sample_flag(n: int, E: float, cfg: dict) -> str:
    if n < cfg.get("tiny", 30) or E < cfg.get("min_expected", 5):
        return "样本极小，不可解读"
    if n < cfg.get("small", 100):
        return "样本不足"
    return "可计算"


def rule_stats(ctx: Ctx, target_lo: int, target_hi: int, small_cfg: dict, rules=None) -> pd.DataFrame:
    rows = []
    for r in rules or REGISTRY:
        if r.needs_zodiac and not ctx.zok.any():
            rows.append({"规则编号": r.rule_id, "名称": r.name, "状态": "生肖映射缺失，已停止"})
            continue
        t, hit, p = rule_arrays(r, ctx, target_lo, target_hi)
        n, O, E = len(t), int(hit.sum()), float(p.sum())
        V = float((p * (1 - p)).sum())
        z = (O - E) / np.sqrt(V) if V > 0 else float("nan")
        lo, hi = wilson(O, n)
        rows.append({
            "规则编号": r.rule_id, "类别": r.family, "名称": r.name, "触发次数n": n, "实际次数O": O,
            "随机期望E": round(E, 2), "实际率": O / n if n else np.nan, "基准率": E / n if n else np.nan,
            "效应(百分点)": 100 * (O - E) / n if n else np.nan,
            "效应95%下限": 100 * (lo - E / n) if n else np.nan, "效应95%上限": 100 * (hi - E / n) if n else np.nan,
            "O/E": O / E if E > 0 else np.nan, "z": z, "p值": poibin_pvalue(O, p) if n else np.nan,
            "状态": sample_flag(n, E, small_cfg),
        })
    df = pd.DataFrame(rows)
    if "p值" in df:
        ok = df["p值"].notna()
        df.loc[ok, "Holm校正p"] = holm(df.loc[ok, "p值"].to_numpy())
        df.loc[ok, "BH校正q"] = bh(df.loc[ok, "p值"].to_numpy())
    return df


def holm(p: np.ndarray) -> np.ndarray:
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * p[i])
        adj[i] = min(1.0, run)
    return adj


def bh(p: np.ndarray) -> np.ndarray:
    m = len(p)
    order = np.argsort(p)[::-1]
    adj = np.empty(m)
    run = 1.0
    for rank, i in enumerate(order):
        run = min(run, p[i] * m / (m - rank))
        adj[i] = run
    return adj


def _all_z(ctx: Ctx, lo: int, hi: int, rules) -> np.ndarray:
    out = []
    for r in rules:
        t, hit, p = rule_arrays(r, ctx, lo, hi)
        V = (p * (1 - p)).sum()
        out.append((hit.sum() - p.sum()) / np.sqrt(V) if V > 0 else np.nan)
    return np.array(out, dtype=float)


def maxT_adjust(ctx: Ctx, lo: int, hi: int, n_sims: int, seed: int, rules=None, progress=None):
    """蒙特卡洛 maxT：零假设为各期特别号码独立且在 1..49 上均匀。

    每次模拟替换整个特别号码序列（含 lo 之前的历史，因为条件依赖历史），重跑全部登记规则。
    返回 (校正 p 值数组, 模拟的 max|z| 分布)。
    """
    rules = [r for r in (rules or REGISTRY) if not (r.needs_zodiac and not ctx.zok.any())]
    z_obs = np.abs(_all_z(ctx, lo, hi, rules))
    rng = np.random.default_rng(seed)
    mx = np.empty(n_sims)
    for i in range(n_sims):
        sim = ctx.with_specials(rng.integers(1, 50, size=ctx.L))
        zs = np.abs(_all_z(sim, lo, hi, rules))
        mx[i] = np.nanmax(zs)
        if progress and i % 50 == 0:
            progress(i / n_sims)
    adj = np.array([(1 + (mx >= z).sum()) / (n_sims + 1) if np.isfinite(z) else np.nan for z in z_obs])
    return dict(zip([r.rule_id for r in rules], adj)), mx


def next_number_distribution(rule: Rule, ctx: Ctx, lo: int, hi: int) -> pd.DataFrame:
    t, _, _ = rule_arrays(rule, ctx, lo, hi)
    cnt = np.bincount(ctx.s[t + 1], minlength=50)[1:]
    n = len(t)
    return pd.DataFrame({"号码": np.arange(1, 50), "次数": cnt, "随机期望": n / 49.0})


def next_zodiac_distribution(rule: Rule, ctx: Ctx, lo: int, hi: int) -> pd.DataFrame | None:
    if not ctx.zok.any():
        return None
    from .zodiac import ZODIACS

    t, _, _ = rule_arrays(rule, ctx, lo, hi)
    t = t[ctx.zok[t + 1]]
    z = ctx.z[t + 1]
    cnt = np.bincount(z, minlength=12)
    exp = ctx.zc[t + 1].sum(axis=0) / 49.0   # 按目标期实际号码数量计算基准
    return pd.DataFrame({"生肖": ZODIACS, "次数": cnt, "随机期望": exp.round(2)})


def concrete_zodiac_triples(ctx: Ctx, lo: int, hi: int) -> pd.DataFrame:
    """具体生肖三期序列（描述性，不作为登记检验）。"""
    from .zodiac import ZODIACS

    z, ok = ctx.z, ctx.zok
    rows = []
    for t in range(2, ctx.L - 1):
        if not (lo <= t + 1 < hi) or not ok[t - 2:t + 2].all():
            continue
        rows.append({"三期生肖": "".join(ZODIACS[k] for k in z[t - 2:t + 1]), "下一期生肖": ZODIACS[z[t + 1]],
                     "下一期延续": z[t + 1] == z[t],
                     "延续基准": ctx.zc[t + 1, z[t]] / 49.0})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby("三期生肖").agg(次数=("下一期生肖", "size"), 延续次数=("下一期延续", "sum"),
                                 延续期望=("延续基准", "sum")).reset_index()
    return g.sort_values("次数", ascending=False)
