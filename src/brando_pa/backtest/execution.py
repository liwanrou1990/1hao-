"""持仓/成交规则（回测与模拟盘共用，保证两者口径完全一致）。

PositionManager：
  fill_entry  按开盘价±滑点成交；成交前重算净盈亏比与仓位，不达标取消
  manage      跳空 → 同根止盈止损（保守止损优先）→ 止损/止盈 → MAE/MFE → 时间止损 → 保本移动止损（下一根生效）
  close       按成交价扣手续费/资金费，生成交易记录
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..risk.costs import estimate_round_trip_cost, net_rr
from ..risk.sizing import size_position

FUNDING_HOURS = (0, 8, 16)


@dataclass
class Position:
    symbol: str; strategy: str; direction: str; instrument: str
    qty: float; entry_price: float; entry_ts: pd.Timestamp; entry_idx: int
    stop: float; tp: float; init_stop: float; risk_usd: float
    signal_ts: pd.Timestamp; signal: dict; sizing: dict
    fees: float = 0.0; funding: float = 0.0
    trail_after_rr: float | None = None; max_hold_bars: int | None = None
    regime: str = ""; trend_4h: str = ""; mae: float = 0.0; mfe: float = 0.0; trailed: bool = False
    net_rr_planned: float = 0.0
    position_id: str = ""

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * (1 if self.direction == "long" else -1)

    def to_dict(self) -> dict:
        d = asdict(self); d["entry_ts"] = str(self.entry_ts); d["signal_ts"] = str(self.signal_ts); return d

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = dict(d); d["entry_ts"] = pd.Timestamp(d["entry_ts"]); d["signal_ts"] = pd.Timestamp(d["signal_ts"]); return cls(**d)


def fee_rate(costs: dict, instrument: str, kind: str = "taker") -> float:
    return float(costs[instrument][f"{kind}_fee_bps"]) / 1e4


def slip_rate(costs: dict, instrument: str) -> float:
    return float(costs[instrument]["slippage_bps"]) / 1e4


class PositionManager:
    def __init__(self, c: dict, filters_enabled: bool = True):
        self.c = c; self.costs = c["costs"]; self.rc = c["risk"]; self.bt = c["backtest"]; self.filters_enabled = filters_enabled

    def fill_entry(self, pend: dict, o: float, t: pd.Timestamp, i: int, equity_now: float, bar_sec: int) -> tuple[Position | None, float, dict | None]:
        """返回 (持仓或 None, 现金变化（入场手续费，负数）, 取消记录或 None)。"""
        sig, inst = pend["signal"], pend["instrument"]; d = sig["direction"]; costs = self.costs
        slip = slip_rate(costs, inst)
        fill = o * (1 + slip) if d == "long" else o * (1 - slip)
        stop, tp = float(sig["stop_price"]), float(sig["take_profit"])
        if (d == "long" and fill <= stop) or (d == "short" and fill >= stop):
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": "开盘价已越过止损，取消入场"}
        hold = int(sig.get("max_hold_bars") or 96)
        cost = estimate_round_trip_cost(fill, stop, costs, inst, d, hold, bar_sec)
        nrr = net_rr(fill, stop, tp, cost["cost_per_unit"])
        if self.filters_enabled and nrr < float(self.c["filters"]["cost"]["min_net_rr"]):
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": f"成交价 {fill:.2f} 使净盈亏比降至 {nrr:.2f}，取消入场"}
        sz = size_position(equity_now, fill, stop, d, sig["symbol"], self.rc, costs, inst, hold_bars=hold, bar_seconds=bar_sec)
        if not sz.ok:
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": "成交时仓位不可行：" + "；".join(sz.reasons)}
        fee = fill * sz.qty * fee_rate(costs, inst)
        p = Position(sig["symbol"], sig["strategy"], d, inst, sz.qty, fill, t, i, stop, tp, stop, sz.risk_usd, pd.Timestamp(pend["signal_ts"]), sig, sz.to_dict(), fees=fee,
                     trail_after_rr=sig.get("trail_after_rr"), max_hold_bars=sig.get("max_hold_bars"), regime=pend.get("regime", ""), trend_4h=pend.get("trend_4h", ""), net_rr_planned=nrr,
                     position_id=f"{sig['setup_id']}@{t.isoformat()}")
        return p, -fee, None

    def manage(self, p: Position, o: float, h: float, l: float, cl: float, t: pd.Timestamp, i: int, trades: list[dict]) -> tuple[bool, float]:
        costs, bt = self.costs, self.bt
        long = p.direction == "long"
        if p.instrument == "perp":
            f = costs["funding"]; rate = float(f.get("assumed_rate_8h", 0.0))
            n_cross = 1 if (t.hour in FUNDING_HOURS and t.minute == 0) else 0
            if n_cross and ((long and f.get("long_pays", True)) or (not long and rate < 0)):
                p.funding += abs(rate) * p.qty * o * n_cross
        stop_slip = float(costs.get("stop_slippage_bps", 0)) / 1e4
        hit_stop = (l <= p.stop) if long else (h >= p.stop)
        hit_tp = (h >= p.tp) if long else (l <= p.tp)
        gap_stop = (o <= p.stop) if long else (o >= p.stop)
        gap_tp = (o >= p.tp) if long else (o <= p.tp)
        if gap_stop:
            return True, self.close(p, o, t, i, "stop_gap", trades, slip_extra=stop_slip)
        if gap_tp:
            return True, self.close(p, o, t, i, "tp_gap", trades)
        if hit_stop and hit_tp:
            if bt.get("same_bar_rule", "stop_first") == "stop_first":
                return True, self.close(p, p.stop, t, i, "stop_same_bar", trades, slip_extra=stop_slip)
            return True, self.close(p, p.tp, t, i, "tp_same_bar", trades)
        if hit_stop:
            return True, self.close(p, p.stop, t, i, "trail_stop" if p.trailed else "stop", trades, slip_extra=stop_slip)
        if hit_tp:
            return True, self.close(p, p.tp, t, i, "take_profit", trades)
        adverse = (p.entry_price - l) if long else (h - p.entry_price); favor = (h - p.entry_price) if long else (p.entry_price - l)
        p.mae = max(p.mae, adverse); p.mfe = max(p.mfe, favor)
        if p.max_hold_bars and i - p.entry_idx >= int(p.max_hold_bars):
            return True, self.close(p, cl, t, i, "time_stop", trades)
        if p.trail_after_rr:
            risk = abs(p.entry_price - p.init_stop)
            if favor >= float(p.trail_after_rr) * risk:
                cushion = 2 * fee_rate(costs, p.instrument) + 2 * slip_rate(costs, p.instrument) + stop_slip
                be = p.entry_price * (1 + cushion) if long else p.entry_price * (1 - cushion)
                new_stop = max(p.stop, be) if long else min(p.stop, be)
                if new_stop != p.stop:
                    p.stop, p.trailed = new_stop, True
        return False, 0.0

    def close(self, p: Position, px: float, t: pd.Timestamp, i: int, reason: str, trades: list[dict], slip_extra: float = 0.0) -> float:
        costs = self.costs
        slip = slip_rate(costs, p.instrument) + slip_extra
        fill = px * (1 - slip) if p.direction == "long" else px * (1 + slip)
        gross = (fill - p.entry_price) * p.qty * (1 if p.direction == "long" else -1)
        fee = fill * p.qty * fee_rate(costs, p.instrument)
        p.fees += fee
        net = gross - p.fees - p.funding
        trades.append({"position_id": p.position_id, "symbol": p.symbol, "strategy": p.strategy, "direction": p.direction, "instrument": p.instrument, "signal_ts": p.signal_ts, "entry_ts": p.entry_ts, "exit_ts": t,
                       "entry_price": p.entry_price, "exit_price": fill, "stop_init": p.init_stop, "tp": p.tp, "qty": p.qty, "notional": p.qty * p.entry_price, "leverage": p.sizing.get("leverage"),
                       "risk_usd": p.risk_usd, "gross_pnl": gross, "fees": p.fees, "funding": p.funding, "net_pnl": net, "r_multiple": net / p.risk_usd if p.risk_usd else np.nan,
                       "exit_reason": reason, "bars_held": i - p.entry_idx, "regime": p.regime, "trend_4h": p.trend_4h, "zone_id": p.signal.get("zone", {}).get("zone_id"),
                       "zone_tfs": "/".join(p.signal.get("zone", {}).get("tfs", [])), "net_rr_planned": p.net_rr_planned, "mae": p.mae, "mfe": p.mfe, "reasons": " | ".join(p.signal.get("reasons", []))})
        return gross - fee - p.funding
