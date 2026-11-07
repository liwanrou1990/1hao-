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
    entry_mode: str = "market"
    atr_entry: float = 0.0
    invalidation_price: float | None = None
    partial_done: bool = False
    partial_pnl: float = 0.0
    qty_initial: float = 0.0
    slippage_cost: float = 0.0
    peak_favor: float = 0.0

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
    """市价成交的单边不利偏移 = 滑点 + 半价差（spread_bps 为全价差，默认 0 以保持与前两阶段口径一致；压力测试时设置）。"""
    return (float(costs[instrument]["slippage_bps"]) + 0.5 * float(costs[instrument].get("spread_bps", 0.0))) / 1e4


class PositionManager:
    def __init__(self, c: dict, filters_enabled: bool = True):
        self.c = c; self.costs = c["costs"]; self.rc = c["risk"]; self.bt = c["backtest"]; self.filters_enabled = filters_enabled
        self.ex = c.get("exits", {}) or {}
        self.limit_cfg = (c.get("execution_modes", {}) or {}).get("limit", {}) or {}

    # ---------- 退出配置解析 ----------
    def _resolve_stop_tp(self, sig: dict, fill: float, d: str) -> tuple[float, float, float | None]:
        """按 exits 配置决定初始止损/止盈。返回 (stop, tp, trail_after_rr)。never_widen_stop：止损只能比策略止损更紧或相同。"""
        stop = float(sig["stop_price"]); tp = float(sig["take_profit"])
        struct = sig.get("structure_price"); micro = sig.get("micro_stop_price")
        mode = self.ex.get("stop_mode", "atr_buffer")
        if mode == "fixed_structure" and struct is not None:
            stop = float(struct)
        elif mode == "micro" and micro is not None:
            # 低周期微结构止损：只允许比策略止损更紧（never widen）
            stop = float(micro) if ((d == "long" and micro > stop) or (d == "short" and micro < stop)) else stop
        tp_mode = self.ex.get("tp_mode", "next_zone_or_rr")
        risk = abs(fill - stop)
        if tp_mode == "rr":
            tp_rr = float(self.c["strategies"].get(sig["strategy"], {}).get("tp_rr", 2.0))
            tp = fill + tp_rr * risk if d == "long" else fill - tp_rr * risk
        elif tp_mode == "none":
            tp = float("inf") if d == "long" else 0.0
        be = self.ex.get("breakeven_after_rr", "__unset__")
        trail = sig.get("trail_after_rr") if be == "__unset__" else be
        return stop, tp, trail

    def fill_entry(self, pend: dict, o: float, t: pd.Timestamp, i: int, equity_now: float, bar_sec: int, fill_price: float | None = None, maker: bool = False) -> tuple[Position | None, float, dict | None]:
        """返回 (持仓或 None, 现金变化（入场手续费，负数）, 取消记录或 None)。fill_price 给定时（限价成交）按该价成交；maker=True 用 maker 费且无滑点。"""
        sig, inst = pend["signal"], pend["instrument"]; d = sig["direction"]; costs = self.costs
        slip = 0.0 if maker else slip_rate(costs, inst)
        base = fill_price if fill_price is not None else o
        fill = base * (1 + slip) if d == "long" else base * (1 - slip)
        stop, tp, trail = self._resolve_stop_tp(sig, fill, d)
        if (d == "long" and fill <= stop) or (d == "short" and fill >= stop):
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": "成交价已越过止损，取消入场"}
        hold = int(sig.get("max_hold_bars") or 96) if self.ex.get("time_exit", True) else None
        cost = estimate_round_trip_cost(fill, stop, costs, inst, d, hold or 96, bar_sec)
        nrr = net_rr(fill, stop, tp, cost["cost_per_unit"]) if np.isfinite(tp) and tp > 0 else float("inf")
        if self.filters_enabled and nrr < float(self.c["filters"]["cost"]["min_net_rr"]):
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": f"成交价 {fill:.2f} 使净盈亏比降至 {nrr:.2f}，取消入场"}
        sz = size_position(equity_now, fill, stop, d, sig["symbol"], self.rc, costs, inst, hold_bars=hold or 96, bar_seconds=bar_sec)
        if not sz.ok:
            return None, 0.0, {"ts": str(t), "symbol": sig["symbol"], "strategy": sig["strategy"], "reason": "成交时仓位不可行：" + "；".join(sz.reasons)}
        fee = fill * sz.qty * fee_rate(costs, inst, "maker" if maker else "taker")
        p = Position(sig["symbol"], sig["strategy"], d, inst, sz.qty, fill, t, i, stop, tp, stop, sz.risk_usd, pd.Timestamp(pend["signal_ts"]), sig, sz.to_dict(), fees=fee,
                     trail_after_rr=trail, max_hold_bars=hold, regime=pend.get("regime", ""), trend_4h=pend.get("trend_4h", ""), net_rr_planned=nrr,
                     position_id=f"{sig['setup_id']}@{t.isoformat()}", entry_mode="limit" if fill_price is not None else "market", atr_entry=float(sig.get("atr") or 0.0),
                     invalidation_price=sig.get("invalidation_price"), qty_initial=sz.qty, slippage_cost=abs(fill - base) * sz.qty)
        return p, -fee, None

    def limit_fill_check(self, pend: dict, o: float, h: float, l: float, c: float, i: int) -> tuple[str, float | None]:
        """限价单在本根的状态：('fill', 价) | ('cancel:<原因>', None) | ('wait', None)。保守：须穿越挂单价 ≥ penetration_atr×ATR 才算成交；跳空越过按开盘价成交。"""
        sig = pend["signal"]; d = sig["direction"]; lp = float(sig["limit_price"]); atr = float(sig.get("atr") or 0.0)
        pen = float(self.limit_cfg.get("penetration_atr", 0.05)) * atr
        inv = sig.get("invalidation_price"); tp = float(sig["take_profit"])
        if d == "long":
            if o <= lp:
                return "fill", o
            if l <= lp - pen:
                return "fill", lp
            if self.limit_cfg.get("cancel_if_tp_hit_first", True) and h >= tp:
                return "cancel:tp_hit_before_fill", None
            if self.limit_cfg.get("cancel_on_invalidation", True) and inv is not None and c < float(inv):
                return "cancel:structure_invalidated", None
        else:
            if o >= lp:
                return "fill", o
            if h >= lp + pen:
                return "fill", lp
            if self.limit_cfg.get("cancel_if_tp_hit_first", True) and l <= tp:
                return "cancel:tp_hit_before_fill", None
            if self.limit_cfg.get("cancel_on_invalidation", True) and inv is not None and c > float(inv):
                return "cancel:structure_invalidated", None
        if i >= int(pend.get("expires_idx", 10 ** 9)):
            return "cancel:timeout", None
        return "wait", None

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
        p.mae = max(p.mae, adverse); p.mfe = max(p.mfe, favor); p.peak_favor = max(p.peak_favor, favor)
        risk = abs(p.entry_price - p.init_stop)
        # 分批止盈（达到 at_rr 时按目标价平掉 fraction，余仓止损移到保本）
        pt = self.ex.get("partial_tp")
        if pt and not p.partial_done and favor >= float(pt["at_rr"]) * risk and p.qty_initial > 0:
            px = p.entry_price + float(pt["at_rr"]) * risk if long else p.entry_price - float(pt["at_rr"]) * risk
            q = p.qty * float(pt["fraction"])
            fee = px * q * fee_rate(costs, p.instrument)
            gross = (px - p.entry_price) * q * (1 if long else -1)
            p.partial_pnl += gross; p.fees += fee; p.qty -= q; p.partial_done = True
            cushion = 2 * fee_rate(costs, p.instrument) + 2 * slip_rate(costs, p.instrument) + stop_slip
            be = p.entry_price * (1 + cushion) if long else p.entry_price * (1 - cushion)
            p.stop = max(p.stop, be) if long else min(p.stop, be); p.trailed = True
            return False, gross - fee
        # 结构反转退出：收盘越过失效价
        if self.ex.get("structure_exit", False) and p.invalidation_price is not None:
            if (long and cl < float(p.invalidation_price)) or ((not long) and cl > float(p.invalidation_price)):
                return True, self.close(p, cl, t, i, "structure_exit", trades)
        if p.max_hold_bars and i - p.entry_idx >= int(p.max_hold_bars):
            return True, self.close(p, cl, t, i, "time_stop", trades)
        if p.trail_after_rr:
            if favor >= float(p.trail_after_rr) * risk:
                cushion = 2 * fee_rate(costs, p.instrument) + 2 * slip_rate(costs, p.instrument) + stop_slip
                be = p.entry_price * (1 + cushion) if long else p.entry_price * (1 - cushion)
                new_stop = max(p.stop, be) if long else min(p.stop, be)
                if new_stop != p.stop:
                    p.stop, p.trailed = new_stop, True
        at = self.ex.get("atr_trail")
        if at and p.atr_entry > 0 and p.peak_favor >= float(at["after_rr"]) * risk:
            lvl = cl - float(at["atr_mult"]) * p.atr_entry if long else cl + float(at["atr_mult"]) * p.atr_entry
            new_stop = max(p.stop, lvl) if long else min(p.stop, lvl)      # never_widen_stop：只向有利方向移动
            if new_stop != p.stop:
                p.stop, p.trailed = new_stop, True
        return False, 0.0

    def close(self, p: Position, px: float, t: pd.Timestamp, i: int, reason: str, trades: list[dict], slip_extra: float = 0.0) -> float:
        """平掉剩余仓位，生成交易记录。返回本次给现金账户的变化（主腿毛利 − 出场费 − 资金费；分批腿的现金已在分批时返还）。
        交易记录口径：gross_pnl = 主腿毛利 + 分批腿毛利；fees = 全部手续费（入场+分批+出场）；net_pnl = gross − fees − funding。"""
        costs = self.costs
        slip = slip_rate(costs, p.instrument) + slip_extra
        fill = px * (1 - slip) if p.direction == "long" else px * (1 + slip)
        p.slippage_cost += abs(fill - px) * p.qty
        main_gross = (fill - p.entry_price) * p.qty * (1 if p.direction == "long" else -1)
        fee = fill * p.qty * fee_rate(costs, p.instrument)
        p.fees += fee
        gross = main_gross + p.partial_pnl
        net = gross - p.fees - p.funding
        q0 = p.qty_initial or p.qty
        trades.append({"position_id": p.position_id, "symbol": p.symbol, "strategy": p.strategy, "direction": p.direction, "instrument": p.instrument, "signal_ts": p.signal_ts, "entry_ts": p.entry_ts, "exit_ts": t,
                       "entry_price": p.entry_price, "exit_price": fill, "stop_init": p.init_stop, "tp": p.tp, "qty": q0, "notional": q0 * p.entry_price, "leverage": p.sizing.get("leverage"),
                       "entry_mode": p.entry_mode, "slippage_cost": p.slippage_cost, "partial_gross": p.partial_pnl,
                       "risk_usd": p.risk_usd, "gross_pnl": gross, "fees": p.fees, "funding": p.funding, "net_pnl": net, "r_multiple": net / p.risk_usd if p.risk_usd else np.nan,
                       "exit_reason": reason, "bars_held": i - p.entry_idx, "regime": p.regime, "trend_4h": p.trend_4h, "zone_id": p.signal.get("zone", {}).get("zone_id"),
                       "zone_tfs": "/".join(p.signal.get("zone", {}).get("tfs", [])), "net_rr_planned": p.net_rr_planned, "mae": p.mae, "mfe": p.mfe, "reasons": " | ".join(p.signal.get("reasons", []))})
        return main_gross - fee - p.funding


