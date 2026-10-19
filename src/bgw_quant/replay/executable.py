"""模式 B：当时可执行复盘。

规则：
  * 第 i 根K线收盘后，策略只能看到 df[:i+1]（全部为已完成K线），输出目标仓位。
  * 仓位变化在第 i+1 根K线的开盘价成交（经成本模型得到实际成交价）。
  * 每个 America/New_York 交易日收盘必须空仓：若日终仍持仓，以当日最后一根已完成K线的收盘价平仓（视为收盘集合竞价，记为假设）。
  * 绝不读取未来K线；引擎本身只按时间顺序把前缀喂给策略，并在测试中做前缀一致性检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel


@dataclass
class ReplayResult:
    symbol: str
    strategy: str
    capital_usd: float
    final_cash_usd: float
    net_return: float
    n_trades: int
    total_cost_usd: float
    trades: list[dict] = field(default_factory=list)
    equity: pd.Series | None = None      # 每根K线按收盘价计的权益
    signals: pd.Series | None = None     # 目标仓位
    unverifiable: bool = False
    failed_fills: int = 0
    partial_fills: int = 0
    note: str = ""

    def to_dict(self, with_series: bool = False) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("equity", "signals")}
        if with_series and self.equity is not None:
            d["equity"] = [float(x) for x in self.equity.to_numpy()]
            d["signals"] = [int(x) for x in self.signals.to_numpy()]
        return d


class ExecutableReplay:
    def __init__(self, cost: CostModel, capital_usd: float, position_fraction: float = 1.0):
        self.cost = cost
        self.capital_usd = float(capital_usd)
        self.position_fraction = position_fraction

    def run(self, df: pd.DataFrame, strategy, signals: pd.Series | None = None) -> ReplayResult:
        df = df.reset_index(drop=True)
        n = len(df)
        if n == 0:
            return ReplayResult(symbol="", strategy=getattr(strategy, "name", "?"), capital_usd=self.capital_usd,
                                final_cash_usd=self.capital_usd, net_return=0.0, n_trades=0, total_cost_usd=0.0)
        sig = (signals if signals is not None else strategy.compute(df)).fillna(0).astype(int).to_numpy()
        day = pd.to_datetime(df["ts_ny"]).dt.date.to_numpy()
        open_, close = df["open"].to_numpy(float), df["close"].to_numpy(float)
        ts = df["ts"].to_numpy()
        cash = self.capital_usd
        qty = 0.0
        pos = 0
        trades: list[dict] = []
        equity = np.empty(n)
        total_cost = 0.0
        failed = partial = 0
        unver = False
        open_trade: dict | None = None

        def do_buy(i: int, ref: float, signal_i: int):
            nonlocal cash, qty, pos, total_cost, failed, partial, unver, open_trade
            notional = cash * self.position_fraction
            f = self.cost.execute("buy", ref, notional)
            if not f.filled:
                failed += 1
                cash -= f.gas_usd
                total_cost += f.gas_usd
                return
            if f.fill_ratio < 1:
                partial += 1
            unver = unver or f.unverifiable
            qty = f.notional_usd / f.exec_price           # 以实际成交价换得的股数
            cash -= f.notional_usd + f.gas_usd
            total_cost += f.total_cost_usd
            pos = 1
            open_trade = {"signal_idx": int(signal_i), "signal_ts": pd.Timestamp(ts[signal_i]).isoformat(),
                          "buy_idx": int(i), "buy_ts": pd.Timestamp(ts[i]).isoformat(), "buy_ref": ref,
                          "buy_exec": f.exec_price, "qty": qty, "buy_cost_usd": f.total_cost_usd,
                          "fill_ratio": f.fill_ratio, "unverifiable": f.unverifiable, "note": f.note}

        def do_sell(i: int, ref: float, reason: str):
            nonlocal cash, qty, pos, total_cost, failed, unver, open_trade
            if pos == 0:
                return
            f = self.cost.execute("sell", ref, qty * ref)
            if not f.filled:
                failed += 1
                cash -= f.gas_usd
                total_cost += f.gas_usd
                return   # 仍持仓，等下一次机会
            proceeds = qty * f.exec_price - f.gas_usd
            cash += proceeds
            total_cost += f.total_cost_usd
            t = dict(open_trade or {})
            entry_cash = t["qty"] * t["buy_exec"] + t.get("buy_cost_usd", 0) - (t["qty"] * t["buy_exec"] * 0)  # 买入投入（含费）
            t.update({"sell_idx": int(i), "sell_ts": pd.Timestamp(ts[i]).isoformat(), "sell_ref": ref,
                      "sell_exec": f.exec_price, "sell_cost_usd": f.total_cost_usd, "reason": reason,
                      "cost_usd": t.get("buy_cost_usd", 0) + f.total_cost_usd,
                      "gross_pnl_usd": qty * (ref - t["buy_ref"]),
                      "net_pnl_usd": proceeds - (t["qty"] * t["buy_exec"] + self.cost.p.gas_usd * self.cost.n_child_orders(t["qty"] * t["buy_exec"])),
                      "net_ret": f.exec_price / t["buy_exec"] - 1 - (2 * self.cost.p.gas_usd / max(t["qty"] * t["buy_exec"], 1e-9)),
                      "hold_bars": int(i - t["buy_idx"])})
            trades.append(t)
            qty, pos, open_trade = 0.0, 0, None

        for i in range(n):
            # 1) 新交易日第一根：若上日残留持仓（理论不应发生），按开盘价平仓
            if i > 0 and day[i] != day[i - 1] and pos == 1:
                do_sell(i, open_[i], "跨日强制平仓")
            # 2) 执行上一根收盘产生的信号（在本根开盘）
            if i > 0 and day[i] == day[i - 1]:
                target = sig[i - 1]
                if target == 1 and pos == 0:
                    do_buy(i, open_[i], i - 1)
                elif target == 0 and pos == 1:
                    do_sell(i, open_[i], "信号平仓")
            # 3) 日终：当日最后一根已完成K线收盘强制空仓
            last_of_day = (i == n - 1) or (day[i + 1] != day[i])
            if last_of_day and pos == 1:
                do_sell(i, close[i], "日终强制平仓(收盘价假设)")
            equity[i] = cash + qty * close[i]
        return ReplayResult(symbol=str(df["symbol"].iloc[0]), strategy=getattr(strategy, "name", "?"),
                            capital_usd=self.capital_usd, final_cash_usd=float(cash),
                            net_return=float(cash / self.capital_usd - 1), n_trades=len(trades),
                            total_cost_usd=float(total_cost), trades=trades,
                            equity=pd.Series(equity, index=df.index), signals=pd.Series(sig, index=df.index),
                            unverifiable=unver, failed_fills=failed, partial_fills=partial,
                            note="信号在下一根K线开盘成交；日终按最后一根已完成K线收盘价平仓（假设）")


def prefix_consistency_check(strategy, df: pd.DataFrame, cuts: list[int]) -> list[int]:
    """返回不满足前缀一致性（存在未来函数）的截断点列表。"""
    full = strategy.compute(df).fillna(0).astype(int).to_numpy()
    bad = []
    for k in cuts:
        part = strategy.compute(df.iloc[:k].reset_index(drop=True)).fillna(0).astype(int).to_numpy()
        if not np.array_equal(part, full[:k]):
            bad.append(k)
    return bad
