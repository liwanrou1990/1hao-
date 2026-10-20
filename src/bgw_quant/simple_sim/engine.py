"""简化版多空教学模拟（已知昨日完整走势的事后分析，非实盘预测）。

目标唯一：累计净利润（扣手续费/价差/滑点/冲击/Gas/资金成本/借券费）达到本金档位对应的目标金额即停止。
规则：
  * 按时间顺序扫描；空仓时从当前K线出发，分别评估“做多”和“做空（仅 shortable 品种）”的最早可行出场：
    持有期内不利波动 ≤ 止损 0.5%，且扣除全部成本后净收益 ≥ 最低单笔净收益；取最早出场（或最高净收益）的方向。
  * 单笔投入 ≤ 本金 × position_fraction，并受逐根流动性参与率限制（分批成交）；不加杠杆、不加仓；同一时刻只有一个仓位。
  * 当日累计亏损达到上限即停止；收盘前 N 根不再开仓；交易次数不限、不强制凑数。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import load_yaml
from ..costs.model import CostModel
from ..target_model.execution import BatchFill, bar_liquidity, batch_fill

DISCLAIMER = "已知昨日完整走势的事后教学分析：所有买卖点由程序在看过全天K线后反向选出，不是实盘预测，不代表未来收益。"
BARS_PER_YEAR = 252 * 390


@dataclass
class SimpleSimConfig:
    position_fraction: float = 1.0
    stop_loss_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    min_trade_net_pct: float = 0.10
    max_hold_bars: int = 120
    cooldown_bars: int = 1
    batch_bars: int = 5
    participation_rate: float = 0.10
    flat_before_close_bars: int = 5
    capital_cost_annual: float = 0.05
    short_borrow_rate_annual: float = 0.05
    direction_choice: str = "earliest_exit"
    target_mode: str = "lower"


def load_simple_config() -> SimpleSimConfig:
    raw = load_yaml("simple_sim.yaml")
    return SimpleSimConfig(**{k: v for k, v in raw.items() if k in SimpleSimConfig.__dataclass_fields__})


def shortable(symbol: str) -> tuple[bool, dict]:
    cfg = load_yaml("symbols.yaml").get("short_support", {}) or {}
    v = (cfg.get("verified") or {}).get(symbol)
    if v and v.get("shortable"):
        return True, v
    return bool(cfg.get("default_shortable", False)), {}


@dataclass
class SimTrade:
    seq: int
    direction: str            # long | short
    open_fill: BatchFill
    close_fill: BatchFill
    size_usd: float
    qty: float
    avg_open: float
    avg_close: float
    trading_cost_usd: float   # 手续费/价差/滑点/冲击/Gas
    capital_cost_usd: float   # 资金成本 + 借券费
    net_pnl_usd: float
    net_ret: float
    mae_pct: float
    hold_bars: int
    cum_net_pnl_usd: float

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("open_fill", "close_fill")}
        d["open_fill"], d["close_fill"] = self.open_fill.to_dict(), self.close_fill.to_dict()
        d["open_ts"], d["close_ts"] = self.open_fill.fills[0].ts_ny, self.close_fill.fills[-1].ts_ny
        d["open_idx"], d["close_idx"] = self.open_fill.start_idx, self.close_fill.end_idx
        return d


@dataclass
class SimpleSimResult:
    symbol: str
    day: str
    source: str
    venue: str
    capital_usd: float
    target_usd: float
    target_return: float
    tier: str | None
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    target_hit_ts: str | None
    n_buys: int
    n_sells: int
    n_long: int
    n_short: int
    shortable: bool
    capital_utilization: float          # 单笔最大实际投入 / 本金
    avg_utilization: float              # 平均投入 / 本金
    trading_cost_usd: float
    capital_cost_usd: float
    max_drawdown_usd: float
    stop_reason: str | None
    trades: list[SimTrade]
    cum_curve: list[float]
    unverifiable: bool
    data_is_simulated: bool
    notes: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "trades"}
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


def _gas(fill: BatchFill, cost: CostModel) -> float:
    return sum(cost.p.gas_usd * max(1, int(np.ceil(f.notional_usd / cost.p.max_tx_usd))) for f in fill.fills)


def _pnl(direction: str, open_fill: BatchFill, close_fill: BatchFill, cost: CostModel, cfg: SimpleSimConfig,
         hold_bars: int, borrow_annual: float) -> tuple[float, float, float]:
    """返回 (净盈亏, 交易成本, 资金成本)。多头：买入花 open.filled，卖出得 close.filled；空头：卖出得 open.filled，买回花 close.filled。"""
    gas = _gas(open_fill, cost) + _gas(close_fill, cost)
    if direction == "long":
        gross_after_price_costs = close_fill.filled_usd - open_fill.filled_usd
    else:
        gross_after_price_costs = open_fill.filled_usd - close_fill.filled_usd
    cap_cost = open_fill.filled_usd * cfg.capital_cost_annual * hold_bars / BARS_PER_YEAR
    if direction == "short":
        cap_cost += open_fill.filled_usd * borrow_annual * hold_bars / BARS_PER_YEAR
    trading_cost = open_fill.cost_usd + close_fill.cost_usd      # 含价格内成本与 Gas（展示用）
    net = gross_after_price_costs - gas - cap_cost
    return net, trading_cost, cap_cost


def _search_exit(bars, i, direction, cost, cfg, liq, close, low, high, size_usd, borrow_annual, need_usd: float | None = None):
    """从第 i 根开仓，在止损未触发的持有窗口内枚举满足“净收益≥最低单笔净收益”的平仓点。
    返回 (open_fill, close_fill, s, net, tc, cc, mae)：若某个平仓点的净收益已能补足剩余目标（need_usd），取最早的那个；否则取净收益最大的。"""
    n = len(close)
    open_side = "buy" if direction == "long" else "sell"
    open_fill = batch_fill(bars, i, open_side, size_usd, cost, liq, cfg.batch_bars, "close")
    if not open_fill.fills or open_fill.filled_usd < max(cost.p.min_tx_usd, 0.01 * size_usd):
        return None
    avg = open_fill.avg_exec
    qty = open_fill.qty
    e_end = open_fill.end_idx
    mae = 0.0
    side_cost = cost.proportional_side_cost() + cost.impact_fraction(open_fill.filled_usd)
    feasible = []
    for s in range(e_end + 1, min(n - 1, e_end + cfg.max_hold_bars) + 1):
        adverse = (low[s] / avg - 1) if direction == "long" else (1 - high[s] / avg)
        mae = min(mae, adverse)
        if mae < -cfg.stop_loss_pct / 100:
            return None
        # 粗估
        if direction == "long":
            est = qty * close[s] * (1 - side_cost) - open_fill.filled_usd
        else:
            est = open_fill.filled_usd - qty * close[s] * (1 + side_cost)
        if est < cfg.min_trade_net_pct / 100 * open_fill.filled_usd:
            continue
        close_side = "sell" if direction == "long" else "buy"
        # 做空买回：按含成本的金额申请，保证买回数量 ≥ qty，再按比例缩减到恰好 qty
        req_usd = qty * close[s] if direction == "long" else qty * close[s] * (1 + 2 * side_cost + 0.002)
        close_fill = batch_fill(bars, s, close_side, req_usd, cost, liq, cfg.batch_bars, "close",
                                qty_to_sell=qty if direction == "long" else None)
        if direction == "short":
            # 买回固定数量：按金额分批后校正数量
            if not close_fill.fills:
                continue
            bought_qty = sum(f.qty for f in close_fill.fills)
            if bought_qty < qty * 0.999:
                continue
            if bought_qty > qty:   # 按比例缩减到恰好 qty
                r = qty / bought_qty
                close_fill = close_fill.scaled(r)
        elif not close_fill.fills or close_fill.qty < qty * 0.999:
            continue
        hold = close_fill.end_idx - open_fill.start_idx
        net, tc, cc = _pnl(direction, open_fill, close_fill, cost, cfg, hold, borrow_annual)
        if net >= cfg.min_trade_net_pct / 100 * open_fill.filled_usd:
            cand = (open_fill, close_fill, s, net, tc, cc, mae * 100)
            if need_usd is not None and net >= need_usd - 1e-9:
                return cand                       # 最早能补足剩余目标的平仓点
            feasible.append(cand)
    if not feasible:
        return None
    return max(feasible, key=lambda c: c[3])      # 否则取窗口内净收益最大的平仓点


def simulate_day(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_usd: float, cfg: SimpleSimConfig, *,
                 symbol: str | None = None, tier: str | None = None, allow_short: bool | None = None,
                 short_borrow_annual: float | None = None) -> SimpleSimResult:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]) if n else ""
    can_short, short_info = (allow_short, {}) if allow_short is not None else shortable(symbol)
    borrow = short_borrow_annual if short_borrow_annual is not None else float(short_info.get("short_borrow_rate_annual", cfg.short_borrow_rate_annual))
    close, low, high = bars["close"].to_numpy(float), bars["low"].to_numpy(float), bars["high"].to_numpy(float)
    liq = bar_liquidity(bars, cfg.participation_rate)
    liquidity_known = bool(np.isfinite(liq).all())
    size_plan = capital_usd * min(cfg.position_fraction, 1.0)
    trades: list[SimTrade] = []
    cum, curve = 0.0, np.zeros(n)
    i, last_exit = 0, -10**9
    max_size, sizes = 0.0, []
    hit_ts, stop = None, None
    last_entry = n - 1 - cfg.flat_before_close_bars
    while i <= last_entry:
        curve[i] = cum
        if cum >= target_usd - 1e-9:
            stop = "累计净利润已达目标，停止模拟"
            break
        if cum <= -cfg.max_daily_loss_pct / 100 * capital_usd:
            stop = "触及当日亏损上限，停止模拟"
            break
        if i - last_exit <= cfg.cooldown_bars:
            i += 1
            continue
        need = target_usd - cum
        cands = []
        r = _search_exit(bars, i, "long", cost, cfg, liq, close, low, high, size_plan, borrow, need)
        if r:
            cands.append(("long", r))
        if can_short:
            r = _search_exit(bars, i, "short", cost, cfg, liq, close, low, high, size_plan, borrow, need)
            if r:
                cands.append(("short", r))
        if not cands:
            i += 1
            continue
        if cfg.direction_choice == "best_net":
            direction, (of, cf, s, net, tc, cc, mae) = max(cands, key=lambda c: c[1][3])
        else:   # earliest_exit：能补足目标者优先最早，否则最早平仓
            direction, (of, cf, s, net, tc, cc, mae) = min(cands, key=lambda c: (0 if c[1][3] >= need - 1e-9 else 1, c[1][2]))
        cum += net
        max_size = max(max_size, of.filled_usd)
        sizes.append(of.filled_usd)
        trades.append(SimTrade(len(trades) + 1, direction, of, cf, of.filled_usd, of.qty, of.avg_exec, cf.avg_exec, tc, cc, net,
                               net / of.filled_usd, mae, cf.end_idx - of.start_idx, cum))
        for k in range(i, cf.end_idx + 1):
            curve[k] = cum - net if k < cf.end_idx else cum
        if cum >= target_usd - 1e-9 and hit_ts is None:
            hit_ts = cf.fills[-1].ts_ny
        last_exit = cf.end_idx
        i = cf.end_idx + 1
    curve[i:] = cum
    met = cum >= target_usd - 1e-9
    notes = []
    if not can_short:
        notes.append("该品种未核实可做空渠道（链上现货代币），本模拟仅做多")
    if not liquidity_known:
        notes.append("数据源无成交额：流动性未知，未施加逐根上限，结果不可验证")
    if not met:
        notes.append("未达标：" + ("全日没有满足“止损 0.5% 内、扣费后净收益 ≥ 最低要求”的机会" if not trades else
                                   f"可行机会累计净利润 {cum:,.2f} USD 低于目标 {target_usd:,.2f} USD"))
        if liquidity_known and max_size < size_plan * 0.98 and trades:
            notes.append(f"流动性限制：单笔最大实际投入 {max_size:,.0f} USD，低于计划 {size_plan:,.0f} USD")
    if stop:
        notes.append(stop)
    dd = float((np.maximum.accumulate(curve) - curve).max()) if n else 0.0
    n_long = sum(t.direction == "long" for t in trades)
    n_short = len(trades) - n_long
    return SimpleSimResult(symbol, day, str(bars["source"].iloc[0]) if n else "", str(bars["venue"].iloc[0]) if n else "", capital_usd,
                           target_usd, target_usd / capital_usd if capital_usd else 0.0, tier, cum, cum / capital_usd if capital_usd else 0.0,
                           met, hit_ts, len(trades), len(trades), n_long, n_short, can_short,
                           max_size / capital_usd if capital_usd else 0.0, (float(np.mean(sizes)) / capital_usd) if sizes else 0.0,
                           float(sum(t.trading_cost_usd for t in trades)), float(sum(t.capital_cost_usd for t in trades)), dd, stop, trades,
                           curve.tolist(), cost.is_unverifiable(max_size) or not liquidity_known,
                           bool((bars["quality"].astype(int) & 128).any()) if n else False, notes)
