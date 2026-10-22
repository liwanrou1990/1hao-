"""统一收益决策引擎（因果：第 i 根收盘决策，第 i+1 根起分批成交；绝不使用未来K线）。

每根K线：
  1) 结算到期/触发出场的仓位；2) 更新状态（趋势、区间、波动、流动性、成本门槛）；
  3) 选择模式：trend_long / trend_short / range_long / range_short / pause；
  4) 定仓：单笔 = min(本金/并行数, 过去 N 根成交额×参与率×分批根数, 单笔上限) × 波动率缩放；
  5) 频率：冷却、并行上限、API 限频（每分钟子单数）；达标/日亏损上限 → 暂停。
方向只在 ProductCapability 允许的集合内选择；没有登记做空渠道就绝不做空，现货卖出只用于平多。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import load_yaml
from ..costs.model import CostModel
from ..target_model.execution import BatchFill, bar_liquidity, batch_fill
from .capability import ProductCapability

BARS_PER_YEAR = 252 * 390
DISCLAIMER_ENGINE = "因果决策引擎：每根K线收盘后决策、下一根起分批成交，不使用未来信息；目标收益不是保证收益。"


@dataclass
class EngineConfig:
    risk: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    grid: dict = field(default_factory=dict)
    objective: dict = field(default_factory=dict)


def load_engine_config() -> EngineConfig:
    raw = load_yaml("decision_engine.yaml")
    return EngineConfig(**{k: raw.get(k, {}) for k in ("risk", "execution", "features", "grid", "objective")})


@dataclass
class EngineParams:
    z_entry: float = 1.5
    tp_atr_mult: float = 1.5
    max_open: int = 4
    trend_thresh: float = 0.5
    edge_min_ratio: float = 1.2

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Position:
    direction: str
    mode: str
    fill: BatchFill
    entry_idx: int
    peak: float            # 多头最高价 / 空头最低价（用于跟踪止损）
    tp_net_usd: float


@dataclass
class EngineTrade:
    seq: int
    direction: str
    mode: str
    open_fill: BatchFill
    close_fill: BatchFill
    size_usd: float
    qty: float
    avg_open: float
    avg_close: float
    trading_cost_usd: float
    funding_cost_usd: float
    net_pnl_usd: float
    net_ret: float
    hold_bars: int
    reason_exit: str
    cum_net_pnl_usd: float = 0.0
    partial: bool = False

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("open_fill", "close_fill")}
        d["open_fill"], d["close_fill"] = self.open_fill.to_dict(), self.close_fill.to_dict()
        d["open_ts"], d["close_ts"] = self.open_fill.fills[0].ts_ny, self.close_fill.fills[-1].ts_ny
        d["open_idx"], d["close_idx"] = self.open_fill.start_idx, self.close_fill.end_idx
        return d


@dataclass
class DayRun:
    symbol: str
    day: str
    capital_usd: float
    target_usd: float
    params: EngineParams
    capability: ProductCapability
    trades: list[EngineTrade]
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    target_hit_ts: str | None
    n_trades: int
    n_long: int
    n_short: int
    win_rate: float | None
    max_drawdown_usd: float
    peak_deployed_usd: float
    avg_deployed_usd: float
    execution_cost_usd: float          # 手续费/价差/滑点/冲击/Gas（价格内成本 + Gas）
    funding_cost_usd: float            # 资金成本 + 借券费
    mode_counts: dict
    pause_reasons: dict
    stop_reason: str | None
    partial_fills: int
    unverifiable: bool
    cum_curve: list[float]
    data_is_simulated: bool
    disclaimer: str = DISCLAIMER_ENGINE

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trades", "params", "capability")}
        d["params"], d["capability"] = self.params.to_dict(), self.capability.to_dict()
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


def _gas(fill: BatchFill, cost: CostModel) -> float:
    return sum(cost.p.gas_usd * max(1, int(np.ceil(f.notional_usd / cost.p.max_tx_usd))) for f in fill.fills)


def _features(bars: pd.DataFrame, fc: dict) -> dict[str, np.ndarray]:
    c = bars["close"].astype(float)
    ema_f = c.ewm(span=int(fc.get("ema_fast", 10)), adjust=False).mean()
    ema_s = c.ewm(span=int(fc.get("ema_slow", 30)), adjust=False).mean()
    prev = c.shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(), (bars["low"] - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / int(fc.get("atr_window", 14)), adjust=False, min_periods=int(fc.get("atr_window", 14))).mean()
    zw = int(fc.get("z_window", 30))
    m, sd = c.rolling(zw, min_periods=zw).mean(), c.rolling(zw, min_periods=zw).std(ddof=0)
    z = (c - m) / sd.replace(0, np.nan)
    rv = np.log(c).diff().rolling(int(fc.get("rv_window", 30)), min_periods=10).std() * np.sqrt(390)   # 实现日波动
    trend = (ema_f - ema_s) / atr.replace(0, np.nan)
    return {"atr": atr.to_numpy(), "z": z.to_numpy(), "rv": rv.to_numpy(), "trend": trend.to_numpy(), "close": c.to_numpy()}


def run_engine_day(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: EngineConfig, params: EngineParams,
                   cap: ProductCapability, symbol: str | None = None) -> DayRun:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]) if n else ""
    risk, ex = cfg.risk, cfg.execution
    sl = float(risk.get("stop_loss_pct", 0.5)) / 100
    max_hold = int(risk.get("max_hold_bars", 60))
    flat_before = int(risk.get("flat_before_close_bars", 5))
    vol_target = float(risk.get("vol_target_daily", 0.02))
    part = float(ex.get("participation_rate", 0.1))
    batch = int(ex.get("batch_bars", 5))
    look = int(ex.get("liquidity_lookback", 20))
    rate_limit = int(ex.get("api_rate_limit_orders_per_min", 10))
    cap_cost_annual = float(ex.get("capital_cost_annual", 0.05))
    latency = max(1, int(ex.get("latency_bars", 1)))
    fill_rate = float(ex.get("fill_rate", 1.0))
    max_dd_pct = float(risk.get("max_intraday_drawdown_pct", 1.0)) / 100
    jump_pct = float(risk.get("anomaly_jump_pct", 3.0)) / 100
    anomaly_pause = int(risk.get("anomaly_pause_bars", 15))
    gap_pause = int(risk.get("data_gap_pause_bars", 5))
    low_liq_min = float(risk.get("low_liquidity_min_tranche_usd", 500))
    assert float(risk.get("max_leverage", 1.0)) <= 1.0, "V1 不允许杠杆"
    ts_arr = pd.to_datetime(bars["ts"]).to_numpy()
    paused_until = -1
    borrow = cap.short_borrow_rate_annual if cap.short else 0.0
    f = _features(bars, cfg.features)
    close, low, high = f["close"], bars["low"].to_numpy(float), bars["high"].to_numpy(float)
    liq = bar_liquidity(bars, part)
    liquidity_known = bool(np.isfinite(liq).all())
    avail = liq.copy()
    side_cost = cost.proportional_side_cost()
    positions: list[Position] = []
    trades: list[EngineTrade] = []
    cum, curve, deployed = 0.0, np.zeros(n), np.zeros(n)
    mode_counts: dict[str, int] = {}
    pause_reasons: dict[str, int] = {}
    stop = None
    hit_ts = None
    last_open = -10**9
    orders_this_bar = 0
    pending_entries: list[tuple[str, str, float, int]] = []      # (direction, mode, size, due_idx) 延迟后执行
    pending_exits: list[tuple[Position, str]] = []
    partial = 0
    peak_dep = 0.0
    max_concurrent_cap = max(1, params.max_open)

    def close_position(pos: Position, i: int, reason: str, max_bars: int):
        nonlocal cum, partial
        side = "sell" if pos.direction == "long" else "buy"
        req = pos.fill.qty * close[i] * (1.0 if pos.direction == "long" else (1 + 2 * side_cost + 0.002))
        fill = batch_fill(bars, i, side, req, cost, avail, max_bars, "open", qty_to_sell=pos.fill.qty if pos.direction == "long" else None)
        if not fill.fills:
            return False
        if pos.direction == "short":
            bought = fill.qty
            if bought > pos.fill.qty:
                fill = fill.scaled(pos.fill.qty / bought)
        ratio = min(1.0, fill.qty / pos.fill.qty)
        sold = pos.fill.scaled(ratio)
        for ch in fill.fills:
            avail[ch.idx] = max(0.0, avail[ch.idx] - ch.notional_usd)
        hold = fill.end_idx - pos.fill.start_idx
        gas = _gas(sold, cost) + _gas(fill, cost)
        funding = sold.filled_usd * (cap_cost_annual + (borrow if pos.direction == "short" else 0.0)) * hold / BARS_PER_YEAR
        gross = (fill.filled_usd - sold.filled_usd) if pos.direction == "long" else (sold.filled_usd - fill.filled_usd)
        net = gross - gas - funding
        cum += net
        trades.append(EngineTrade(len(trades) + 1, pos.direction, pos.mode, sold, fill, sold.filled_usd, sold.qty, pos.fill.avg_exec,
                                  fill.avg_exec, sold.cost_usd + fill.cost_usd, funding, net, net / sold.filled_usd if sold.filled_usd else 0.0,
                                  hold, reason + ("（部分平仓）" if ratio < 0.999 else ""), cum, ratio < 0.999))
        if ratio < 0.999:
            partial += 1
            pos.fill = pos.fill.scaled(1 - ratio)
            return False
        return True

    i = 0
    while i < n:
        orders_this_bar = 0
        # 1) 执行上一根决定的出场
        for pos, reason in pending_exits:
            if pos in positions:
                done = close_position(pos, i, reason, batch if not reason.startswith("日终") else max(1, n - i))
                if done:
                    positions.remove(pos)
        pending_exits = []
        # 2) 执行到期的入场（延迟 latency_bars，受 API 限频与成交率）
        still_pending = []
        for direction, mode, size, due in pending_entries:
            if due > i:
                still_pending.append((direction, mode, size, due))
                continue
            if len(positions) >= max_concurrent_cap or orders_this_bar >= rate_limit:
                pause_reasons["API限频/并行上限"] = pause_reasons.get("API限频/并行上限", 0) + 1
                continue
            side = "buy" if direction == "long" else "sell"
            fill = batch_fill(bars, i, side, size * fill_rate, cost, avail, batch, "open")
            if not fill.fills or fill.filled_usd < max(cost.p.min_tx_usd, 0.005 * size):
                pause_reasons["流动性不足未成交"] = pause_reasons.get("流动性不足未成交", 0) + 1
                continue
            for ch in fill.fills:
                avail[ch.idx] = max(0.0, avail[ch.idx] - ch.notional_usd)
            orders_this_bar += len(fill.fills)
            partial += int(fill.partial)
            atr_i = f["atr"][i] if np.isfinite(f["atr"][i]) else close[i] * 0.002
            tp_net = params.tp_atr_mult * atr_i * fill.qty
            positions.append(Position(direction, mode, fill, i, fill.avg_exec, tp_net))
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            last_open = i
            i = max(i, fill.end_idx)      # 分批期间不再决策
        pending_entries = still_pending
        dep = sum(p.fill.filled_usd for p in positions)
        deployed[i] = dep
        peak_dep = max(peak_dep, dep)
        curve[i] = cum
        if cum >= target_usd - 1e-9 and hit_ts is None:
            hit_ts = str(bars["ts_ny"].iloc[i])
        # 2b) 熔断：异常跳变 / 数据缺口 / 日内回撤
        if i > 0:
            jump = abs(close[i] / close[i - 1] - 1)
            gap_min = (ts_arr[i] - ts_arr[i - 1]) / np.timedelta64(1, "m")
            if jump >= jump_pct:
                paused_until = max(paused_until, i + anomaly_pause)
                pause_reasons["异常跳变熔断"] = pause_reasons.get("异常跳变熔断", 0) + 1
            elif gap_min > 1.5:
                paused_until = max(paused_until, i + gap_pause)
                pause_reasons["数据缺口暂停"] = pause_reasons.get("数据缺口暂停", 0) + 1
        if stop is None and (np.max(curve[: i + 1]) - cum) >= max_dd_pct * capital_usd and np.max(curve[: i + 1]) > 0:
            stop = "日内回撤熔断：累计净利润回撤超过上限，停止交易"
        # 3) 本根收盘决策（只用 ≤ i 的信息）
        atr_i, z_i, rv_i, tr_i = f["atr"][i], f["z"][i], f["rv"][i], f["trend"][i]
        # 3a) 出场判断
        for pos in positions:
            px = close[i]
            unreal = (pos.fill.qty * px * (1 - side_cost - cost.impact_fraction(pos.fill.filled_usd)) - pos.fill.filled_usd) if pos.direction == "long" \
                else (pos.fill.filled_usd - pos.fill.qty * px * (1 + side_cost + cost.impact_fraction(pos.fill.filled_usd)))
            adverse = (px / pos.fill.avg_ref - 1) if pos.direction == "long" else (1 - px / pos.fill.avg_ref)   # 相对参考价（不含成本）
            pos.peak = max(pos.peak, px) if pos.direction == "long" else min(pos.peak, px)
            trail = (pos.peak - px) / pos.peak if pos.direction == "long" else (px - pos.peak) / pos.peak
            held = i - pos.entry_idx
            if i >= n - 1 - batch:
                pending_exits.append((pos, "日终平仓"))
            elif unreal >= pos.tp_net_usd:
                pending_exits.append((pos, "止盈（净额达到 ATR 倍数）"))
            elif adverse <= -sl:
                pending_exits.append((pos, "止损 0.5%"))
            elif pos.mode.startswith("trend") and np.isfinite(atr_i) and trail * px >= atr_i * 1.0 and unreal > 0:
                pending_exits.append((pos, "趋势跟踪止盈回撤"))
            elif held >= max_hold:
                pending_exits.append((pos, "超过最长持有"))
            elif pos.mode.startswith("trend") and np.isfinite(tr_i) and ((pos.direction == "long" and tr_i < -params.trend_thresh) or (pos.direction == "short" and tr_i > params.trend_thresh)):
                pending_exits.append((pos, "趋势反转"))
        # 3b) 入场判断
        if stop is None:
            if cum >= target_usd - 1e-9:
                stop = "累计净利润已达目标，停止开新仓"
            elif cum <= -float(risk.get("max_daily_loss_pct", 1.5)) / 100 * capital_usd:
                stop = "触及当日亏损上限，暂停交易"
        can_enter = stop is None and i > paused_until and i <= n - 1 - flat_before and len(positions) < max_concurrent_cap \
            and (i - last_open) >= 1 and np.isfinite(atr_i) and np.isfinite(z_i)
        if stop is None and i <= paused_until:
            pause_reasons["熔断/缺口暂停中"] = pause_reasons.get("熔断/缺口暂停中", 0) + 1
        if can_enter:
            # 定仓
            trailing = float(np.nanmean(liq[max(0, i - look):i + 1])) if liquidity_known and i > 0 else np.inf
            size = min(capital_usd / max_concurrent_cap, capital_usd - dep)
            if np.isfinite(trailing):
                size = min(size, trailing * batch)
            if cap.tx_max_buy_usd:
                size = min(size, cap.tx_max_buy_usd * batch)
            if np.isfinite(rv_i) and rv_i > vol_target:
                size *= vol_target / rv_i
            # 成本门槛：预期波动（ATR×tp倍数）/ 往返成本
            exp_move = params.tp_atr_mult * atr_i / close[i]
            rt = cost.round_trip_fraction(size) + 2 * cost.p.gas_usd / max(size, 1.0)
            edge = exp_move / rt if rt > 0 else 0.0
            direction, mode = None, None
            if np.isfinite(trailing) and trailing * batch < low_liq_min:
                pause_reasons["低流动性行情暂停"] = pause_reasons.get("低流动性行情暂停", 0) + 1
            elif size < max(cost.p.min_tx_usd, 0.002 * capital_usd):
                pause_reasons["流动性/容量不足"] = pause_reasons.get("流动性/容量不足", 0) + 1
            elif edge < params.edge_min_ratio:
                pause_reasons["成本门槛高于预期波动"] = pause_reasons.get("成本门槛高于预期波动", 0) + 1
            elif np.isfinite(tr_i) and tr_i > params.trend_thresh and "long" in cap.directions:
                direction, mode = "long", "trend_long"            # 上升趋势：顺势做多（受成本门槛、止损、跟踪止盈约束）
            elif np.isfinite(tr_i) and tr_i < -params.trend_thresh and "short" in cap.directions:
                direction, mode = "short", "trend_short"          # 下跌趋势：顺势做空（仅限可做空品种）
            elif abs(tr_i) <= params.trend_thresh and z_i <= -params.z_entry and "long" in cap.directions:
                direction, mode = "long", "range_long"            # 区间：低吸
            elif abs(tr_i) <= params.trend_thresh and z_i >= params.z_entry and "short" in cap.directions:
                direction, mode = "short", "range_short"          # 区间：高抛（仅限可做空品种）
            elif np.isfinite(tr_i) and tr_i < -params.trend_thresh and "short" not in cap.directions:
                pause_reasons["下跌趋势但产品不支持做空"] = pause_reasons.get("下跌趋势但产品不支持做空", 0) + 1
            else:
                pause_reasons["区间内等待极值"] = pause_reasons.get("区间内等待极值", 0) + 1
            if direction:
                pending_entries.append((direction, mode, size, i + latency))
        i += 1
    # 收盘仍未平的仓位：按最后收盘估值（标记不可验证）
    unliq = 0.0
    for pos in positions:
        px = close[-1]
        est = px * (1 - side_cost) if pos.direction == "long" else px * (1 + side_cost)
        net = (pos.fill.qty * est - pos.fill.filled_usd) if pos.direction == "long" else (pos.fill.filled_usd - pos.fill.qty * est)
        net -= cost.p.gas_usd
        cum += net
        unliq += pos.fill.qty * px
        from ..target_model.execution import ChildFill
        mark = BatchFill(side="sell" if pos.direction == "long" else "buy", requested_usd=pos.fill.qty * px)
        mark.fills.append(ChildFill(n - 1, str(bars["ts_ny"].iloc[-1]), float(px), float(est), float(pos.fill.qty * est), float(pos.fill.qty), float(cost.p.gas_usd)))
        trades.append(EngineTrade(len(trades) + 1, pos.direction, pos.mode, pos.fill, mark, pos.fill.filled_usd, pos.fill.qty, pos.fill.avg_exec, est,
                                  pos.fill.cost_usd + cost.p.gas_usd, 0.0, net, net / pos.fill.filled_usd, n - 1 - pos.entry_idx,
                                  "日终未能平仓：按收盘估值（不可验证）", cum, True))
    curve[-1] = cum if n else 0.0
    met = cum >= target_usd - 1e-9
    wins = [t for t in trades if t.net_pnl_usd > 0]
    dd = float((np.maximum.accumulate(curve) - curve).max()) if n else 0.0
    return DayRun(symbol, day, capital_usd, target_usd, params, cap, trades, cum, cum / capital_usd if capital_usd else 0.0, met, hit_ts,
                  len(trades), sum(t.direction == "long" for t in trades), sum(t.direction == "short" for t in trades),
                  (len(wins) / len(trades)) if trades else None, dd, peak_dep, float(deployed.mean()) if n else 0.0,
                  float(sum(t.trading_cost_usd for t in trades)), float(sum(t.funding_cost_usd for t in trades)), mode_counts, pause_reasons,
                  stop, partial, (not liquidity_known) or unliq > 0 or cost.is_unverifiable(peak_dep), curve.tolist(),
                  bool((bars["quality"].astype(int) & 128).any()) if n else False)
