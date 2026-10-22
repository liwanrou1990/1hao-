"""资产池层面的事后对照（A / B），与因果 C 模型严格分离。

A 历史事后理论最优：在统一分钟网格上，对每个区间 [b, s] 取“所有当时有 K 线的资产中利润最大者”（不限流动性、不计冲击、
  单次全仓），再做最大权重不相交区间 DP —— 即“全知者在资产之间任意切换”的日利润上限。
B 历史流动性约束上限：每个资产单独求执行约束下的最大可实现净收益 B_a(c)（见 hindsight/feasibility.py，共享流动性、
  收盘价成交、全部成本），再在资产之间分配本金（以 C/4 为单位的背包）使 Σ B_a(c_a) 最大；组合策略按均分。
两者都使用了当日完整信息，是“历史上可以赚到的利润”的上界估计，不是可提前执行的策略。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..hindsight.feasibility import FeasibilityResult, feasibility
from ..hindsight.optimal_path import _dp_unbounded
from .strategies import StrategySpec
from .universe import GRID_MINUTES, AssetDay

DISCLAIMER_AB = "A/B 为事后（使用当日全部信息）的理论/可执行上限，不代表可提前执行的策略，也不代表未来可复制收益。"


@dataclass
class UniverseAB:
    strategy_key: str
    day: str
    capital_usd: float
    target_usd: float
    theoretical_net_usd: float               # A
    theoretical_trades: list[dict]            # A 路径（资产、买卖网格时刻、利润）
    executable_net_usd: float                 # B
    executable_alloc: dict[str, float]        # B 的本金分配
    per_asset_B: dict[str, float]             # 各资产在其分配本金下的 B
    executable_trades: int
    a_met: bool
    b_met: bool
    liquidity_sources: dict[str, str]
    notes: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER_AB

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _theoretical_pi_grid(a: AssetDay, capital_usd: float) -> np.ndarray:
    """单资产理论利润矩阵（无流动性/冲击），映射到 1440 网格；无 K 线位置为 -inf。"""
    p = a.bars["close"].to_numpy(float)
    n = len(p)
    side = a.cost.proportional_side_cost()
    buy = p[:, None] * (1 + side)
    sell = p[None, :] * (1 - side)
    qty = capital_usd / buy
    pi = qty * sell - capital_usd - 2 * a.cost.p.gas_usd * np.ceil(capital_usd / a.cost.p.max_tx_usd)
    pi[np.tril_indices(n)] = -np.inf
    G = np.full((GRID_MINUTES, GRID_MINUTES), -np.inf)
    g = a.grid_of_bar
    G[np.ix_(g, g)] = pi
    return G


def universe_theoretical(assets: list[AssetDay], capital_usd: float) -> tuple[float, list[dict]]:
    best_pi = np.full((GRID_MINUTES, GRID_MINUTES), -np.inf)
    who = np.full((GRID_MINUTES, GRID_MINUTES), -1, dtype=np.int16)
    for i, a in enumerate(assets):
        if a.n < 2 or "long" not in a.cap.directions:
            continue
        G = _theoretical_pi_grid(a, capital_usd)
        m = G > best_pi
        best_pi[m], who[m] = G[m], i
    best_pi = np.where(np.isfinite(best_pi), best_pi, -np.inf)
    if not np.isfinite(best_pi).any():
        return 0.0, []
    bk, pairs = _dp_unbounded(best_pi)
    trades = []
    for b, s_ in pairs:
        a = assets[int(who[b, s_])]
        trades.append({"symbol": a.symbol, "asset_class": a.asset_class, "buy_grid": int(b), "sell_grid": int(s_),
                       "open_ts": str(a.bars["ts_ny"].iloc[int(a.bar_at_grid[b])]), "close_ts": str(a.bars["ts_ny"].iloc[int(a.bar_at_grid[s_])]),
                       "net_pnl_usd": float(best_pi[b, s_])})
    return float(bk[-1]), trades


def asset_B(a: AssetDay, capital_usd: float, target_usd: float, participation_rate: float, tx_amount_per_bar=None) -> FeasibilityResult:
    """单资产执行约束上限 B_a(c)。加密货币 1440 根 K 线时资金片上限收紧到 32、早停阈值 5%（计算量），报告中注明：
    这使加密资产的 B 略偏保守（仍是事后上界的近似）。"""
    crypto = a.n > 800
    grid = (1, 2, 4, 8, 16, 32) if crypto else (1, 2, 4, 8, 16, 32, 64, 128, 256)
    return feasibility(a.bars, a.cost.p, capital_usd, target_usd, allow_short=a.cap.short, participation_rate=participation_rate,
                       tx_amount_per_bar=tx_amount_per_bar, exec_cfg={"capital_cost_annual": 0.05}, symbol=a.symbol, tranche_grid=grid,
                       early_stop_gain=0.05 if crypto else 0.02)


def universe_executable(assets: list[AssetDay], spec: StrategySpec, capital_usd: float, target_usd: float, participation_rate: float,
                        tx_amount: dict[str, np.ndarray | None] | None = None, b_cache: dict | None = None, top_n: int = 4
                        ) -> tuple[float, dict[str, float], dict[str, float], int, dict[str, str]]:
    """资产池 B：max( 单资产全仓 B_a(C), 前 top_n 名按 1/2、1/4 本金拆分之和 )。组合策略 F 按均分 C/N。
    b_cache[(symbol, capital)] 可跨策略复用同一天的单资产结果。"""
    tx_amount = tx_amount or {}
    b_cache = b_cache if b_cache is not None else {}
    srcs: dict[str, str] = {}

    def B(a: AssetDay, c: float) -> FeasibilityResult:
        key = (a.symbol, round(c, 2))
        if key not in b_cache:
            b_cache[key] = asset_B(a, c, target_usd, participation_rate, tx_amount.get(a.symbol))
        r = b_cache[key]
        srcs[a.symbol] = r.liquidity_source
        return r

    usable = [a for a in assets if a.n >= 2 and a.cap.directions]
    if not usable:
        return 0.0, {}, {}, 0, {}
    if len(usable) == 1 or spec.family == "F":
        alloc = {a.symbol: capital_usd / len(usable) for a in usable}
        total, per, ntr = 0.0, {}, 0
        for a in usable:
            r = B(a, alloc[a.symbol])
            per[a.symbol], total, ntr = r.executable_net_usd, total + r.executable_net_usd, ntr + len(r.trades)
        return total, alloc, per, ntr, srcs
    full = {a.symbol: B(a, capital_usd) for a in usable}
    ranked = sorted(usable, key=lambda a: -full[a.symbol].executable_net_usd)
    best_total, best_alloc, best_per, best_ntr = -np.inf, {}, {}, 0
    for m in (1, 2, 4):
        chosen = ranked[: min(m, len(ranked))]
        if len(chosen) < m and m > 1:
            break
        tot, per, ntr, alloc = 0.0, {}, 0, {}
        for a in chosen:
            r = full[a.symbol] if m == 1 else B(a, capital_usd / m)
            tot += r.executable_net_usd; per[a.symbol] = r.executable_net_usd; ntr += len(r.trades); alloc[a.symbol] = capital_usd / m
        if tot > best_total:
            best_total, best_alloc, best_per, best_ntr = tot, alloc, per, ntr
    return float(best_total), best_alloc, best_per, best_ntr, srcs


def universe_ab(assets: list[AssetDay], spec: StrategySpec, capital_usd: float, target_usd: float, day: str, participation_rate: float = 0.10,
                tx_amount: dict[str, np.ndarray | None] | None = None, b_cache: dict | None = None) -> UniverseAB:
    theo, theo_trades = universe_theoretical(assets, capital_usd)
    ex, alloc, per, ntr, srcs = universe_executable(assets, spec, capital_usd, target_usd, participation_rate, tx_amount, b_cache)
    notes = []
    if not any("long" in a.cap.directions for a in assets):
        notes.append("资产池内无可交易方向（能力未核实）")
    if any(s == "unknown" for s in srcs.values()):
        notes.append("部分资产流动性未知：B 未施加成交量上限，不可验证")
    return UniverseAB(spec.key, day, capital_usd, target_usd, theo, theo_trades, ex, alloc, per, ntr, theo >= target_usd - 1e-9, ex >= target_usd - 1e-9, srcs, notes)
