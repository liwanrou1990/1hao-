"""逐日回测引擎：每个交易日以固定本金独立运行（日内策略、日终空仓），产出每日净收益序列。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..costs.model import CostModel
from ..replay.executable import ExecutableReplay
from ..replay.hindsight import optimal_trades


@dataclass
class DailyRun:
    day: date
    net_return: float
    n_trades: int
    cost_usd: float
    hindsight_net: float
    buy_hold_net: float
    unverifiable: bool
    trades: list[dict] = field(default_factory=list)


def split_days(bars: pd.DataFrame) -> dict[date, pd.DataFrame]:
    d = pd.to_datetime(bars["ts_ny"]).dt.date
    return {k: g.reset_index(drop=True) for k, g in bars.groupby(d)}


def run_daily(bars: pd.DataFrame, strategy, cost: CostModel, capital_usd: float, with_hindsight: bool = True,
              params: dict | None = None) -> list[DailyRun]:
    """strategy 可以是策略对象或工厂（callable(**params)）。"""
    strat = strategy(**(params or {})) if isinstance(strategy, type) else strategy
    rep = ExecutableReplay(cost, capital_usd)
    out = []
    for d, g in split_days(bars).items():
        r = rep.run(g, strat)
        h = optimal_trades(g, cost, capital_usd).net_return if with_hindsight else float("nan")
        # 买入持有：开盘买、收盘卖，各付一次成本
        bh_buy = cost.execute("buy", float(g["open"].iloc[0]), capital_usd)
        bh_sell_px = float(g["close"].iloc[-1]) * (1 - cost.proportional_side_cost() - cost.impact_fraction(capital_usd))
        bh = (bh_buy.notional_usd / bh_buy.exec_price * bh_sell_px - cost.p.gas_usd * cost.n_child_orders(capital_usd)) / capital_usd - 1 - bh_buy.gas_usd / capital_usd
        out.append(DailyRun(d, r.net_return, r.n_trades, r.total_cost_usd, h, bh, r.unverifiable, r.trades))
    return out


def daily_frame(runs: list[DailyRun]) -> pd.DataFrame:
    df = pd.DataFrame([{"day": r.day, "net_return": r.net_return, "n_trades": r.n_trades, "cost_usd": r.cost_usd,
                        "hindsight_net": r.hindsight_net, "buy_hold_net": r.buy_hold_net, "unverifiable": r.unverifiable}
                       for r in runs])
    if not df.empty:
        df = df.set_index("day").sort_index()
    return df
