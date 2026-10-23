"""历史盈利路径优化（事后模板，不预测未来）。

给定昨天完整走势（多资产、统一分钟网格）、本金与净收益目标，搜索满足约束的交易路径：
  * 候选交易 = 任一资产上 [b, s] 区间的多头（或登记支持的空头），成交价 = 收盘价 ± 单边成本（手续费/价差/滑点/冲击），
    另扣 Gas、资金成本、借券费；投入 S = min(资金片, b 根可用量, s 根可用量)，可用量 = 参与率 × 实际成交额 − 已用（跨片共享）；
  * 资金片：本金拆成 k 片（k ∈ {1,2,4,8,16,32}），片内同一时刻只持一个仓位，片与片并行 → 总占用 ≤ 本金；
  * 目标函数：先满足“累计净收益 ≥ 目标”，再最小化风险 R = 路径最大回撤(逐分钟盯市) + 0.5×Σ单笔最大不利波动 + 成本 + 笔数罚；
    实现方式：对区间打分 score = 净利润 − λ × 最大不利波动(USD)，λ 越大越偏好低风险区间；对每个 k 二分搜索能达标的最大 λ，
    再按平仓时间截断到首次达标；在所有达标候选中取 R 最小者。无候选达标 → 输出 λ=0 的最高可实现净收益与缺口（不伪造）。
  * 资产切换：区间按“各资产中得分最高者”逐元素取最大，DP 结果中相邻区间换资产即为切换时机。
  * 不假设最高/最低价成交（高低价只用于衡量不利波动）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..hindsight.optimal_path import _dp_unbounded
from ..rotation.universe import GRID_MINUTES, AssetDay

BARS_PER_YEAR = 252 * 390
DISCLAIMER_PLAYBOOK = ("历史盈利路径模板：在已知昨天完整走势的前提下反向求解，属于事后分析；"
                       "不是当时可执行的策略，不代表未来可复制收益，也不构成任何收益承诺。")


@dataclass
class PlaybookTrade:
    no: int
    tranche: int
    symbol: str
    asset_class: str
    direction: str
    entry_grid: int
    exit_grid: int
    entry_ts: str
    entry_ref: float
    entry_price: float
    exit_ts: str
    exit_ref: float
    exit_price: float
    size_usd: float
    qty: float
    cost_usd: float
    funding_usd: float
    gas_usd: float
    mae_pct: float
    mae_usd: float
    net_pnl_usd: float
    net_ret: float
    hold_min: int
    cum_net_pnl_usd: float = 0.0
    reaches_target: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class PlaybookResult:
    day: str
    tier: str | None
    capital_usd: float
    target_usd: float
    mode: str                               # cross | single:<symbol>
    universe: list[str]
    target_met: bool
    net_usd: float
    net_return: float
    trades: list[PlaybookTrade]
    n_trades: int
    n_long: int
    n_short: int
    n_switches: int
    assets_used: list[str]
    n_tranches: int
    lam: float
    max_drawdown_usd: float                 # 逐分钟盯市权益曲线最大回撤
    sum_mae_usd: float
    cost_usd: float
    funding_usd: float
    risk_score_usd: float
    time_to_target_ts: str | None
    peak_deployed_usd: float
    liquidity_used_ratio: float | None
    max_achievable_net_usd: float           # λ=0 的最高可实现净收益（含流动性/成本约束）
    gap_usd: float
    candidates: list[dict]
    equity_curve: list[float]
    limits: list[str]
    sources: dict
    liquidity_sources: dict
    data_is_simulated: bool
    objective: dict
    disclaimer: str = DISCLAIMER_PLAYBOOK

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trades", "equity_curve")}
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


# ----------------------------------------------------------------------------- 预计算（统一网格 + 持有期带状矩阵）
# 带状表示：X[b, h-1] 对应区间 (b, s=b+h)，h = 1..H（最长持有 H 分钟）。比 n×n 全矩阵少一个数量级，便于跨资产逐元素取最大。
def _liquidity(a: AssetDay, participation: float, tx_amount: np.ndarray | None) -> tuple[np.ndarray, str]:
    n = a.n
    if tx_amount is not None and len(tx_amount) == n and np.nansum(tx_amount) > 0:
        return np.where(np.isfinite(tx_amount), tx_amount * participation, 0.0), "transactions"
    amt = a.bars["amount"].to_numpy(float) if "amount" in a.bars else np.full(n, np.nan)
    if np.isfinite(amt).any() and np.nansum(amt) > 0:
        return np.where(np.isfinite(amt), amt * participation, 0.0), "kline_amount"
    return np.full(n, np.inf), "unknown"


def _to_grid(a: AssetDay, x: np.ndarray, fill=np.nan) -> np.ndarray:
    g = np.full(GRID_MINUTES, fill, dtype=float)
    g[a.grid_of_bar] = x
    return g


def _shift_band(x: np.ndarray, H: int) -> np.ndarray:
    """B[b, h-1] = x[b+h]（越界为 NaN）。"""
    pad = np.concatenate([x, np.full(H, np.nan)])
    return np.lib.stride_tricks.sliding_window_view(pad, H + 1)[:, 1:]


@dataclass
class _Pre:
    asset: AssetDay
    p: np.ndarray            # 网格收盘价（缺失 NaN）
    p_s: np.ndarray          # 带状：p[b+h]
    liq: np.ndarray          # 网格可用量（缺失 0）
    liq_src: str
    mae_l: np.ndarray        # 带状最大不利波动比例（多）
    mae_s: np.ndarray | None
    hold: np.ndarray         # 带状持有分钟数
    ts: np.ndarray           # 网格美东时间字符串


def _precompute(assets: list[AssetDay], participation: float, tx_amount: dict | None, H: int, grid_ny: list[str]) -> list[_Pre]:
    out = []
    hold = np.broadcast_to(np.arange(1, H + 1, dtype=float)[None, :], (GRID_MINUTES, H))
    for a in assets:
        if a.n < 2 or not a.cap.directions:
            continue
        p = _to_grid(a, a.bars["close"].to_numpy(float))
        low = _to_grid(a, a.bars["low"].to_numpy(float), np.inf)
        high = _to_grid(a, a.bars["high"].to_numpy(float), -np.inf)
        liq_bars, src = _liquidity(a, participation, (tx_amount or {}).get(a.symbol))
        liq = _to_grid(a, liq_bars, 0.0)
        p_s = _shift_band(p, H)
        with np.errstate(invalid="ignore"):
            rm = np.minimum.accumulate(np.nan_to_num(_shift_band(low, H), nan=np.inf), axis=1)
            mae_l = np.maximum(0.0, (p[:, None] - rm) / p[:, None])
            mae_l = np.where(np.isfinite(mae_l), mae_l, 0.0)
            mae_s = None
            if a.cap.short:
                rM = np.maximum.accumulate(np.nan_to_num(_shift_band(high, H), nan=-np.inf), axis=1)
                mae_s = np.maximum(0.0, (rM - p[:, None]) / p[:, None])
                mae_s = np.where(np.isfinite(mae_s), mae_s, 0.0)
        out.append(_Pre(a, p, p_s, liq, src, mae_l, mae_s, np.asarray(hold), np.asarray(grid_ny)))
    return out


def _asset_bands(pr: _Pre, avail: np.ndarray, size_cap: float, cap_cost: float):
    """带状净利润（多/空）、投入 S、单边成本比例 c（冲击按资金片规模计算，保守）。"""
    cost = pr.asset.cost
    H = pr.hold.shape[1]
    S = np.minimum(size_cap, np.minimum(avail[:, None], _shift_band(avail, H)))
    S = np.where(np.isnan(S) | (S < cost.p.min_tx_usd) | np.isnan(pr.p[:, None]) | np.isnan(pr.p_s), 0.0, S)
    c = cost.proportional_side_cost() + cost.impact_fraction(size_cap)
    gas = 2 * cost.p.gas_usd * max(1, math.ceil(size_cap / cost.p.max_tx_usd))
    fund = S * cap_cost * pr.hold / BARS_PER_YEAR
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = pr.p_s / pr.p[:, None]
        pi_l = S * (ratio * (1 - c) / (1 + c) - 1) - gas - fund
    pi_l = np.where(S > 0, pi_l, -np.inf)
    pi_s = None
    if pr.asset.cap.short:
        borrow = pr.asset.cap.short_borrow_rate_annual
        with np.errstate(invalid="ignore", divide="ignore"):
            pi_s = S * (1 - ratio * (1 + c) / (1 - c)) - gas - fund - S * borrow * pr.hold / BARS_PER_YEAR
        pi_s = np.where(S > 0, pi_s, -np.inf)
    return pi_l, pi_s, S, c, fund, gas


def _dp_band(band: np.ndarray) -> tuple[float, list[tuple[int, int]]]:
    """最大权重不相交区间 DP（带状）：best[j] = 前 j 格内最优；返回 (b, s) 列表。"""
    n, H = band.shape
    best = np.zeros(n + 1)
    choice = np.full(n + 1, -1, dtype=np.int64)
    hs_all = np.arange(1, H + 1)
    for j in range(2, n + 1):
        s = j - 1
        hmax = min(H, s)
        hs = hs_all[:hmax]
        bs = s - hs
        vals = best[bs] + band[bs, hs - 1]
        i = int(np.argmax(vals))
        if vals[i] > best[j - 1]:
            best[j], choice[j] = vals[i], bs[i]
        else:
            best[j] = best[j - 1]
    pairs = []
    j = n
    while j > 0:
        if choice[j] >= 0:
            pairs.append((int(choice[j]), j - 1))
            j = choice[j]
        else:
            j -= 1
    pairs.reverse()
    return float(best[-1]), pairs


# ----------------------------------------------------------------------------- 一次路径求解（固定 k、λ）
def _solve(pres: list[_Pre], capital: float, k: int, lam: float, cap_cost: float) -> list[PlaybookTrade]:
    avail = [pr.liq.copy() for pr in pres]
    trades: list[PlaybookTrade] = []
    size_cap = capital / k
    H = pres[0].hold.shape[1]
    for t_id in range(k):
        best = np.full((GRID_MINUTES, H), -np.inf)
        who = np.full((GRID_MINUTES, H), -1, dtype=np.int16)
        direc = np.zeros((GRID_MINUTES, H), dtype=np.int8)
        cache = []
        for i, pr in enumerate(pres):
            pi_l, pi_s, S, c, fund, gas = _asset_bands(pr, avail[i], size_cap, cap_cost)
            cache.append((pi_l, pi_s, S, c, fund, gas))
            sc = pi_l - lam * S * pr.mae_l
            m = sc > best
            best, who, direc = np.where(m, sc, best), np.where(m, i, who), np.where(m, 0, direc)
            if pi_s is not None:
                sc = pi_s - lam * S * pr.mae_s
                m = sc > best
                best, who, direc = np.where(m, sc, best), np.where(m, i, who), np.where(m, 1, direc)
        if not np.isfinite(best).any():
            break
        total, pairs = _dp_band(best)
        if total <= 1e-9 or not pairs:
            break
        for b, s in pairs:
            h = s - b
            i = int(who[b, h - 1])
            pr = pres[i]
            pi_l, pi_s, S, c, fund, gas = cache[i]
            short = bool(direc[b, h - 1])
            size = float(S[b, h - 1])
            if size <= 0:
                continue
            net = float((pi_s if short else pi_l)[b, h - 1])
            pb, ps = float(pr.p[b]), float(pr.p[s])
            if short:
                entry, exit_ = pb * (1 - c), ps * (1 + c)
                qty = size / entry
                mae = float(pr.mae_s[b, h - 1])
            else:
                entry, exit_ = pb * (1 + c), ps * (1 - c)
                qty = size / entry
                mae = float(pr.mae_l[b, h - 1])
            f = float(fund[b, h - 1])
            trade_cost = size * c + qty * ps * c + gas            # 入场成本 + 出场成本 + Gas（价格内成本按比例折算）
            trades.append(PlaybookTrade(0, t_id + 1, pr.asset.symbol, pr.asset.asset_class, "short" if short else "long", int(b), int(s),
                                        pr.ts[b], pb, entry, pr.ts[s], ps, exit_, size, qty, float(trade_cost), f, float(gas), mae, size * mae, net,
                                        net / size if size else 0.0, int(h)))
            avail[i][b] = max(0.0, avail[i][b] - size)
            avail[i][s] = max(0.0, avail[i][s] - size)
    return trades


def _equity_curve(trades: list[PlaybookTrade], pres: list[_Pre]) -> np.ndarray:
    """逐分钟盯市权益：已实现 + 未实现（按各资产收盘价、不含平仓成本）。"""
    eq = np.zeros(GRID_MINUTES)
    by_sym = {pr.asset.symbol: pr for pr in pres}
    for t in trades:
        pr = by_sym[t.symbol]
        px = pr.p[t.entry_grid + 1:t.exit_grid]
        unreal = t.qty * (px - t.entry_price) if t.direction == "long" else t.qty * (t.entry_price - px)
        eq[t.entry_grid + 1:t.exit_grid] += np.nan_to_num(unreal, nan=0.0)
        eq[t.exit_grid:] += t.net_pnl_usd
    return eq


def _metrics(trades: list[PlaybookTrade], pres: list[_Pre], weights: dict) -> dict:
    eq = _equity_curve(trades, pres)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(trades) else 0.0
    mae = float(sum(t.mae_usd for t in trades))
    cost = float(sum(t.cost_usd for t in trades))
    n = len(trades)
    risk = (weights.get("w_drawdown", 1.0) * dd + weights.get("w_mae", 0.5) * mae + weights.get("w_cost", 1.0) * cost
            + weights.get("per_trade_penalty_usd", 1.0) * n)
    return {"max_drawdown_usd": dd, "sum_mae_usd": mae, "cost_usd": cost, "n_trades": n, "risk_score_usd": float(risk), "equity": eq}


def _merge_same_interval(trades: list[PlaybookTrade]) -> list[PlaybookTrade]:
    """同一资产、同一方向、同一进出场时刻的多片成交合并为一笔（本质是同一订单的分片）。"""
    groups: dict[tuple, PlaybookTrade] = {}
    for t in trades:
        key = (t.symbol, t.direction, t.entry_grid, t.exit_grid)
        if key in groups:
            g = groups[key]
            tot = g.size_usd + t.size_usd
            g.entry_price = (g.entry_price * g.size_usd + t.entry_price * t.size_usd) / tot
            g.exit_price = (g.exit_price * g.size_usd + t.exit_price * t.size_usd) / tot
            g.size_usd, g.qty = tot, g.qty + t.qty
            g.cost_usd += t.cost_usd; g.funding_usd += t.funding_usd; g.gas_usd += t.gas_usd; g.net_pnl_usd += t.net_pnl_usd
            g.mae_usd += t.mae_usd; g.net_ret = g.net_pnl_usd / g.size_usd
        else:
            groups[key] = PlaybookTrade(**t.to_dict())
    return list(groups.values())


def _scale_trade(t: PlaybookTrade, ratio: float) -> None:
    """把一笔交易的仓位按比例缩小（Gas 固定，其余线性）。"""
    t.size_usd *= ratio; t.qty *= ratio; t.mae_usd *= ratio; t.funding_usd *= ratio
    t.cost_usd = (t.cost_usd - t.gas_usd) * ratio + t.gas_usd
    t.net_pnl_usd = (t.net_pnl_usd + t.gas_usd) * ratio - t.gas_usd
    t.net_ret = t.net_pnl_usd / t.size_usd if t.size_usd else 0.0


def _truncate_to_target(trades: list[PlaybookTrade], target: float, min_tx_usd: float = 1.0, buffer: float = 0.002) -> tuple[list[PlaybookTrade], str | None]:
    """按平仓时间排序并截断到首次达标（之后的交易不做）；最后一笔按需要缩小仓位，使累计净收益 ≈ 目标×(1+buffer)，
    不多承担不必要的仓位与风险（审计：T1 目标 24 不再输出 76）。"""
    tr = sorted(_merge_same_interval(trades), key=lambda t: (t.exit_grid, t.entry_grid, t.tranche))
    cum, out, hit = 0.0, [], None
    goal = target * (1 + buffer)
    for t in tr:
        need = goal - cum
        if t.net_pnl_usd > need > 0:
            ratio = (need + t.gas_usd) / (t.net_pnl_usd + t.gas_usd)
            if t.size_usd * ratio >= min_tx_usd and 0 < ratio < 1:
                _scale_trade(t, ratio)
        cum += t.net_pnl_usd
        t.cum_net_pnl_usd = cum
        out.append(t)
        if hit is None and cum >= target - 1e-9:
            hit = t.exit_ts
            t.reaches_target = True
            break
    for i, t in enumerate(out, 1):
        t.no = i
    return out, hit


def _finalize_order(trades: list[PlaybookTrade]) -> list[PlaybookTrade]:
    tr = sorted(_merge_same_interval(trades), key=lambda t: (t.exit_grid, t.entry_grid, t.tranche))
    cum = 0.0
    for i, t in enumerate(tr, 1):
        cum += t.net_pnl_usd
        t.cum_net_pnl_usd, t.no = cum, i
    return tr


def _binding_constraints(pres: list[_Pre], trades: list[PlaybookTrade], capital: float, target: float, participation: float, cap_cost: float) -> list[str]:
    """不可达时的真实限制因素：流动性预算、无流动性上限时的理论最大、成本占比、资金占用。"""
    out = []
    budget = sum(float(np.sum(np.where(np.isfinite(pr.liq), pr.liq, 0.0))) for pr in pres)
    used = sum(2 * t.size_usd for t in trades)
    out.append(f"流动性：参与率 {participation:.0%} 下资产池全日可成交 {budget:,.0f} USD（已用 {used / budget:.0%}）；本金 {capital:,.0f} 需要往返成交 ≥ {2 * capital:,.0f} USD 才能满仓一次")
    # 无流动性上限（但仍含成本）的理论最大：单片全仓
    inf_liq = [np.full_like(pr.liq, np.inf) for pr in pres]
    best = np.full((GRID_MINUTES, pres[0].hold.shape[1]), -np.inf)
    for pr, av in zip(pres, inf_liq):
        pi_l, pi_s, S, c, fund, gas = _asset_bands(pr, av, capital, cap_cost)
        best = np.maximum(best, pi_l)
        if pi_s is not None:
            best = np.maximum(best, pi_s)
    theo, _ = _dp_band(best)
    out.append(f"若不限流动性（仍扣全部成本、单片全仓）理论最大净收益约 {theo:,.0f} USD → " + ("目标在数学上可达，瓶颈是盘口容量/冲击" if theo >= target else "即使不限流动性也不达标，瓶颈是行情本身与成本"))
    cost = sum(t.cost_usd + t.funding_usd for t in trades)
    gross = sum(t.net_pnl_usd for t in trades) + cost
    if gross > 0:
        out.append(f"成本：路径毛利 {gross:,.0f} USD 中成本 {cost:,.0f} USD（{cost / gross:.0%}）")
    peak = 0.0
    if trades:
        dep = np.zeros(GRID_MINUTES)
        for t in trades:
            dep[t.entry_grid:t.exit_grid + 1] += t.size_usd
        peak = float(dep.max())
    out.append(f"资金占用：峰值 {peak:,.0f} USD（{peak / capital:.0%} 本金）；低于 100% 说明同一时刻可成交量不足以用满本金，而不是资金不够")
    return out


# ----------------------------------------------------------------------------- 主入口
def optimize_playbook(assets: list[AssetDay], capital_usd: float, target_usd: float, day: str, *, mode: str = "cross", tier: str | None = None,
                      participation: float = 0.10, tx_amount: dict | None = None, k_grid=(1, 2, 4, 8, 16, 32, 64, 128, 256),
                      lam_grid=(0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0), weights: dict | None = None, cap_cost_annual: float = 0.05, min_tranche_usd: float = 500.0,
                      early_stop_gain: float = 0.02, max_hold_min: int = 480) -> PlaybookResult:
    from .universe_time import grid_ny_strings

    weights = weights or {}
    pres = _precompute(assets, participation, tx_amount, int(max_hold_min), grid_ny_strings(day))
    limits: list[str] = []
    sources = {a.symbol: a.source for a in assets}
    liq_srcs = {pr.asset.symbol: pr.liq_src for pr in pres}
    sim = any(a.simulated for a in assets)
    if not pres:
        return PlaybookResult(day, tier, capital_usd, target_usd, mode, [a.symbol for a in assets], False, 0.0, 0.0, [], 0, 0, 0, 0, [], 0, 0.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, None, 0.0, None, 0.0, target_usd, [], [0.0] * GRID_MINUTES,
                              ["资产池内无可交易方向（能力未核实或无数据）"], sources, liq_srcs, sim, weights)
    ks = [k for k in k_grid if k == 1 or capital_usd / k >= min_tranche_usd] or [1]
    candidates: list[dict] = []
    best_max, best_max_trades, best_max_k, prev = -np.inf, [], 1, -np.inf
    # 1) λ=0：各 k 的最高可实现净收益（早停）
    base: dict[int, list[PlaybookTrade]] = {}
    for k in ks:
        tr = _solve(pres, capital_usd, k, 0.0, cap_cost_annual)
        tot = sum(t.net_pnl_usd for t in tr)
        base[k] = tr
        if tot > best_max:
            best_max, best_max_trades, best_max_k = tot, tr, k
        if k > 1 and prev > 0 and tot <= prev * (1 + early_stop_gain):
            break
        prev = max(prev, tot)
    max_achievable = float(max(best_max, 0.0))
    # 2) 对能达标的 k：二分搜索最大 λ（仍达标），截断到首次达标，记录候选
    for k, tr0 in base.items():
        if sum(t.net_pnl_usd for t in tr0) < target_usd - 1e-9:
            continue
        best_lam, best_tr = 0.0, tr0
        for lam in lam_grid:                       # 几何递增，首次不达标即停（λ 越大越偏好低风险区间、利润越低）
            tr = _solve(pres, capital_usd, k, lam, cap_cost_annual)
            if sum(t.net_pnl_usd for t in tr) >= target_usd - 1e-9:
                best_lam, best_tr = lam, tr
            else:
                break
        min_tx = max(pr.asset.cost.p.min_tx_usd for pr in pres)
        for lam, tr in ((best_lam, best_tr), (0.0, tr0)):
            cut, hit = _truncate_to_target([PlaybookTrade(**t.to_dict()) for t in tr], target_usd, min_tx)
            m = _metrics(cut, pres, weights)
            candidates.append({"k": k, "lam": round(lam, 4), "net_usd": float(sum(t.net_pnl_usd for t in cut)), "n_trades": len(cut), "hit_ts": hit,
                               "max_drawdown_usd": m["max_drawdown_usd"], "sum_mae_usd": m["sum_mae_usd"], "cost_usd": m["cost_usd"],
                               "risk_score_usd": m["risk_score_usd"], "_trades": cut, "_eq": m["equity"]})
    if candidates:
        chosen = min(candidates, key=lambda c: (c["risk_score_usd"], c["n_trades"]))
        trades, eq, met, hit = chosen["_trades"], chosen["_eq"], True, chosen["hit_ts"]
        k_used, lam_used = chosen["k"], chosen["lam"]
    else:
        trades = _finalize_order([PlaybookTrade(**t.to_dict()) for t in best_max_trades])
        m = _metrics(trades, pres, weights)
        eq, met, hit, k_used, lam_used = m["equity"], False, None, best_max_k, 0.0
        if max_achievable < target_usd:
            limits.append(f"目标 {target_usd:,.0f} USD 在真实成本与流动性约束下不可达：事后最高可实现净收益 {max_achievable:,.0f} USD，缺口 {target_usd - max_achievable:,.0f} USD")
            limits += _binding_constraints(pres, trades, capital_usd, target_usd, participation, cap_cost_annual)
    m = _metrics(trades, pres, weights)
    # 切换次数：按平仓时间顺序相邻交易资产不同
    sw = sum(1 for a, b in zip(trades, trades[1:]) if a.symbol != b.symbol)
    deployed = np.zeros(GRID_MINUTES)
    for t in trades:
        deployed[t.entry_grid:t.exit_grid + 1] += t.size_usd
    used = None
    if all(pr.liq_src != "unknown" for pr in pres):
        budget = sum(float(np.sum(pr.liq)) for pr in pres)
        used_amt = sum(2 * t.size_usd for t in trades)
        used = float(min(1.0, used_amt / budget)) if budget > 0 else None
    else:
        limits.append("部分资产流动性未知：未施加成交量上限，结果不可验证")
    if any(a.cap.short for a in assets) is False:
        limits.append("仅做多：资产池内无登记核实的做空渠道（现货卖出只用于平多）")
    limits.append("资金费率：现货无资金费率；做空借券费与资金成本已按年化计提")
    net = float(sum(t.net_pnl_usd for t in trades))
    return PlaybookResult(day, tier, capital_usd, target_usd, mode, [a.symbol for a in assets], met, net, net / capital_usd if capital_usd else 0.0,
                          trades, len(trades), sum(t.direction == "long" for t in trades), sum(t.direction == "short" for t in trades), sw,
                          sorted({t.symbol for t in trades}), k_used, float(lam_used), m["max_drawdown_usd"], m["sum_mae_usd"], m["cost_usd"],
                          float(sum(t.funding_usd for t in trades)), m["risk_score_usd"], hit, float(deployed.max()) if len(trades) else 0.0, used,
                          max_achievable, float(max(0.0, target_usd - net)),
                          [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates], eq.tolist(), limits, sources, liq_srcs, sim,
                          {"primary": "累计净收益 ≥ 目标", "secondary": "min R = w_dd×最大回撤 + w_mae×Σ最大不利波动 + w_cost×成本 + 每笔罚金",
                           "weights": {"w_drawdown": weights.get("w_drawdown", 1.0), "w_mae": weights.get("w_mae", 0.5), "w_cost": weights.get("w_cost", 1.0),
                                       "per_trade_penalty_usd": weights.get("per_trade_penalty_usd", 1.0)}, "lambda_grid": list(lam_grid), "max_hold_min": int(max_hold_min),
                           "impact_note": "冲击按资金片规模计算（保守）"})