def hypothetical_outcome(pm: "PositionManager", mc, pend: dict, i_entry: int, bar_sec: int, equity: float = 10000.0, max_bars: int | None = None) -> dict:
    """限价单未成交的机会损失评估：假设在 i_entry 开盘市价入场、同样止损止盈、同样退出规则，向前推演到退出。只用于统计，不进入组合。"""
    if i_entry >= mc.n:
        return {"status": "no_data"}
    pm2 = PositionManager(pm.c, pm.filters_enabled)
    p, _, canc = pm2.fill_entry(pend, float(mc.o[i_entry]), mc.ts.iloc[i_entry], i_entry, equity, bar_sec)
    if p is None:
        return {"status": "would_be_cancelled", "reason": canc["reason"] if canc else ""}
    trades: list[dict] = []
    end = mc.n if max_bars is None else min(mc.n, i_entry + max_bars)
    for j in range(i_entry, end):
        closed, _ = pm2.manage(p, float(mc.o[j]), float(mc.h[j]), float(mc.l[j]), float(mc.cl[j]), mc.ts.iloc[j], j, trades)
        if closed:
            return {"status": "closed", "net_pnl": trades[-1]["net_pnl"], "r_multiple": trades[-1]["r_multiple"], "exit_reason": trades[-1]["exit_reason"]}
    pm2.close(p, float(mc.cl[end - 1]), mc.ts.iloc[end - 1], end - 1, "open_at_horizon", trades)
    return {"status": "open_at_horizon", "net_pnl": trades[-1]["net_pnl"], "r_multiple": trades[-1]["r_multiple"], "exit_reason": "open_at_horizon"}
