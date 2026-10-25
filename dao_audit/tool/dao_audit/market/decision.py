"""TRADE / NO TRADE decision engine.

A TRADE is emitted ONLY when: all required data is real, OK and fresh; there
are at least `min_evidence_sources` independent OK sources; and expected net
(after ALL costs) exceeds `min_net_bps`. Everything else is NO TRADE with an
explicit reason. No fixed return is ever asserted — net is an estimate under
stated assumptions.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, List

from .costs import CostResult

TRADE = "TRADE"
NO_TRADE = "NO_TRADE"


@dataclass
class Decision:
    decision: str                 # TRADE | NO_TRADE
    reason: str
    net_bps: Optional[float]
    evidence_sources: int
    data_status: str              # OK | UNAVAILABLE | STALE | INSUFFICIENT_DATA
    disclaimer: str = ("估计值，非收益承诺；基于所列真实数据与假设。"
                       "未经人工确认不得连接钱包或执行真实交易。")

    def to_dict(self):
        return asdict(self)


def decide(cost: CostResult, evidence_sources: int, data_status: str,
           min_net_bps: float, min_evidence_sources: int) -> Decision:
    # 1) data integrity gates first
    if data_status != "OK":
        return Decision(NO_TRADE, f"数据状态={data_status}（真实数据不可得/陈旧），拒绝交易",
                        None, evidence_sources, data_status)
    if evidence_sources < min_evidence_sources:
        return Decision(NO_TRADE,
                        f"独立数据源不足 ({evidence_sources} < {min_evidence_sources})",
                        None, evidence_sources, data_status)
    if cost.status != "OK":
        return Decision(NO_TRADE, f"成本模型: {cost.reason}", None,
                        evidence_sources, "INSUFFICIENT_DATA")
    # 2) economics
    if cost.net_bps is None or cost.net_bps <= 0:
        return Decision(NO_TRADE, f"预期净收益非正 (net={cost.net_bps} bps)",
                        cost.net_bps, evidence_sources, data_status)
    if cost.net_bps < min_net_bps:
        return Decision(NO_TRADE,
                        f"净收益 {cost.net_bps:.1f} bps 低于阈值 {min_net_bps} bps",
                        cost.net_bps, evidence_sources, data_status)
    return Decision(TRADE,
                    f"净收益 {cost.net_bps:.1f} bps ≥ 阈值 {min_net_bps} bps，"
                    f"且 {evidence_sources} 个真实源一致",
                    cost.net_bps, evidence_sources, data_status)
