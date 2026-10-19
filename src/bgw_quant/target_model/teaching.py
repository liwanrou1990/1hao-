"""教学路径（已知完整走势）：多次小额交易累计达到固定日净收益目标，稳定性优先。

规则（全部可配置）：
  * 按时间顺序扫描；空仓时在“局部低点”尝试入场，分批建仓（每根受流动性参与率限制，最多 batch_bars 根）。
  * 只接受“稳定”的交易：持有期内最大不利波动不超过 stop_loss_pct，且在 max_hold_bars 内净收益达到 take_profit_net_pct；
    达到即分批平仓，不追求单笔最大利润。
  * 累计已实现净利润 ≥ 当日目标后停止新增交易；交易次数由行情决定，不强制凑数。
  * 不加杠杆、不超额使用本金；同一时刻只有一个持仓。
输出带“已知完整走势的事后教学模拟”标记，仅供教学，不代表实盘可复制。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from .config import TargetModelConfig
from .execution import BatchFill, bar_liquidity, batch_fill

TEACHING_DISCLAIMER = "已知完整走势的事后教学模拟：利用昨日全部K线反向挑选稳定的小额交易，仅供学习，不代表实盘可复制或未来收益。"


@dataclass
class RoundTrip:
    seq: int
    symbol: str
    entry: BatchFill
    exit: BatchFill
    size_usd: float
    qty: float
    avg_entry: float
    avg_exit: float
    gross_pnl_usd: float
    cost_usd: float
    net_pnl_usd: float
    net_ret: float
    mae_pct: float            # 持有期内最大不利波动（相对平均入场价）
    mfe_pct: float            # 最大有利波动
    hold_bars: int
    cum_net_pnl_usd: float
    reason_entry: str
    reason_exit: str

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("entry", "exit")}
        d["entry"] = self.entry.to_dict()
        d["exit"] = self.exit.to_dict()
        d["buy_ts"], d["sell_ts"] = self.entry.fills[0].ts_ny, self.exit.fills[-1].ts_ny
        d["buy_idx"], d["sell_idx"] = self.entry.start_idx, self.exit.end_idx
        return d


@dataclass
class TargetPathResult:
    symbol: str
    day: str
    venue: str
    source: str
    capital_usd: float
    target_return: float
    target_usd: float
    cost_profile: str
    trades: list[RoundTrip]
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    target_hit_ts: str | None
    n_buys: int
    n_sells: int
    cum_curve: list[float]            # 每根K线的累计已实现净利润
    max_single_loss_usd: float
    max_drawdown_usd: float           # 累计已实现曲线的最大回撤
    capital_utilization: float        # 单笔最大投入 / 本金
    liquidity_known: bool
    partial_fills: int
    unverifiable: bool
    data_is_simulated: bool
    reasons_not_met: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "teaching_hindsight"
    disclaimer: str = TEACHING_DISCLAIMER

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "trades"}
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


def _local_low(close: np.ndarray, i: int, n: int) -> bool:
    if n <= 0:
        return True
    lo = max(0, i - n)
    return close[i] <= close[lo:i + 1].min() + 1e-12


def teaching_path(bars: pd.DataFrame, cost: CostModel, capital_usd: float, target_return: float, cfg: TargetModelConfig,
                  *, symbol: str | None = None, cost_profile: str = "") -> TargetPathResult:
    bars = bars.reset_index(drop=True)
    n = len(bars)
    symbol = symbol or str(bars["symbol"].iloc[0])
    day = str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0])
    close = bars["close"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    liq = bar_liquidity(bars, cfg.participation_rate)
    liquidity_known = bool(np.isfinite(liq).all())
    target_usd = target_return * capital_usd
    tp, sl = cfg.take_profit_net_pct / 100 + cfg.teaching.get("min_edge_over_cost", 0.0) / 100, cfg.stop_loss_pct / 100
    local_n = int(cfg.teaching.get("require_local_low_bars", 0))
    side_cost = cost.proportional_side_cost()
    trades: list[RoundTrip] = []
    cum = 0.0
    cum_curve = np.zeros(n)
    i = 0
    last_exit = -10**9
    max_size = 0.0
    max_attempted = 0.0
    partial = 0
    hit_ts = None
    stop_reason = None
    last_entry_bar = n - 1 - cfg.flat_before_close_bars
    while i <= last_entry_bar:
        cum_curve[i] = cum
        if cum >= target_usd - 1e-9:
            stop_reason = "目标已达成，停止新增交易"
            break
        if cum <= -cfg.max_daily_loss_pct / 100 * capital_usd:
            stop_reason = "触及当日最大亏损限制"
            break
        if len(trades) >= cfg.max_trades_per_day:
            stop_reason = "达到当日最大交易次数"
            break
        if (i - last_exit) <= cfg.cooldown_bars or not _local_low(close, i, local_n):
            i += 1
            continue
        notional = min(capital_usd * cfg.position_fraction, capital_usd)
        entry = batch_fill(bars, i, "buy", notional, cost, liq, cfg.batch_bars, "close")
        if not entry.fills or entry.filled_usd < max(cost.p.min_tx_usd, 0.01 * notional):
            i += 1
            continue
        max_attempted = max(max_attempted, entry.filled_usd)
        e_end = entry.end_idx
        avg_e = entry.avg_exec
        qty = entry.qty
        gas_exit_est = cost.p.gas_usd * max(1, int(np.ceil(entry.filled_usd / cost.p.max_tx_usd)))
        # 寻找最早的“稳定”出场：持有期内不利波动 ≤ sl，且净收益 ≥ tp
        exit_fill = None
        mae = 0.0
        mfe = 0.0
        found_s = None
        for s in range(e_end + 1, min(n - 1, e_end + cfg.max_hold_bars) + 1):
            mae = min(mae, low[s] / avg_e - 1)
            mfe = max(mfe, high[s] / avg_e - 1)
            if mae < -sl:
                break
            # 粗估（按当根收盘一次性卖出）达标后，再做分批卖出的精确核算
            est_net = qty * close[s] * (1 - side_cost - cost.impact_fraction(entry.filled_usd)) - gas_exit_est - entry.filled_usd
            if est_net >= tp * entry.filled_usd:
                cand = batch_fill(bars, s, "sell", qty * close[s], cost, liq, cfg.batch_bars, "close", qty_to_sell=qty)
                if cand.fills and cand.qty >= qty * 0.999:
                    net_true = cand.filled_usd - cand_gas(cand, cost) - entry.filled_usd - entry_gas(entry, cost)
                    if net_true >= tp * entry.filled_usd:
                        exit_fill, found_s = cand, s
                        break
        if exit_fill is None:
            i += 1
            continue
        net_pnl = exit_fill.filled_usd - cand_gas(exit_fill, cost) - entry.filled_usd - entry_gas(entry, cost)
        cost_usd = entry.cost_usd + exit_fill.cost_usd
        gross = qty * (exit_fill.fills[-1].ref_price - entry.fills[0].ref_price)
        cum += net_pnl
        max_size = max(max_size, entry.filled_usd)
        partial += int(entry.partial) + int(exit_fill.partial)
        trades.append(RoundTrip(len(trades) + 1, symbol, entry, exit_fill, entry.filled_usd, qty, avg_e, exit_fill.avg_exec, gross,
                                cost_usd, net_pnl, net_pnl / entry.filled_usd, mae * 100, mfe * 100, exit_fill.end_idx - entry.start_idx, cum,
                                f"最近 {local_n} 根局部低点，分 {len(entry.fills)} 批买入", f"净收益达到 {tp:.2%} 目标，分 {len(exit_fill.fills)} 批卖出"))
        for k in range(i, exit_fill.end_idx + 1):
            cum_curve[k] = cum if k == exit_fill.end_idx else cum - net_pnl
        if cum >= target_usd - 1e-9 and hit_ts is None:
            hit_ts = exit_fill.fills[-1].ts_ny
        last_exit = exit_fill.end_idx
        i = exit_fill.end_idx + 1
    cum_curve[i:] = cum
    met = cum >= target_usd - 1e-9
    reasons = []
    if not met:
        if not trades:
            reasons.append("全日没有任何满足“不利波动≤止损且净收益≥目标”的稳定交易机会（成本过高或波动不足）")
        else:
            reasons.append(f"稳定交易累计净利润 {cum:,.2f} USD 低于目标 {target_usd:,.2f} USD")
        if liquidity_known and max_attempted < capital_usd * cfg.position_fraction * 0.98:
            reasons.append(f"流动性限制：分批建仓（≤{cfg.batch_bars} 根，参与率 {cfg.participation_rate:.0%}）单笔最多只能成交 "
                           f"{max_attempted:,.0f} USD，低于计划投入 {capital_usd * cfg.position_fraction:,.0f} USD")
        if stop_reason:
            reasons.append(stop_reason)
    notes = []
    if not liquidity_known:
        notes.append("数据源无成交额字段：流动性未知，未施加逐根上限，结果不可验证")
    if stop_reason:
        notes.append(stop_reason)
    losses = [t.net_pnl_usd for t in trades if t.net_pnl_usd < 0]
    curve = np.array(cum_curve)
    dd = float((np.maximum.accumulate(curve) - curve).max()) if n else 0.0
    return TargetPathResult(symbol, day, str(bars["venue"].iloc[0]), str(bars["source"].iloc[0]), capital_usd, target_return, target_usd,
                            cost_profile, trades, cum, cum / capital_usd, met, hit_ts, len(trades), len(trades), curve.tolist(),
                            float(min(losses)) if losses else 0.0, dd, max_size / capital_usd if capital_usd else 0.0,
                            liquidity_known, partial, cost.is_unverifiable(max_size) or not liquidity_known,
                            bool((bars["quality"].astype(int) & 128).any()), reasons, notes)


def entry_gas(entry: BatchFill, cost: CostModel) -> float:
    return sum(cost.p.gas_usd * max(1, int(np.ceil(f.notional_usd / cost.p.max_tx_usd))) for f in entry.fills)


def cand_gas(fill: BatchFill, cost: CostModel) -> float:
    return sum(cost.p.gas_usd * max(1, int(np.ceil(f.notional_usd / cost.p.max_tx_usd))) for f in fill.fills)
