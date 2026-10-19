"""实盘型目标寻求策略（不使用任何未来信息）。

每根已完成K线收盘后决策，下一根开始分批成交：
  * 入场：空仓、未停止、非收盘前 N 根；z 分数（close 相对最近 z_window 根均值）≤ −z_entry（低吸）。
  * 出场：按当前收盘估算的扣费净收益 ≥ take_profit_net_pct（止盈）；或 close ≤ 平均入场价×(1−stop_loss_pct)（止损）；或持有 ≥ max_hold_bars；或日终。
  * 日内风控：累计已实现净利润 ≥ 目标 → 停止新增交易；累计亏损 ≥ max_daily_loss_pct → 停止；交易次数上限；冷却期。
  * 仓位：单笔 ≤ 本金 × position_fraction；分批成交受逐根流动性参与率限制；不加杠杆。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from .config import TargetModelConfig
from .execution import BatchFill, bar_liquidity, batch_fill
from .teaching import RoundTrip, cand_gas, entry_gas

STRATEGY_DISCLAIMER = "无未来信息的实盘型策略回测：每根K线收盘后决策、下一根起成交；所有参数在训练窗内选择，绩效只报样本外。"


@dataclass
class StrategyDayResult:
    symbol: str
    day: str
    capital_usd: float
    target_return: float
    target_usd: float
    params: dict
    trades: list[RoundTrip]
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    stopped_reason: str | None
    cum_curve: list[float]
    max_drawdown_usd: float
    n_signals: int
    partial_fills: int
    liquidity_known: bool
    unverifiable: bool
    data_is_simulated: bool
    mode: str = "strategy_no_lookahead"
    disclaimer: str = STRATEGY_DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "trades"}
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


@dataclass
class TargetSeekingStrategy:
    z_entry: float = 1.5
    z_window: int = 30
    take_profit_net_pct: float = 0.5
    stop_loss_pct: float = 0.8
    position_fraction: float = 0.5
    name: str = "目标寻求低吸"

    @property
    def params(self) -> dict:
        return {"z_entry": self.z_entry, "z_window": self.z_window, "take_profit_net_pct": self.take_profit_net_pct,
                "stop_loss_pct": self.stop_loss_pct, "position_fraction": self.position_fraction}


def run_strategy_day(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_return: float, cfg: TargetModelConfig,
                     strat: TargetSeekingStrategy, symbol: str | None = None) -> StrategyDayResult:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0])
    close = bars["close"].to_numpy(float)
    liq = bar_liquidity(bars, cfg.participation_rate)
    liquidity_known = bool(np.isfinite(liq).all())
    target_usd = target_return * capital_usd
    tp, sl = strat.take_profit_net_pct / 100, strat.stop_loss_pct / 100
    side_cost = cost.proportional_side_cost()
    # 因果 z 分数：只用截至当根的收盘
    cs = pd.Series(close)
    mean = cs.rolling(strat.z_window, min_periods=strat.z_window).mean().to_numpy()
    sd = cs.rolling(strat.z_window, min_periods=strat.z_window).std(ddof=0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (close - mean) / sd, np.nan)
    trades: list[RoundTrip] = []
    cum = 0.0
    cum_curve = np.zeros(n)
    pos_entry: BatchFill | None = None
    entry_signal_idx = -1
    last_exit = -10**9
    stopped = None
    n_signals = 0
    partial = 0
    max_size = 0.0
    pending: str | None = None      # 下一根要执行的动作：buy / sell:<reason>
    i = 0
    last_entry_bar = n - 1 - cfg.flat_before_close_bars
    while i < n:
        # 1) 执行上一根收盘决定的动作（从本根开始分批）
        if pending == "buy" and pos_entry is None:
            notional = capital_usd * strat.position_fraction
            fill = batch_fill(bars, i, "buy", notional, cost, liq, cfg.batch_bars, "open")
            if fill.fills and fill.filled_usd >= max(cost.p.min_tx_usd, 0.01 * notional):
                pos_entry = fill
                entry_signal_idx = i - 1
                max_size = max(max_size, fill.filled_usd)
                partial += int(fill.partial)
                i = fill.end_idx       # 分批期间不做新决策
            pending = None
        elif pending and pending.startswith("sell") and pos_entry is not None:
            reason = pending.split(":", 1)[1]
            fill = batch_fill(bars, i, "sell", pos_entry.qty * close[i], cost, liq, cfg.batch_bars, "open", qty_to_sell=pos_entry.qty)
            if fill.fills and fill.qty > 0:
                sold_ratio = min(1.0, fill.qty / pos_entry.qty)
                sold_part = pos_entry.scaled(sold_ratio)
                net = fill.filled_usd - cand_gas(fill, cost) - sold_part.filled_usd - entry_gas(sold_part, cost)
                cum += net
                is_partial = sold_ratio < 0.999
                partial += int(is_partial)
                lows = bars["low"].to_numpy(float)[pos_entry.end_idx + 1: fill.start_idx + 1]
                highs = bars["high"].to_numpy(float)[pos_entry.end_idx + 1: fill.start_idx + 1]
                mae = float((lows / pos_entry.avg_exec - 1).min() * 100) if len(lows) else 0.0
                mfe = float((highs / pos_entry.avg_exec - 1).max() * 100) if len(highs) else 0.0
                trades.append(RoundTrip(len(trades) + 1, symbol, sold_part, fill, sold_part.filled_usd, sold_part.qty, pos_entry.avg_exec,
                                        fill.avg_exec, sold_part.qty * (fill.fills[-1].ref_price - pos_entry.fills[0].ref_price),
                                        sold_part.cost_usd + fill.cost_usd, net, net / sold_part.filled_usd, mae, mfe,
                                        fill.end_idx - pos_entry.start_idx, cum,
                                        f"z={z[entry_signal_idx]:.2f} ≤ -{strat.z_entry}（低吸信号于 {bars['ts_ny'].iloc[entry_signal_idx][11:16]}）",
                                        reason + ("（流动性不足，部分平仓，剩余继续持有）" if is_partial else "")))
                i = fill.end_idx
                if is_partial:
                    pos_entry = pos_entry.scaled(1 - sold_ratio)     # 剩余仓位继续持有，下根再次评估出场
                else:
                    last_exit = fill.end_idx
                    pos_entry = None
            pending = None
        cum_curve[i] = cum
        # 2) 本根收盘决策（只用 ≤ i 的信息）
        if pos_entry is not None:
            unreal_net = pos_entry.qty * close[i] * (1 - side_cost - cost.impact_fraction(pos_entry.filled_usd)) - pos_entry.filled_usd \
                - entry_gas(pos_entry, cost) - cost.p.gas_usd
            held = i - pos_entry.start_idx
            if i >= n - 1 - max(1, cfg.batch_bars):
                pending = "sell:日终平仓"
            elif unreal_net >= tp * pos_entry.filled_usd:
                pending = "sell:止盈（净收益达标）"
            elif close[i] <= pos_entry.avg_exec * (1 - sl):
                pending = "sell:止损"
            elif held >= cfg.max_hold_bars:
                pending = "sell:超过最长持有时间"
        else:
            if stopped is None:
                if cum >= target_usd - 1e-9:
                    stopped = "目标已达成，停止新增交易"
                elif cum <= -cfg.max_daily_loss_pct / 100 * capital_usd:
                    stopped = "触及当日最大亏损限制，停止交易"
                elif len(trades) >= cfg.max_trades_per_day:
                    stopped = "达到当日最大交易次数"
            if stopped is None and i <= last_entry_bar and (i - last_exit) > cfg.cooldown_bars and np.isfinite(z[i]) and z[i] <= -strat.z_entry:
                n_signals += 1
                pending = "buy"
        i += 1
    # 若循环结束仍持仓（极端情况），按最后一根收盘强制平仓
    if pos_entry is not None:
        fill = batch_fill(bars, n - 1, "sell", pos_entry.qty * close[-1], cost, liq, 1, "close", qty_to_sell=pos_entry.qty)
        net = (fill.filled_usd - cand_gas(fill, cost) if fill.fills else 0.0) - pos_entry.filled_usd - entry_gas(pos_entry, cost)
        cum += net
        trades.append(RoundTrip(len(trades) + 1, symbol, pos_entry, fill if fill.fills else pos_entry, pos_entry.filled_usd, pos_entry.qty,
                                pos_entry.avg_exec, fill.avg_exec if fill.fills else 0.0, 0.0, pos_entry.cost_usd + fill.cost_usd, net,
                                net / pos_entry.filled_usd, 0.0, 0.0, n - 1 - pos_entry.start_idx, cum, "", "日终强制平仓"))
        cum_curve[-1] = cum
    curve = np.array(cum_curve)
    dd = float((np.maximum.accumulate(curve) - curve).max()) if n else 0.0
    return StrategyDayResult(symbol, day, capital_usd, target_return, target_usd, strat.params, trades, cum, cum / capital_usd,
                             cum >= target_usd - 1e-9, stopped, curve.tolist(), dd, n_signals, partial, liquidity_known,
                             cost.is_unverifiable(max_size) or not liquidity_known, bool((bars["quality"].astype(int) & 128).any()))
