"""固定形态登记表（预先登记，禁止事后修改定义）。

每条规则：
  触发时点：第 t 期开奖结果公布后；
  条件：只依赖第 t 期及更早的特别号码（生肖规则另需对应各期映射）；
  预测对象：第 t+1 期特别号码（或其生肖）；观察期限：固定 1 期，不得切换；
  事件：第 t+1 期特别号码落入事件集合 A_t（A_t 只由 t 期及以前的信息和 t+1 期的映射决定）；
  基准：随机期望 = |A_t| / 49（生肖事件按 t+1 期映射中对应号码数量计算，而非 1/12）。

每条规则实现为 compute(ctx) → (cond[L], E[L,49])：cond[t] 为 t 期是否触发，E[t] 为事件集合。
重叠特征（如“重号”与“同尾”）作为独立候选登记，但不得把它们当作独立证据相乘。
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .context import Ctx

REGISTERED_AT = "2026-11-18"  # 登记日期：在查看任何真实数据结果之前登记

NUMS = np.arange(1, 50)
ZONE_OF = np.array([0] * 10 + [1] * 10 + [2] * 10 + [3] * 10 + [4] * 9)  # 号码 n → ZONE_OF[n-1]
ZONE_NAMES = ["1—10", "11—20", "21—30", "31—40", "41—49"]
SHAPES = ["AAA", "AAB", "ABA", "ABB", "ABC"]
DIRS = ["连升", "连降", "先升后降", "先降后升", "含相等"]


def shape_of(a, b, c) -> np.ndarray:
    """抽象形态：字母只表示相同或不同。返回 SHAPES 下标。"""
    a, b, c = map(np.asarray, (a, b, c))
    out = np.full(a.shape, 4)
    out[(a == b) & (b == c)] = 0
    out[(a == b) & (b != c)] = 1
    out[(a == c) & (a != b)] = 2
    out[(b == c) & (a != b)] = 3
    return out


def dir_of(a, b, c) -> np.ndarray:
    d1, d2 = np.sign(np.asarray(b) - a), np.sign(np.asarray(c) - b)
    out = np.full(d1.shape, 4)
    out[(d1 > 0) & (d2 > 0)] = 0
    out[(d1 < 0) & (d2 < 0)] = 1
    out[(d1 > 0) & (d2 < 0)] = 2
    out[(d1 < 0) & (d2 > 0)] = 3
    return out


def _lag(x: np.ndarray, k: int, fill) -> np.ndarray:
    """x 向后移 k 期：返回 y[t] = x[t-k]。"""
    y = np.empty_like(x)
    if k == 0:
        return x.copy()
    y[:k] = fill
    y[k:] = x[:-k]
    return y


def _lead_map(ctx: Ctx):
    """t+1 期的映射（zm, zok），最后一期之后无映射。"""
    L = ctx.L
    zm1 = np.full((L, 49), -1, dtype=int)
    zok1 = np.zeros(L, dtype=bool)
    zm1[:-1], zok1[:-1] = ctx.zm[1:], ctx.zok[1:]
    return zm1, zok1


def _hist(ctx: Ctx, k: int) -> np.ndarray:
    """t 期是否有至少 k 期历史（含本期）。"""
    return np.arange(ctx.L) >= k - 1


# ---------------- 生肖形态 ----------------

def _zodiac_rule(shape: int, event: str) -> Callable:
    def compute(ctx: Ctx):
        z = ctx.z
        a, b, c = _lag(z, 2, -1), _lag(z, 1, -1), z
        zok_hist = _lag(ctx.zok, 2, False) & _lag(ctx.zok, 1, False) & ctx.zok
        zm1, zok1 = _lead_map(ctx)
        cond = _hist(ctx, 3) & zok_hist & zok1 & (shape_of(a, b, c) == shape)
        a, b, c = np.where(cond, a, 0), np.where(cond, b, 0), np.where(cond, c, 0)
        if event == "cont":
            E = zm1 == c[:, None]
        elif event == "ret":   # 返回较早生肖：{a,b} 中不同于末期的生肖
            E = ((zm1 == a[:, None]) & (a != c)[:, None]) | ((zm1 == b[:, None]) & (b != c)[:, None])
        else:                  # new：前三期未出现的生肖
            E = (zm1 != a[:, None]) & (zm1 != b[:, None]) & (zm1 != c[:, None])
        E &= cond[:, None]
        return cond, E
    return compute


# ---------------- 号码形态 ----------------

def _s3(ctx):
    s = ctx.s
    return _lag(s, 2, 0), _lag(s, 1, 0), s


def r_repeat(ctx):
    cond = _hist(ctx, 1)
    E = NUMS[None, :] == ctx.s[:, None]
    return cond, E


def r_repeat_after_repeat(ctx):
    a, b, c = _s3(ctx)
    cond = _hist(ctx, 2) & (b == c)
    return cond, (NUMS[None, :] == c[:, None]) & cond[:, None]


def r_skip_repeat(ctx):
    a, b, c = _s3(ctx)
    cond = _hist(ctx, 2) & (b != c)
    return cond, (NUMS[None, :] == b[:, None]) & cond[:, None]


def r_neighbor(ctx):
    E = np.abs(NUMS[None, :] - ctx.s[:, None]) == 1
    return _hist(ctx, 1), E


def r_same_tail(ctx):
    E = (NUMS[None, :] % 10) == (ctx.s[:, None] % 10)
    return _hist(ctx, 1), E


def _dir_rule(d: int) -> Callable:
    def compute(ctx):
        a, b, c = _s3(ctx)
        cond = _hist(ctx, 3) & (dir_of(a, b, c) == d)
        last = np.sign(c - b)
        if d == 4:  # 含相等：事件为下一期与本期重号
            E = NUMS[None, :] == c[:, None]
        else:       # 延续最近一步方向
            E = np.where((last > 0)[:, None], NUMS[None, :] > c[:, None], NUMS[None, :] < c[:, None])
        return cond, E & cond[:, None]
    return compute


def _zone_rule(shape: int) -> Callable:
    def compute(ctx):
        a, b, c = _s3(ctx)
        za, zb, zc_ = ZONE_OF[a - 1], ZONE_OF[b - 1], ZONE_OF[c - 1]
        cond = _hist(ctx, 3) & (shape_of(za, zb, zc_) == shape)
        E = ZONE_OF[None, :] == zc_[:, None]
        return cond, E & cond[:, None]
    return compute


def r_cluster(ctx):
    a, b, c = _s3(ctx)
    lo, hi = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
    cond = _hist(ctx, 3) & (hi - lo <= 5)
    E = (NUMS[None, :] >= lo[:, None]) & (NUMS[None, :] <= hi[:, None])
    return cond, E & cond[:, None]


# ---------------- 联合形态 ----------------

def r_zodiac_repeat_number_diff(ctx):
    z, s = ctx.z, ctx.s
    zm1, zok1 = _lead_map(ctx)
    cond = (_hist(ctx, 2) & _lag(ctx.zok, 1, False) & ctx.zok & zok1
            & (_lag(z, 1, -2) == z) & (_lag(s, 1, 0) != s))
    E = (zm1 == z[:, None]) & cond[:, None]
    return cond, E


def _aba_dir_rule(d: int) -> Callable:
    base = _zodiac_rule(2, "cont")

    def compute(ctx):
        cond0, E0 = base(ctx)
        a, b, c = _s3(ctx)
        cond = cond0 & (dir_of(a, b, c) == d)
        return cond, E0 & cond[:, None]
    return compute


@dataclass(frozen=True)
class Rule:
    rule_id: str
    family: str
    name: str
    condition: str
    event: str
    needs_zodiac: bool
    compute: Callable
    trigger: str = "第 t 期开奖后"
    target: str = "第 t+1 期特别号码"
    horizon: int = 1


def _build_registry() -> list[Rule]:
    R = []
    ev_desc = {"cont": "下一期生肖＝末期生肖（延续）", "ret": "下一期生肖∈前两期中不同于末期的生肖（返回较早生肖）",
               "new": "下一期生肖不在前三期生肖之中（新生肖）"}
    for si, sh in enumerate(SHAPES):
        for ev in ["cont", "ret", "new"]:
            if sh == "AAA" and ev == "ret":
                continue  # AAA 不存在“较早且不同”的生肖，规则不适用
            R.append(Rule(f"Z-{sh}-{ev}", "生肖三期形态", f"生肖{sh}→{ev_desc[ev][:10]}",
                          f"最近三期特别号码生肖形态为 {sh}", ev_desc[ev], True, _zodiac_rule(si, ev)))
    R += [
        Rule("N01", "号码关系", "重号", "无（每期触发）", "下一期＝本期", False, r_repeat),
        Rule("N01c", "号码关系", "重号后再重号", "本期＝上期", "下一期＝本期", False, r_repeat_after_repeat),
        Rule("N02", "号码关系", "隔期重号", "本期≠上期", "下一期＝上期（即前两期），且不同于本期", False, r_skip_repeat),
        Rule("N03", "号码关系", "邻号", "无（每期触发）", "|下一期－本期|＝1", False, r_neighbor),
        Rule("N04", "号码关系", "同尾", "无（每期触发）", "下一期个位数＝本期个位数（含相同号码）", False, r_same_tail),
    ]
    for di, dn in enumerate(DIRS):
        ev = "下一期与本期重号" if di == 4 else "下一期延续最近一步方向（升则更大，降则更小）"
        R.append(Rule(f"N05-{di}", "三期方向", f"三期{dn}", f"最近三期号码方向为“{dn}”", ev, False, _dir_rule(di)))
    for si, sh in enumerate(SHAPES):
        R.append(Rule(f"N06-{sh}", "三期区间", f"区间{sh}", f"最近三期区间（{'/'.join(ZONE_NAMES)}）形态为 {sh}",
                      "下一期落在本期所在区间", False, _zone_rule(si)))
    R.append(Rule("N07", "三期集中", "三期集中", "最近三期 最大值－最小值 ≤ 5", "下一期落在 [三期最小值, 三期最大值]",
                  False, r_cluster))
    R.append(Rule("J01", "联合形态", "生肖重复但号码不同", "本期生肖＝上期生肖 且 本期号码≠上期号码",
                  "下一期生肖＝本期生肖", True, r_zodiac_repeat_number_diff))
    for di, dn in enumerate(DIRS[:4]):
        R.append(Rule(f"J02-{di}", "联合形态", f"生肖ABA＋号码{dn}", f"生肖形态 ABA 且号码方向“{dn}”",
                      "下一期生肖＝末期生肖", True, _aba_dir_rule(di)))
    return R


REGISTRY: list[Rule] = _build_registry()
RULES = {r.rule_id: r for r in REGISTRY}


def registry_hash() -> str:
    h = hashlib.sha256()
    for r in REGISTRY:
        h.update(f"{r.rule_id}|{r.condition}|{r.event}|{r.target}|{r.horizon}".encode())
    h.update(inspect.getsource(inspect.getmodule(_build_registry)).encode())
    return h.hexdigest()[:16]


def rules_table():
    import pandas as pd

    return pd.DataFrame([{
        "规则编号": r.rule_id, "类别": r.family, "名称": r.name, "触发时点": r.trigger, "条件": r.condition,
        "预测对象": r.target, "观察期限": f"{r.horizon} 期", "事件": r.event,
        "需要生肖映射": "是" if r.needs_zodiac else "否", "基准": "|事件号码集合|÷49（按目标期映射）",
    } for r in REGISTRY])
