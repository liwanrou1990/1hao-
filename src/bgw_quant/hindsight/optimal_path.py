"""事后最优交易路径（已知完整走势的事后最优模拟）。

问题：给定昨日完整 1 分钟 K 线（代币成交价）、本金 C、成本与流动性约束，允许无限次买卖（同一时间最多一个持仓、
不得超额使用本金、不得假设无限流动性），求：
  (1) 最高可实现净收益及路径；
  (2) 达到目标净收益所需的最少交易次数及路径。

模型：
  * 每笔交易 = 区间 [b, s]（b < s），在第 b 根收盘买入、第 s 根收盘卖出；区间两两不重叠（资金占用：同一时刻只有一个持仓，卖出后资金才可再用）。
  * 单笔投入 S = min(C, L_b, L_s)，L_i = participation_rate × 第 i 根成交额（流动性未知时 L=∞ 并标记“流动性不可验证”）。
  * 成交价：买 p_b × (1 + c(S))，卖 p_s × (1 − c(S))，c(S) = 手续费 + 半价差 + 滑点 + 冲击(S)；每笔另付 Gas × 拆单数（S 超过单笔上限需拆单）。
  * 单笔净利润 π(b, s) 只与自身有关（本金固定、不做日内复利），于是“最优路径”= 最大权重不相交区间集合（动态规划，O(n²)）。
  * 最少交易次数：在交易次数 ≤ k 的约束下做同样的 DP，取使最优净利润 ≥ 目标的最小 k。

所有输出均为“已知完整走势的事后最优模拟”，不构成任何实盘收益承诺。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel

DISCLAIMER = "已知完整走势的事后最优模拟：使用了昨日全部K线反向求解，实盘无法预知走势，不代表未来可获得同等收益。"


@dataclass
class PathTrade:
    symbol: str
    buy_idx: int
    sell_idx: int
    buy_ts: str          # 美东时间
    sell_ts: str
    buy_ref: float
    buy_exec: float
    sell_ref: float
    sell_exec: float
    size_usd: float      # 投入金额（买入名义）
    qty: float
    proceeds_usd: float
    cost_usd: float
    net_pnl_usd: float
    net_ret: float       # 相对投入金额
    hold_bars: int
    liquidity_capped: bool
    child_orders: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OptimalPathResult:
    symbol: str
    day: str
    venue: str
    source: str
    capital_usd: float
    cost_profile: str
    max_net_profit_usd: float
    max_net_return: float
    n_trades: int
    trades: list[PathTrade]
    target_return: float | None = None
    target_profit_usd: float | None = None
    target_met: bool | None = None
    min_trades_for_target: int | None = None
    target_path: list[PathTrade] = field(default_factory=list)
    best_by_k: dict[int, float] = field(default_factory=dict)      # k 笔以内的最优净利润
    liquidity_known: bool = True
    liquidity_capped_any: bool = False
    max_deployed_usd: float = 0.0
    unverifiable: bool = False
    data_is_simulated: bool = False
    notes: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trades", "target_path")}
        d["trades"] = [t.to_dict() for t in self.trades]
        d["target_path"] = [t.to_dict() for t in self.target_path]
        d["best_by_k"] = {int(k): float(v) for k, v in self.best_by_k.items()}
        return d


# ----------------------------------------------------------------------------- 利润矩阵
def _liquidity_per_bar(bars: pd.DataFrame, participation_rate: float) -> tuple[np.ndarray, bool]:
    amt = bars["amount"].to_numpy(dtype=float) if "amount" in bars else np.full(len(bars), np.nan)
    known = bool(np.isfinite(amt).any() and np.nansum(amt) > 0)
    if not known:
        return np.full(len(bars), np.inf), False
    L = np.where(np.isfinite(amt), amt * participation_rate, 0.0)
    return L, True


def profit_matrix(bars: pd.DataFrame, cost: CostModel, capital_usd: float, participation_rate: float = 0.1,
                  price_col: str = "close") -> tuple[np.ndarray, np.ndarray, bool]:
    """返回 (π[b, s] 净利润矩阵, S[b, s] 投入金额矩阵, 流动性是否已知)。b ≥ s 的位置为 -inf。"""
    p = bars[price_col].to_numpy(dtype=float)
    n = len(p)
    L, known = _liquidity_per_bar(bars, participation_rate)
    S = np.minimum(capital_usd, np.minimum.outer(L, L))
    S = np.where(S < cost.p.min_tx_usd, 0.0, S)
    side = cost.proportional_side_cost()
    with np.errstate(divide="ignore", invalid="ignore"):
        impact = cost.p.impact_coeff * cost.p.daily_vol * np.sqrt(np.where(cost.p.depth_usd > 0, S / cost.p.depth_usd, 0.0))
    c = side + impact
    nchild = np.where(S > 0, np.ceil(S / cost.p.max_tx_usd), 0)
    buy_px = p[:, None] * (1 + c)
    sell_px = p[None, :] * (1 - c)
    with np.errstate(divide="ignore", invalid="ignore"):
        qty = np.where(buy_px > 0, S / buy_px, 0.0)
    proceeds = qty * sell_px
    pi = proceeds - S - 2 * cost.p.gas_usd * nchild
    pi = np.where(S > 0, pi, -np.inf)
    pi[np.tril_indices(n)] = -np.inf     # 要求 s > b
    return pi, S, known


# ----------------------------------------------------------------------------- 动态规划
def _dp_unbounded(pi: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int]]]:
    n = pi.shape[0]
    best = np.zeros(n + 1)          # best[j]: 前 j 根K线内（索引 < j）的最优净利润
    choice = np.full(n + 1, -1, dtype=np.int64)   # -1 = 不在 j-1 卖出；否则为买入索引 b
    for j in range(2, n + 1):
        s = j - 1
        cand = best[: s] + pi[: s, s]      # 在 b 买（b < s），之前的最优为 best[b]
        b = int(np.argmax(cand))
        if cand[b] > best[j - 1]:
            best[j], choice[j] = cand[b], b
        else:
            best[j] = best[j - 1]
    trades = []
    j = n
    while j > 0:
        if choice[j] >= 0:
            trades.append((choice[j], j - 1))
            j = choice[j]
        else:
            j -= 1
    trades.reverse()
    return best, trades


def _dp_bounded(pi: np.ndarray, kmax: int) -> tuple[np.ndarray, dict[int, list[tuple[int, int]]]]:
    """返回 bestk[k] = 至多 k 笔的最优净利润，以及每个 k 的路径。"""
    n = pi.shape[0]
    bestk = np.zeros((kmax + 1, n + 1))
    choice = np.full((kmax + 1, n + 1), -1, dtype=np.int64)
    for k in range(1, kmax + 1):
        prev = bestk[k - 1]
        cur = bestk[k]
        for j in range(2, n + 1):
            s = j - 1
            cand = prev[: s] + pi[: s, s]
            b = int(np.argmax(cand))
            if cand[b] > cur[j - 1]:
                cur[j], choice[k, j] = cand[b], b
            else:
                cur[j] = cur[j - 1]
    paths: dict[int, list[tuple[int, int]]] = {}
    for k in range(1, kmax + 1):
        tr, kk, j = [], k, n
        while j > 0 and kk > 0:
            if choice[kk, j] >= 0:
                tr.append((choice[kk, j], j - 1))
                j = choice[kk, j]
                kk -= 1
            else:
                j -= 1
        tr.reverse()
        paths[k] = tr
    return bestk[:, n], paths


# ----------------------------------------------------------------------------- 组装
def _build_trades(bars: pd.DataFrame, pairs: list[tuple[int, int]], pi: np.ndarray, S: np.ndarray, cost: CostModel,
                  capital_usd: float, symbol: str, price_col: str = "close") -> list[PathTrade]:
    p = bars[price_col].to_numpy(dtype=float)
    ts_ny = bars["ts_ny"].astype(str).to_numpy()
    out = []
    for b, s in pairs:
        size = float(S[b, s])
        impact = cost.p.impact_coeff * cost.p.daily_vol * math.sqrt(size / cost.p.depth_usd) if cost.p.depth_usd > 0 else 0.0
        c = cost.proportional_side_cost() + impact
        nchild = int(math.ceil(size / cost.p.max_tx_usd)) if size > 0 else 0
        buy_exec, sell_exec = p[b] * (1 + c), p[s] * (1 - c)
        qty = size / buy_exec
        proceeds = qty * sell_exec
        net = float(pi[b, s])
        cost_usd = size * c + proceeds / (1 - c) * c + 2 * cost.p.gas_usd * nchild
        out.append(PathTrade(symbol, int(b), int(s), ts_ny[b], ts_ny[s], float(p[b]), float(buy_exec), float(p[s]), float(sell_exec),
                             size, float(qty), float(proceeds), float(cost_usd), net, net / size if size else 0.0, int(s - b),
                             bool(size < capital_usd - 1e-9), nchild))
    return out


def optimal_path(bars: pd.DataFrame, cost: CostModel, capital_usd: float, *, symbol: str | None = None,
                 participation_rate: float = 0.1, target_return: float | None = None, cost_profile: str = "",
                 max_k: int | None = None) -> OptimalPathResult:
    bars = bars.reset_index(drop=True)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]) if len(bars) else ""
    venue = str(bars["venue"].iloc[0]) if len(bars) else ""
    source = str(bars["source"].iloc[0]) if len(bars) else ""
    sim = bool((bars["quality"].astype(int) & 128).any()) if len(bars) else False
    notes: list[str] = []
    if len(bars) < 2:
        return OptimalPathResult(symbol, day, venue, source, capital_usd, cost_profile, 0.0, 0.0, 0, [], target_return,
                                 None, False, None, data_is_simulated=sim, notes=["K线不足"])
    pi, S, known = profit_matrix(bars, cost, capital_usd, participation_rate)
    best, pairs = _dp_unbounded(pi)
    max_profit = float(best[-1])
    trades = _build_trades(bars, pairs, pi, S, cost, capital_usd, symbol)
    max_deployed = max((t.size_usd for t in trades), default=0.0)
    capped = any(t.liquidity_capped for t in trades)
    unver = cost.is_unverifiable(max_deployed) or not known
    if not known:
        notes.append("该数据源不含成交额（如 ondo 报价K线）：流动性未知，未施加逐根成交额上限，结果标记为不可验证")
    if capped:
        notes.append(f"本金 {capital_usd:,.0f} USD 受逐根流动性上限（参与率 {participation_rate:.0%}）限制，单笔最大投入 {max_deployed:,.0f} USD；"
                     "模型假设每笔在一根K线内完成买入/卖出，未建模跨多根分批建仓，结果偏保守；其余资金闲置")
    if cost.is_unverifiable(max_deployed):
        notes.append(f"单笔投入超过深度假设 {cost.p.depth_usd:,.0f} USD 的 10%，成交不可验证")
    res = OptimalPathResult(symbol, day, venue, source, capital_usd, cost_profile, max_profit, max_profit / capital_usd,
                            len(trades), trades, liquidity_known=known, liquidity_capped_any=capped, max_deployed_usd=max_deployed,
                            unverifiable=unver, data_is_simulated=sim, notes=notes)
    if target_return is not None:
        res.target_return = target_return
        res.target_profit_usd = target_return * capital_usd
        res.target_met = max_profit >= res.target_profit_usd - 1e-9
        kmax = max_k or max(len(trades), 1)
        bestk, paths = _dp_bounded(pi, kmax)
        res.best_by_k = {k: float(bestk[k]) for k in range(1, kmax + 1)}
        if res.target_met:
            for k in range(1, kmax + 1):
                if bestk[k] >= res.target_profit_usd - 1e-9:
                    res.min_trades_for_target = k
                    res.target_path = _build_trades(bars, paths[k], pi, S, cost, capital_usd, symbol)
                    break
        else:
            notes.append(f"目标净收益 {target_return:.2%}（{res.target_profit_usd:,.2f} USD）不可达：即使已知完整走势，最高可实现 "
                         f"{res.max_net_return:.2%}（{max_profit:,.2f} USD）")
    return res


def min_trades_for_target(res: OptimalPathResult) -> int | None:
    return res.min_trades_for_target
