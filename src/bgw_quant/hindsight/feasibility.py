"""历史可行性搜索（已知昨日完整走势）：指定日净收益目标在数学上与执行上是否存在可实现路径。

两类结果严格分开：
  * A 理论最大净收益：不限流动性、不计冲击，单次全仓、任意次数；扣手续费/价差/滑点/Gas。
  * B 执行约束下最大可实现净收益：
      - 按时间顺序，所有成交只用该根K线的收盘价（不假设最高/最低价可成交）；
      - 每根K线可成交金额 ≤ 参与率 × 该根实际成交额（优先用逐笔成交聚合的成交额），多笔之间共享、不重复使用；
      - 本金拆成 k 份资金片（k 事后在 {1,2,4,8,16,32} 内选最优），每片按动态规划求自身最优区间集合，片与片共享流动性预算；
      - 每片同一时刻只持一个仓位，不加杠杆、不超本金；成本含手续费/价差/滑点/冲击(按子单规模)/Gas/资金成本；
      - 做空仅在品种能力登记支持时参与（计提借券费）。
  * 达标所需交易次数：对合并路径按平仓时间累计，首次 ≥ 目标时的笔数；并给出“至多 k 笔”的最优值曲线。
所有结果为事后信息下的最优解，不代表未来可复制收益。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel, CostProfile
from .optimal_path import DISCLAIMER, _dp_bounded, _dp_unbounded

BARS_PER_YEAR = 252 * 390


@dataclass
class FeasTrade:
    tranche: int
    direction: str
    buy_idx: int             # 开仓K线
    sell_idx: int            # 平仓K线
    open_ts: str
    close_ts: str
    open_ref: float
    open_exec: float
    close_ref: float
    close_exec: float
    size_usd: float
    qty: float
    cost_usd: float          # 价格内成本 + Gas + 资金/借券
    net_pnl_usd: float
    net_ret: float
    hold_bars: int
    cum_net_pnl_usd: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class FeasibilityResult:
    symbol: str
    day: str
    source: str
    capital_usd: float
    target_usd: float
    tier: str | None
    directions: list[str]
    liquidity_source: str            # transactions | kline_amount | unknown
    theoretical_net_usd: float
    theoretical_trades: int
    theoretical_met: bool
    executable_net_usd: float
    executable_return: float
    executable_met: bool
    n_tranches: int
    tranche_usd: float
    trades: list[FeasTrade]
    n_trades: int
    n_long: int
    n_short: int
    trades_to_target: int | None
    target_hit_ts: str | None
    best_by_k: dict[int, float]
    liquidity_used_ratio: float | None
    peak_deployed_usd: float
    cost_usd: float
    limits: list[str]
    data_is_simulated: bool
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "trades"}
        d["trades"] = [t.to_dict() for t in self.trades]
        d["best_by_k"] = {int(k): float(v) for k, v in self.best_by_k.items()}
        return d


def liquidity_from_bars(bars: pd.DataFrame, participation_rate: float, tx_amount_per_bar: np.ndarray | None = None) -> tuple[np.ndarray, str]:
    """优先逐笔成交聚合（tx_amount_per_bar），否则 K 线成交额；都没有则未知（不施加上限）。"""
    n = len(bars)
    if tx_amount_per_bar is not None and np.isfinite(tx_amount_per_bar).any() and np.nansum(tx_amount_per_bar) > 0:
        return np.where(np.isfinite(tx_amount_per_bar), tx_amount_per_bar * participation_rate, 0.0), "transactions"
    amt = bars["amount"].to_numpy(float) if "amount" in bars else np.full(n, np.nan)
    if np.isfinite(amt).any() and np.nansum(amt) > 0:
        return np.where(np.isfinite(amt), amt * participation_rate, 0.0), "kline_amount"
    return np.full(n, np.inf), "unknown"


def _pi_matrices(p: np.ndarray, avail: np.ndarray, cost: CostModel, size_cap: float, cfg: dict, allow_short: bool, borrow: float):
    """给定当前可用流动性 avail，构造多/空利润矩阵（投入 S = min(size_cap, avail_b, avail_s)，成交价 = 收盘价 ± 成本）。"""
    n = len(p)
    S = np.minimum(size_cap, np.minimum.outer(avail, avail))
    S = np.where(S < cost.p.min_tx_usd, 0.0, S)
    side = cost.proportional_side_cost()
    with np.errstate(divide="ignore", invalid="ignore"):
        impact = cost.p.impact_coeff * cost.p.daily_vol * np.sqrt(np.where(cost.p.depth_usd > 0, S / cost.p.depth_usd, 0.0))
    c = side + impact
    nchild = np.where(S > 0, np.ceil(S / cost.p.max_tx_usd), 0)
    hold = np.maximum(0, np.arange(n)[None, :] - np.arange(n)[:, None])
    fund = S * cfg.get("capital_cost_annual", 0.05) * hold / BARS_PER_YEAR
    gas = 2 * cost.p.gas_usd * nchild
    # 多：买 p_b(1+c) 卖 p_s(1−c)
    with np.errstate(divide="ignore", invalid="ignore"):
        qty_l = np.where(p[:, None] > 0, S / (p[:, None] * (1 + c)), 0.0)
    pi_l = qty_l * p[None, :] * (1 - c) - S - gas - fund
    pi_l = np.where(S > 0, pi_l, -np.inf)
    pi_l[np.tril_indices(n)] = -np.inf
    if not allow_short:
        return pi_l, None, S
    # 空：卖 p_b(1−c) 收 S'，买回 qty×p_s(1+c)；借券费
    qty_s = np.where(p[:, None] > 0, S / (p[:, None] * (1 - c)), 0.0)
    pi_s = S - qty_s * p[None, :] * (1 + c) - gas - fund - S * borrow * hold / BARS_PER_YEAR
    pi_s = np.where(S > 0, pi_s, -np.inf)
    pi_s[np.tril_indices(n)] = -np.inf
    return pi_l, pi_s, S


def _combine(pi_l, pi_s):
    if pi_s is None:
        return pi_l, np.zeros_like(pi_l, dtype=np.int8)
    use_s = pi_s > pi_l
    return np.where(use_s, pi_s, pi_l), use_s.astype(np.int8)


def _materialize(bars, pairs, pi, dirs, S, cost, cfg, borrow, tranche_id) -> list[FeasTrade]:
    p = bars["close"].to_numpy(float)
    ts = bars["ts_ny"].astype(str).to_numpy()
    side = cost.proportional_side_cost()
    out = []
    for b, s in pairs:
        size = float(S[b, s])
        imp = cost.p.impact_coeff * cost.p.daily_vol * math.sqrt(size / cost.p.depth_usd) if cost.p.depth_usd > 0 else 0.0
        c = side + imp
        short = bool(dirs[b, s])
        if short:
            open_exec, close_exec = p[b] * (1 - c), p[s] * (1 + c)
            qty = size / open_exec
        else:
            open_exec, close_exec = p[b] * (1 + c), p[s] * (1 - c)
            qty = size / open_exec
        net = float(pi[b, s])
        gross = (qty * (close_exec - open_exec)) if not short else (qty * (open_exec - close_exec))
        out.append(FeasTrade(tranche_id, "short" if short else "long", int(b), int(s), ts[b], ts[s], float(p[b]), float(open_exec), float(p[s]),
                             float(close_exec), size, float(qty), float(gross - net) if gross - net > 0 else float(size * 2 * c), net,
                             net / size if size else 0.0, int(s - b)))
    return out


def feasibility(bars: pd.DataFrame, profile: CostProfile, capital_usd: float, target_usd: float, *, allow_short: bool = False,
                borrow_annual: float = 0.05, participation_rate: float = 0.10, tranche_grid=(1, 2, 4, 8, 16, 32, 64, 128, 256),
                min_tranche_usd: float = 1000.0, early_stop_gain: float = 0.02,
                tx_amount_per_bar: np.ndarray | None = None, exec_cfg: dict | None = None, symbol: str | None = None,
                tier: str | None = None) -> FeasibilityResult:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    exec_cfg = exec_cfg or {}
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]) if n else ""
    p = bars["close"].to_numpy(float)
    cost = CostModel(profile)
    liq, liq_src = liquidity_from_bars(bars, participation_rate, tx_amount_per_bar)
    liquidity_known = liq_src != "unknown"
    # ---- A 理论：无流动性/冲击约束
    theo_prof = profile.scaled()
    theo_prof.impact_coeff, theo_prof.depth_usd, theo_prof.max_tx_usd = 0.0, 1e18, 1e18
    theo_cost = CostModel(theo_prof)
    pi_tl, pi_ts, _ = _pi_matrices(p, np.full(n, np.inf), theo_cost, capital_usd, exec_cfg, allow_short, borrow_annual)
    pi_t, _ = _combine(pi_tl, pi_ts)
    best_t, pairs_t = _dp_unbounded(pi_t)
    theo_net = float(best_t[-1])
    # ---- B 可执行：资金片 DP，共享流动性
    best_total, best = -np.inf, None
    # 资金片数：小本金不切成低于 min_tranche_usd 的碎片（k=1 始终保留）
    grid = [k for k in tranche_grid if k == 1 or capital_usd / k >= min_tranche_usd] or [1]
    for k in grid:
        avail = liq.copy()
        size_cap = capital_usd / k
        trades: list[FeasTrade] = []
        total = 0.0
        for t_id in range(k):
            pi_l, pi_s, S = _pi_matrices(p, avail, cost, size_cap, exec_cfg, allow_short, borrow_annual)
            pi, dirs = _combine(pi_l, pi_s)
            bk, pairs = _dp_unbounded(pi)
            if bk[-1] <= 1e-9 or not pairs:
                break
            tr = _materialize(bars, pairs, pi, dirs, S, cost, exec_cfg, borrow_annual, t_id + 1)
            for x in tr:
                avail[x.buy_idx] = max(0.0, avail[x.buy_idx] - x.size_usd)
                avail[x.sell_idx] = max(0.0, avail[x.sell_idx] - x.size_usd)
            trades += tr
            total += float(bk[-1])
        improved = total > best_total
        prev_best = best_total
        if improved:
            best_total, best = total, (k, trades, avail)
        # 早停：资金片翻倍后增益低于 early_stop_gain → 更多资金片不再划算（节省 1440 根加密 K 线上的计算）
        if k > 1 and prev_best > 0 and (not improved or total <= prev_best * (1 + early_stop_gain)):
            break
    k_best, trades, avail_best = best if best else (1, [], liq.copy())
    trades.sort(key=lambda t: (t.sell_idx, t.buy_idx, t.tranche))
    cum, hit_ts, hit_n = 0.0, None, None
    for i, t in enumerate(trades, 1):
        cum += t.net_pnl_usd
        t.cum_net_pnl_usd = cum
        if hit_n is None and cum >= target_usd - 1e-9:
            hit_n, hit_ts = i, t.close_ts
    exec_net = float(sum(t.net_pnl_usd for t in trades))
    # 至多 k 笔的最优值曲线（按第一片的利润矩阵）
    pi_l, pi_s, S = _pi_matrices(p, liq.copy(), cost, capital_usd / k_best, exec_cfg, allow_short, borrow_annual)
    pi1, _ = _combine(pi_l, pi_s)
    kmax = max(1, min(len(trades), 40))
    bk, _ = _dp_bounded(pi1, kmax)
    best_by_k = {k: float(bk[k]) for k in range(1, kmax + 1)}
    # 同一时刻占用
    deployed = np.zeros(n)
    for t in trades:
        deployed[t.buy_idx:t.sell_idx + 1] += t.size_usd
    peak = float(deployed.max()) if n else 0.0
    used_ratio = None
    if liquidity_known:
        budget = float(np.sum(liq))
        used_ratio = float((budget - np.sum(avail_best)) / budget) if budget > 0 else None
    limits = []
    if theo_net < target_usd:
        limits.append(f"目标 {target_usd:,.0f} USD 高于理论最大净收益 {theo_net:,.0f} USD：即使不计流动性与冲击，当日行情在数学上不足以达标")
    if not liquidity_known:
        limits.append("流动性未知（无逐笔成交与成交额），可执行结果未施加成交量上限，不可验证")
    elif exec_net < target_usd:
        budget = float(np.sum(liq))
        limits.append(f"执行约束：参与率 {participation_rate:.0%} 下全日可成交 {budget:,.0f} USD（本金 {capital_usd:,.0f}），单根中位 {np.nanmedian(liq):,.0f} USD；"
                      f"最优资金片数 {k_best}，峰值占用 {peak:,.0f} USD（{peak / capital_usd:.0%}）")
        rt = cost.round_trip_fraction(min(capital_usd / k_best, float(np.nanmedian(liq))))
        limits.append(f"成本约束：单笔往返成本约 {rt:.2%}（含冲击），可执行路径累计 {exec_net:,.0f} USD，缺口 {target_usd - exec_net:,.0f} USD")
    if not allow_short:
        limits.append("仅做多：官方接口无做空渠道（或未登记核实渠道）")
    return FeasibilityResult(symbol, day, str(bars["source"].iloc[0]) if n else "", capital_usd, target_usd, tier,
                             ["long", "short"] if allow_short else ["long"], liq_src, theo_net, len(pairs_t), theo_net >= target_usd - 1e-9,
                             exec_net, exec_net / capital_usd if capital_usd else 0.0, exec_net >= target_usd - 1e-9, k_best, capital_usd / k_best,
                             trades, len(trades), sum(t.direction == "long" for t in trades), sum(t.direction == "short" for t in trades),
                             hit_n, hit_ts, best_by_k, used_ratio, peak, float(sum(t.cost_usd for t in trades)), limits,
                             bool((bars["quality"].astype(int) & 128).any()) if n else False)
