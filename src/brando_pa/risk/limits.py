"""组合级风控：总风险敞口、同向相关性、最大持仓数、单日亏损暂停、禁止补仓/马丁、异常停机。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskState:
    equity: float
    day: str | None = None                    # UTC 日期字串
    day_start_equity: float | None = None
    day_realized_pnl: float = 0.0
    halted_until_day: str | None = None
    halt_reasons: list[str] = field(default_factory=list)
    consecutive_losses: int = 0

    def roll_day(self, day: str) -> None:
        if self.day != day:
            self.day = day; self.day_start_equity = self.equity; self.day_realized_pnl = 0.0
            if self.halted_until_day is not None and day >= self.halted_until_day:
                self.halted_until_day = None; self.halt_reasons = []


def check_new_position(sig_direction: str, symbol: str, qty_risk_usd: float, open_positions: list[dict], state: RiskState, risk_cfg: dict,
                       unrealized_pnl: float = 0.0, data_ok: bool = True, exchange_ok: bool = True) -> tuple[bool, list[str]]:
    """返回 (允许开仓, 理由列表)。open_positions 元素需含 symbol, direction, risk_usd, strategy。"""
    reasons: list[str] = []
    if not data_ok:
        reasons.append("行情数据过期/缺失，禁止新开仓")
    if not exchange_ok:
        reasons.append("交易所/接口状态异常，禁止新开仓")
    if state.halted_until_day is not None:
        reasons.append(f"单日亏损触发暂停至 {state.halted_until_day}（{'; '.join(state.halt_reasons)}）")
    # 单日亏损（已实现 + 浮动）
    if state.day_start_equity:
        day_loss = -(state.day_realized_pnl + unrealized_pnl) / state.day_start_equity
        if day_loss >= float(risk_cfg["daily_loss_limit"]):
            reasons.append(f"当日亏损 {day_loss:.2%} ≥ 限额 {float(risk_cfg['daily_loss_limit']):.2%}")
    if len(open_positions) >= int(risk_cfg["max_positions"]):
        reasons.append(f"持仓数 {len(open_positions)} 已达上限 {risk_cfg['max_positions']}")
    total_risk = sum(p.get("risk_usd", 0.0) for p in open_positions) + qty_risk_usd
    if total_risk > float(risk_cfg["max_total_risk"]) * state.equity:
        reasons.append(f"总风险敞口 {total_risk/state.equity:.2%} 超过上限 {float(risk_cfg['max_total_risk']):.2%}")
    groups = risk_cfg.get("correlation_group", {})
    g = groups.get(symbol)
    same = [p for p in open_positions if p["direction"] == sig_direction and groups.get(p["symbol"]) == g]
    if g is not None and len(same) >= int(risk_cfg["max_same_direction_correlated"]):
        reasons.append(f"同向相关持仓 {len(same)} 已达上限 {risk_cfg['max_same_direction_correlated']}（{g}）")
    if risk_cfg.get("forbid_averaging_down", True):
        for p in open_positions:
            if p["symbol"] == symbol and p["direction"] == sig_direction:
                reasons.append("已有同向持仓，禁止补仓/摊平"); break
    return (not reasons), reasons
