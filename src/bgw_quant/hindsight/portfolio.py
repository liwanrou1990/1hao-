"""多股票组合的事后最优路径。

两种资金使用方式（均不超额使用本金、不假设无限流动性）：
  A. 轮动（sequential）：全部本金在任一时刻只持有一只股票，可在不同股票间轮换 —— 跨股票的最大权重不相交区间 DP。
  B. 并行均分（parallel_split）：本金均分给 m 只股票，各自独立求最优路径（m = 1..M），取总净利润最高的 m。
输出两者并标出更优者。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from .optimal_path import DISCLAIMER, OptimalPathResult, PathTrade, _build_trades, optimal_path, profit_matrix


@dataclass
class PortfolioResult:
    day: str
    capital_usd: float
    cost_profile: str
    sequential_profit_usd: float
    sequential_trades: list[PathTrade]
    parallel_best_m: int
    parallel_profit_usd: float
    parallel_allocation: dict[str, float]
    parallel_results: dict[str, OptimalPathResult]
    best_mode: str
    best_profit_usd: float
    best_return: float
    target_return: float | None = None
    target_met: bool | None = None
    symbols: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    data_is_simulated: bool = False
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {"day": self.day, "capital_usd": self.capital_usd, "cost_profile": self.cost_profile,
                "sequential": {"profit_usd": self.sequential_profit_usd, "return": self.sequential_profit_usd / self.capital_usd,
                               "n_trades": len(self.sequential_trades), "trades": [t.to_dict() for t in self.sequential_trades]},
                "parallel": {"m": self.parallel_best_m, "profit_usd": self.parallel_profit_usd, "return": self.parallel_profit_usd / self.capital_usd,
                             "allocation": self.parallel_allocation,
                             "per_symbol": {k: {"profit_usd": v.max_net_profit_usd, "return": v.max_net_return, "n_trades": v.n_trades,
                                                "unverifiable": v.unverifiable, "trades": [t.to_dict() for t in v.trades]}
                                            for k, v in self.parallel_results.items()}},
                "best_mode": self.best_mode, "best_profit_usd": self.best_profit_usd, "best_return": self.best_return,
                "target_return": self.target_return, "target_met": self.target_met, "symbols": self.symbols, "notes": self.notes,
                "data_is_simulated": self.data_is_simulated, "disclaimer": self.disclaimer}


def _sequential(bars_by_symbol: dict[str, pd.DataFrame], cost: CostModel, capital_usd: float, participation_rate: float):
    # 统一时间网格
    grid = sorted(set().union(*[set(b["ts"]) for b in bars_by_symbol.values()]))
    pos = {t: i for i, t in enumerate(grid)}
    n = len(grid)
    mats = {}
    for sym, b in bars_by_symbol.items():
        b = b.reset_index(drop=True)
        pi, S, known = profit_matrix(b, cost, capital_usd, participation_rate)
        idx = np.array([pos[t] for t in b["ts"]])
        full = np.full((n, n), -np.inf)
        full[np.ix_(idx, idx)] = pi
        mats[sym] = (full, S, idx, b)
    best = np.zeros(n + 1)
    choice: list[tuple[str, int] | None] = [None] * (n + 1)
    for j in range(2, n + 1):
        s = j - 1
        best[j] = best[j - 1]
        for sym, (full, _, _, _) in mats.items():
            cand = best[:s] + full[:s, s]
            b = int(np.argmax(cand))
            if cand[b] > best[j]:
                best[j], choice[j] = cand[b], (sym, b)
    pairs = []
    j = n
    while j > 0:
        if choice[j] is not None:
            sym, b = choice[j]
            pairs.append((sym, b, j - 1))
            j = b
        else:
            j -= 1
    pairs.reverse()
    trades: list[PathTrade] = []
    for sym, b, s in pairs:
        full, S, idx, bars = mats[sym]
        inv = {g: i for i, g in enumerate(idx)}
        lb, ls = inv[b], inv[s]
        pi_local, S_local, _ = profit_matrix(bars, cost, capital_usd, participation_rate)
        trades += _build_trades(bars, [(lb, ls)], pi_local, S_local, cost, capital_usd, sym)
    return float(best[-1]), trades


def portfolio_optimal(bars_by_symbol: dict[str, pd.DataFrame], cost: CostModel, capital_usd: float, *, cost_profile: str = "",
                      participation_rate: float = 0.1, target_return: float | None = None, max_parallel: int = 5) -> PortfolioResult:
    syms = list(bars_by_symbol)
    day = str(pd.to_datetime(next(iter(bars_by_symbol.values()))["ts_ny"]).dt.date.iloc[0])
    seq_profit, seq_trades = _sequential(bars_by_symbol, cost, capital_usd, participation_rate)
    # 并行均分：按单股（本金/m）最优收益排序取前 m
    best_m, best_par, best_alloc, best_res = 1, -np.inf, {}, {}
    for m in range(1, min(max_parallel, len(syms)) + 1):
        cap = capital_usd / m
        singles = {s: optimal_path(b, cost, cap, symbol=s, participation_rate=participation_rate, cost_profile=cost_profile)
                   for s, b in bars_by_symbol.items()}
        top = sorted(singles.items(), key=lambda kv: -kv[1].max_net_profit_usd)[:m]
        total = sum(r.max_net_profit_usd for _, r in top)
        if total > best_par:
            best_m, best_par = m, total
            best_alloc = {s: cap for s, _ in top}
            best_res = {s: r for s, r in top}
    mode = "sequential" if seq_profit >= best_par else "parallel_split"
    best_profit = max(seq_profit, best_par)
    notes = ["轮动：任一时刻只持有一只股票；并行均分：本金等分给 m 只股票各自独立交易；两者都不超额使用本金"]
    sim = any(bool((b["quality"].astype(int) & 128).any()) for b in bars_by_symbol.values())
    res = PortfolioResult(day, capital_usd, cost_profile, seq_profit, seq_trades, best_m, float(best_par), best_alloc, best_res,
                          mode, float(best_profit), float(best_profit / capital_usd), target_return, None, syms, notes, sim)
    if target_return is not None:
        res.target_met = best_profit >= target_return * capital_usd - 1e-9
        if not res.target_met:
            notes.append(f"组合目标 {target_return:.2%} 不可达，最高可实现 {res.best_return:.2%}")
    return res
