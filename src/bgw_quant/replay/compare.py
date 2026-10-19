"""A/B 双模式对照：同一标的、同一日、同一成本模型下，事后最优（A）与当时可执行（B）的差距分解。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..costs.model import CostModel
from .executable import ExecutableReplay, ReplayResult
from .hindsight import HindsightResult, optimal_trades


@dataclass
class ABComparison:
    symbol: str
    day: str
    capital_usd: float
    cost_profile: str
    hindsight: HindsightResult
    executable: dict[str, ReplayResult]      # 策略名 -> 结果
    best_strategy: str | None
    gap_explanation: list[str] = field(default_factory=list)
    data_is_simulated: bool = False

    def summary_table(self) -> pd.DataFrame:
        rows = [{"模式": "A 事后最优(理论上限)", "策略": "DP", "净收益": self.hindsight.net_return,
                 "交易次数": self.hindsight.n_trades, "成本(USD)": self.hindsight.total_cost_usd, "与A差距": 0.0}]
        for k, r in self.executable.items():
            rows.append({"模式": "B 当时可执行", "策略": k, "净收益": r.net_return, "交易次数": r.n_trades,
                         "成本(USD)": r.total_cost_usd, "与A差距": self.hindsight.net_return - r.net_return})
        return pd.DataFrame(rows)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "day": self.day, "capital_usd": self.capital_usd, "cost_profile": self.cost_profile,
            "data_is_simulated": self.data_is_simulated,
            "hindsight": self.hindsight.to_dict(),
            "executable": {k: v.to_dict() for k, v in self.executable.items()},
            "best_strategy": self.best_strategy, "gap_explanation": self.gap_explanation,
        }


def explain_gap(h: HindsightResult, r: ReplayResult, bars: pd.DataFrame, cost: CostModel) -> list[str]:
    msgs = []
    gap = h.net_return - r.net_return
    msgs.append(f"A 模式净收益 {h.net_return:+.2%}（{h.n_trades} 笔）；B 模式 {r.net_return:+.2%}（{r.n_trades} 笔）；差距 {gap:+.2%}。")
    msgs.append("A 模式在成交前已看到当日全部K线，知道每个波段的起止点；B 模式只能在信号出现后的下一根K线开盘成交，天然滞后至少 1 分钟且无法预知波段长度。")
    rt = cost.round_trip_fraction(r.capital_usd)
    msgs.append(f"本成本模型下单笔往返比例成本约 {rt:.2%}（含手续费/价差/滑点/冲击，另有 Gas）；"
                f"B 模式 {r.n_trades} 笔交易合计成本 {r.total_cost_usd:,.2f} USD，占本金 {r.total_cost_usd / r.capital_usd:.2%}。")
    if r.n_trades > h.n_trades * 1.5 and r.n_trades > 2:
        msgs.append("B 模式交易次数明显多于最优路径：规则策略在震荡中反复进出，成本吞噬收益（过度交易）。")
    if r.n_trades == 0:
        msgs.append("B 模式全日无交易：策略未触发或被收盘前空仓规则限制，此时“不交易”是正确输出而非缺陷。")
    if h.n_trades == 0:
        msgs.append("A 模式亦无交易：即使事后看全日行情，任何波段的涨幅都不足以覆盖交易成本——该日不存在正期望机会。")
    ret_day = bars["close"].iloc[-1] / bars["open"].iloc[0] - 1
    msgs.append(f"参考：当日买入持有（不含成本）{ret_day:+.2%}。")
    if r.unverifiable:
        msgs.append("注意：B 模式订单规模超过流动性深度假设的 10%，成交结果标记为不可验证。")
    return msgs


def run_ab(bars: pd.DataFrame, strategies: list, cost: CostModel, capital_usd: float, cost_profile_name: str) -> ABComparison:
    bars = bars.reset_index(drop=True)
    h = optimal_trades(bars, cost, capital_usd)
    rep = ExecutableReplay(cost, capital_usd)
    results = {s.name: rep.run(bars, s) for s in strategies}
    best = max(results, key=lambda k: results[k].net_return) if results else None
    expl = explain_gap(h, results[best], bars, cost) if best else []
    sim = bool((bars["quality"].astype(int) & 128).any())
    return ABComparison(symbol=str(bars["symbol"].iloc[0]), day=str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]),
                        capital_usd=capital_usd, cost_profile=cost_profile_name, hindsight=h, executable=results,
                        best_strategy=best, gap_explanation=expl, data_is_simulated=sim)
