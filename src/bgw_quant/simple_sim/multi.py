"""多笔分批（多仓位并行）可执行模拟 + 理论最优对照 + 失败原因分解（已知昨日完整走势的事后教学分析）。

可执行模拟（B）：
  * 同时最多 max_open 个仓位；每个仓位是一笔独立的小额交易（分批成交、受逐根流动性预算限制，流动性在各仓位间共享、不重复计算）。
  * 每笔的平仓点在开仓时按“止损 0.5% 内 + 扣费净收益 ≥ 最低要求”的规则事后选定；累计已实现净利润达到目标后停止开新仓。
  * 每笔金额 = min(可用本金, 本金×仓位比例/max_open, 当下可用流动性)，按本金自动调整；不加杠杆、不加仓。
  * 成本：手续费、价差、滑点、冲击、Gas、资金成本、借券费（做空仅限核实品种）。
  * 参数（max_open、最低单笔净收益）在小网格内按“达标优先、其次净利润”事后挑选，标记为“事后调参”。
理论最优（A）：
  * 动态规划最大化净利润，只扣手续费/价差/滑点/Gas，不考虑流动性与冲击，单次全仓、任意次数、仅做多 —— 纯上限，不可执行。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel, CostProfile
from ..hindsight.optimal_path import OptimalPathResult, optimal_path
from ..target_model.execution import BatchFill, bar_liquidity, batch_fill
from .engine import DISCLAIMER, SimTrade, SimpleSimConfig, _pnl, _search_exit, shortable


@dataclass
class MultiParams:
    max_open: int = 4
    min_trade_net_pct: float = 0.1
    position_fraction: float = 1.0
    spacing_bars: int = 1

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MultiResult:
    symbol: str
    day: str
    source: str
    capital_usd: float
    target_usd: float
    tier: str | None
    params: MultiParams
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    target_hit_ts: str | None
    n_trades: int
    n_long: int
    n_short: int
    shortable: bool
    max_concurrent: int
    peak_deployed_usd: float
    avg_deployed_usd: float            # 时间加权平均占用资金
    capital_utilization: float         # 峰值占用 / 本金
    avg_utilization: float
    trading_cost_usd: float
    capital_cost_usd: float
    liquidity_used_ratio: float        # 已用流动性 / 全日可用流动性预算
    trades: list[SimTrade]
    cum_curve: list[float]
    stop_reason: str | None
    unverifiable: bool
    data_is_simulated: bool
    notes: list[str] = field(default_factory=list)
    hindsight_tuned: bool = True
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trades", "params")}
        d["params"] = self.params.to_dict()
        d["trades"] = [t.to_dict() for t in self.trades]
        d["n_buys"] = self.n_long + self.n_short      # 每笔一开一平
        d["n_sells"] = self.n_long + self.n_short
        return d


def simulate_day_multi(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: SimpleSimConfig,
                       params: MultiParams, *, symbol: str | None = None, tier: str | None = None, allow_short: bool | None = None,
                       short_borrow_annual: float | None = None) -> MultiResult:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]) if n else ""
    can_short, short_info = (allow_short, {}) if allow_short is not None else shortable(symbol)
    borrow = short_borrow_annual if short_borrow_annual is not None else float(short_info.get("short_borrow_rate_annual", cfg.short_borrow_rate_annual))
    close, low, high = bars["close"].to_numpy(float), bars["low"].to_numpy(float), bars["high"].to_numpy(float)
    liq = bar_liquidity(bars, cfg.participation_rate)
    liquidity_known = bool(np.isfinite(liq).all())
    avail = liq.copy()                                   # 共享流动性预算（各仓位不重复使用）
    local_cfg = SimpleSimConfig(**{**cfg.__dict__, "min_trade_net_pct": params.min_trade_net_pct})
    frac = min(params.position_fraction, 1.0)
    tranche_plan = capital_usd * frac / max(1, params.max_open)
    open_pos: list[dict] = []           # {close_end, net, size}
    trades: list[SimTrade] = []
    cum, curve = 0.0, np.zeros(n)
    deployed = np.zeros(n)
    last_open = -10**9
    hit_ts, stop = None, None
    last_entry = n - 1 - cfg.flat_before_close_bars
    peak = 0.0
    for i in range(n):
        # 1) 结算在本根完成平仓的仓位
        for p in [p for p in open_pos if p["close_end"] == i]:
            cum += p["net"]
            open_pos.remove(p)
            if cum >= target_usd - 1e-9 and hit_ts is None:
                hit_ts = p["ts"]
        curve[i] = cum
        deployed[i] = sum(p["size"] for p in open_pos)
        peak = max(peak, deployed[i])
        # 2) 决定是否开新仓
        if stop is None:
            if cum >= target_usd - 1e-9:
                stop = "累计净利润已达目标，停止开新仓"
            elif cum <= -cfg.max_daily_loss_pct / 100 * capital_usd:
                stop = "触及当日亏损上限，停止开新仓"
        if stop or i > last_entry or len(open_pos) >= params.max_open or i - last_open < params.spacing_bars:
            continue
        free_cap = capital_usd - sum(p["size"] for p in open_pos)
        size = min(free_cap, tranche_plan)
        if size < max(cost.p.min_tx_usd, 0.001 * capital_usd):
            continue
        need = target_usd - cum - sum(p["net"] for p in open_pos)
        cands = []
        r = _search_exit(bars, i, "long", cost, local_cfg, avail, close, low, high, size, borrow, need)
        if r:
            cands.append(("long", r))
        if can_short:
            r = _search_exit(bars, i, "short", cost, local_cfg, avail, close, low, high, size, borrow, need)
            if r:
                cands.append(("short", r))
        if not cands:
            continue
        direction, (of, cf, s, net, tc, cc, mae) = min(cands, key=lambda c: (0 if c[1][3] >= need - 1e-9 else 1, c[1][2]))
        # 占用共享流动性
        for f in of.fills + cf.fills:
            avail[f.idx] = max(0.0, avail[f.idx] - f.notional_usd)
        open_pos.append({"close_end": cf.end_idx, "net": net, "size": of.filled_usd, "ts": cf.fills[-1].ts_ny})
        trades.append(SimTrade(len(trades) + 1, direction, of, cf, of.filled_usd, of.qty, of.avg_exec, cf.avg_exec, tc, cc, net,
                               net / of.filled_usd, mae, cf.end_idx - of.start_idx, 0.0))
        last_open = i
    # 收尾：仍未结算的仓位（不应发生，平仓点都在日内）
    for p in open_pos:
        cum += p["net"]
    curve[-1] = cum if n else 0.0
    # 按平仓时间排序并填累计
    trades.sort(key=lambda t: (t.close_fill.end_idx, t.seq))
    running = 0.0
    for k, t in enumerate(trades, 1):
        running += t.net_pnl_usd
        t.cum_net_pnl_usd = running
        t.seq = k
    met = cum >= target_usd - 1e-9
    notes = []
    if not can_short:
        notes.append("未核实可做空渠道，仅做多")
    if not liquidity_known:
        notes.append("无成交额字段：流动性未知，结果不可验证")
    if stop:
        notes.append(stop)
    if not met:
        notes.append((f"全日无满足“止损 {cfg.stop_loss_pct}% 内且扣费净收益 ≥ {params.min_trade_net_pct:.2f}%”的机会") if not trades else
                     f"可执行机会累计 {cum:,.2f} USD 低于目标 {target_usd:,.2f} USD")
    n_long = sum(t.direction == "long" for t in trades)
    budget = float(np.sum(liq[np.isfinite(liq)])) if liquidity_known else float("nan")
    used = float(np.sum(liq[np.isfinite(liq)] - avail[np.isfinite(liq)])) if liquidity_known else float("nan")
    return MultiResult(symbol, day, str(bars["source"].iloc[0]) if n else "", capital_usd, target_usd, tier, params, cum,
                       cum / capital_usd if capital_usd else 0.0, met, hit_ts, len(trades), n_long, len(trades) - n_long, can_short,
                       int(max((sum(1 for t in trades if t.open_fill.start_idx <= i <= t.close_fill.end_idx) for i in range(n)), default=0)),
                       peak, float(deployed.mean()) if n else 0.0, peak / capital_usd if capital_usd else 0.0,
                       float(deployed.mean()) / capital_usd if capital_usd else 0.0,
                       float(sum(t.trading_cost_usd for t in trades)), float(sum(t.capital_cost_usd for t in trades)),
                       (used / budget) if liquidity_known and budget > 0 else float("nan"), trades, curve.tolist(), stop,
                       cost.is_unverifiable(peak) or not liquidity_known, bool((bars["quality"].astype(int) & 128).any()) if n else False, notes)


DEFAULT_GRID = {"max_open": [1, 4, 8, 16, 32], "min_trade_net_pct": [0.1, 0.3]}


def auto_search(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: SimpleSimConfig, *,
                grid: dict | None = None, symbol: str | None = None, tier: str | None = None) -> tuple[MultiResult, list[dict]]:
    """在小网格内事后挑选参数：达标优先（最早达标、最少笔数），否则净利润最高。返回最佳结果与全部尝试记录。"""
    grid = grid or DEFAULT_GRID
    keys = list(grid)
    tried = []
    best, best_key = None, None
    for combo in itertools.product(*[grid[k] for k in keys]):
        p = MultiParams(**dict(zip(keys, combo)))
        r = simulate_day_multi(bars, cost, capital_usd, target_usd, cfg, p, symbol=symbol, tier=tier)
        hit_idx = next((t.close_fill.end_idx for t in r.trades if t.cum_net_pnl_usd >= target_usd - 1e-9), 10**9) if r.target_met else 10**9
        key = (0 if r.target_met else 1, hit_idx if r.target_met else -r.realized_net_usd, r.n_trades)
        tried.append({**p.to_dict(), "realized_net_usd": r.realized_net_usd, "n_trades": r.n_trades, "target_met": r.target_met,
                      "avg_utilization": r.avg_utilization})
        if best_key is None or key < best_key:
            best, best_key = r, key
    return best, tried


def theoretical_profile(profile: CostProfile) -> CostProfile:
    """理论最优的成本：保留手续费/价差/滑点/Gas，去掉冲击与流动性限制。"""
    p = profile.scaled()
    p.impact_coeff = 0.0
    p.depth_usd = 1e18
    p.max_tx_usd = 1e18
    return p


def theoretical_optimum(bars: pd.DataFrame, profile: CostProfile, capital_usd: float, target_usd: float, symbol: str | None = None) -> OptimalPathResult:
    cm = CostModel(theoretical_profile(profile))
    r = optimal_path(bars, cm, capital_usd, symbol=symbol, participation_rate=1e12, target_return=target_usd / capital_usd,
                     cost_profile="theoretical(no liquidity/impact)")
    r.notes.insert(0, "理论最优：单次全仓、不限次数、仅做多、不计流动性与冲击 —— 纯上限，不可执行")
    return r


def failure_analysis(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: SimpleSimConfig,
                     theo: OptimalPathResult, exe: MultiResult) -> dict:
    """把“为什么没达标”拆成可量化的原因。"""
    close = bars["close"].to_numpy(float)
    liq = bar_liquidity(bars, cfg.participation_rate)
    liquidity_known = bool(np.isfinite(liq).all())
    tranche = exe.params.position_fraction * capital_usd / max(1, exe.params.max_open)
    # 成本门槛按“实际能成交的单笔规模”计算：有交易取中位投入，否则取计划与分批可成交量的较小者
    if exe.trades:
        size_ref = float(np.median([t.size_usd for t in exe.trades]))
    else:
        per_batch = float(np.nanmedian(liq)) * cfg.batch_bars if liquidity_known else tranche
        size_ref = min(tranche, per_batch, capital_usd)
    rt_cost = cost.round_trip_fraction(size_ref)
    h = min(cfg.max_hold_bars, 60)
    moves = np.abs(close[h:] / close[:-h] - 1) if len(close) > h else np.array([0.0])
    p90 = float(np.percentile(moves, 90)) if len(moves) else 0.0
    day_budget = float(np.sum(liq)) if liquidity_known else float("nan")
    reasons = []
    if theo.max_net_profit_usd < target_usd:
        reasons.append(f"目标高于理论上限：即使不计流动性与冲击、单次全仓、任意次数，当日最高净利润也只有 {theo.max_net_profit_usd:,.2f} USD（目标 {target_usd:,.2f}）")
    if rt_cost > p90:
        reasons.append(f"成本门槛高于行情波动：单笔往返成本 {rt_cost:.2%}（含冲击）超过 {h} 根内价格变动的 90 分位 {p90:.2%}，可行机会稀少")
    if liquidity_known and exe.capital_utilization < 0.5:
        reasons.append(f"流动性容量不足：峰值占用仅 {exe.capital_utilization:.0%} 本金；全日 {cfg.participation_rate:.0%} 参与率可用流动性 {day_budget:,.0f} USD，已用 {exe.liquidity_used_ratio:.0%}")
    if exe.n_trades == 0:
        reasons.append("无任何满足止损与最低净收益的可执行机会")
    elif not exe.target_met:
        reasons.append(f"机会数量不足：{exe.n_trades} 笔可执行交易累计 {exe.realized_net_usd:,.2f} USD，缺口 {target_usd - exe.realized_net_usd:,.2f} USD")
    if not liquidity_known:
        reasons.append("流动性未知（数据源无成交额），无法验证容量")
    return {"round_trip_cost_pct": rt_cost * 100, "size_ref_usd": size_ref, "move_p90_pct": p90 * 100, "move_median_pct": float(np.median(moves)) * 100 if len(moves) else 0.0,
            "day_liquidity_budget_usd": day_budget, "tranche_usd": tranche, "theoretical_max_usd": theo.max_net_profit_usd,
            "executable_usd": exe.realized_net_usd, "gap_to_target_usd": max(0.0, target_usd - exe.realized_net_usd),
            "theory_vs_exec_gap_usd": theo.max_net_profit_usd - exe.realized_net_usd, "reasons": reasons}
