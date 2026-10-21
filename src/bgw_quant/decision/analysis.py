"""逐档位未达标原因归类：产品不支持 / 流动性不足 / 资金容量限制 / 成本过高 / 策略不足 / 目标高于理论上限。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..costs.model import CostModel
from ..target_model.execution import bar_liquidity
from .capability import ProductCapability
from .engine import DayRun


def classify(bars: pd.DataFrame, cost: CostModel, cap: ProductCapability, run: DayRun, theoretical_usd: float, target_usd: float,
             participation_rate: float, batch_bars: int, oos_attainment: float | None = None) -> dict:
    close = bars["close"].to_numpy(float)
    liq = bar_liquidity(bars, participation_rate)
    known = bool(np.isfinite(liq).all())
    per_batch = float(np.nanmedian(liq)) * batch_bars if known else float("nan")
    day_budget = float(np.sum(liq)) if known else float("nan")
    h = 60
    moves = np.abs(close[h:] / close[:-h] - 1) if len(close) > h else np.array([0.0])
    p90 = float(np.percentile(moves, 90)) if len(moves) else 0.0
    size_ref = float(np.median([t.size_usd for t in run.trades])) if run.trades else min(run.capital_usd / max(1, run.params.max_open), per_batch if known else run.capital_usd)
    rt = cost.round_trip_fraction(size_ref)
    tags, details = [], []
    if not cap.spot_long and cap.verification_status == "unsupported":
        tags.append("产品不支持"); details.append("官方 stockList 不存在该 ticker")
    elif not cap.spot_long:
        tags.append("未验证"); details.append("资产身份与可交易渠道尚未通过官方 API 核实（无法在当前环境核实），未判定支持或不支持")
    if "short" not in cap.directions and run.pause_reasons.get("下跌趋势但产品不支持做空", 0) > 0:
        tags.append("产品不支持做空"); details.append(f"{run.pause_reasons['下跌趋势但产品不支持做空']} 根K线处于下跌趋势但无可核实做空渠道")
    if theoretical_usd < target_usd:
        tags.append("目标高于理论上限"); details.append(f"即使事后最优、不计流动性与冲击，当日上限 {theoretical_usd:,.0f} USD < 目标 {target_usd:,.0f} USD")
    if rt > p90:
        tags.append("成本过高"); details.append(f"单笔 {size_ref:,.0f} USD 往返成本 {rt:.2%} > 60 根波动 90 分位 {p90:.2%}（引擎因此暂停 {run.pause_reasons.get('成本门槛高于预期波动', 0)} 次）")
    if known and run.capital_usd / max(1, run.params.max_open) > per_batch:
        tags.append("流动性不足"); details.append(f"单笔计划 {run.capital_usd / max(1, run.params.max_open):,.0f} USD > 分批可成交 {per_batch:,.0f} USD（全日参与率预算 {day_budget:,.0f} USD）")
    if known and run.capital_usd > day_budget:
        tags.append("资金容量限制"); details.append(f"本金 {run.capital_usd:,.0f} USD > 全日可成交预算 {day_budget:,.0f} USD，无法在一日内完整周转")
    if not known:
        tags.append("流动性未知"); details.append("数据源无成交额，无法验证容量")
    if theoretical_usd >= target_usd and not run.target_met and "成本过高" not in tags and "流动性不足" not in tags:
        tags.append("策略不足"); details.append(f"理论上限可达标但因果引擎只实现 {run.realized_net_usd:,.0f} USD（无未来信息下的预测不足）")
    if oos_attainment is not None and oos_attainment < 0.5 and run.target_met:
        tags.append("昨日达标但样本外不稳定"); details.append(f"滚动样本外达标率 {oos_attainment:.0%}")
    if not tags and not run.target_met:
        tags.append("机会不足"); details.append(f"可执行交易累计 {run.realized_net_usd:,.0f} USD，缺口 {target_usd - run.realized_net_usd:,.0f} USD")
    return {"tags": tags, "details": details, "round_trip_cost_pct": rt * 100, "move_p90_pct": p90 * 100, "size_ref_usd": size_ref,
            "per_batch_liquidity_usd": per_batch, "day_liquidity_budget_usd": day_budget, "gap_usd": max(0.0, target_usd - run.realized_net_usd)}
