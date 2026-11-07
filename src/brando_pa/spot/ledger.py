"""现货账本：USDT 现金 + 各资产数量；买入必须有可用现金，卖出不得超过持仓；无杠杆、无负持仓；FIFO 成本与已实现/未实现盈亏。"""
from __future__ import annotations

from dataclasses import dataclass, field


class SpotConstraintError(RuntimeError):
    pass


@dataclass
class Lot:
    qty: float
    price: float
    ts: str
    strategy: str
    stop: float
    init_stop: float
    atr: float
    risk_usd: float
    lot_id: str
    trailed: bool = False
    mae: float = 0.0
    mfe: float = 0.0


@dataclass
class SpotLedger:
    cash: float
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    lots: dict = field(default_factory=dict)          # symbol -> list[Lot]（FIFO）
    realized: float = 0.0
    fees_paid: float = 0.0
    trade_log: list = field(default_factory=list)
    _seq: int = 0

    def qty(self, symbol: str) -> float:
        return float(sum(l.qty for l in self.lots.get(symbol, [])))

    def avg_cost(self, symbol: str) -> float | None:
        q = self.qty(symbol)
        return float(sum(l.qty * l.price for l in self.lots[symbol]) / q) if q > 0 else None

    def equity(self, prices: dict) -> float:
        return float(self.cash + sum(self.qty(s) * float(prices.get(s, 0.0)) for s in self.lots))

    def exposure(self, prices: dict) -> float:
        eq = self.equity(prices)
        return float(sum(self.qty(s) * float(prices.get(s, 0.0)) for s in self.lots) / eq) if eq > 0 else 0.0

    def unrealized(self, prices: dict) -> float:
        return float(sum((float(prices.get(s, 0.0)) - l.price) * l.qty for s, ls in self.lots.items() for l in ls))

    def buy(self, symbol: str, qty: float, ref_price: float, ts, strategy: str, stop: float, atr: float, risk_usd: float) -> Lot:
        if qty <= 0:
            raise SpotConstraintError("数量必须为正")
        fill = ref_price * (1 + self.slippage_bps / 1e4)
        fee = fill * qty * self.fee_bps / 1e4
        cost = fill * qty + fee
        if cost > self.cash + 1e-9:
            raise SpotConstraintError(f"现金不足：需要 {cost:.2f}，可用 {self.cash:.2f}")
        self.cash -= cost; self.fees_paid += fee; self._seq += 1
        lot = Lot(qty, fill, str(ts), strategy, stop, stop, atr, risk_usd, f"{symbol}-{self._seq}")
        self.lots.setdefault(symbol, []).append(lot)
        self.trade_log.append({"ts": str(ts), "symbol": symbol, "side": "BUY", "qty": qty, "price": fill, "fee": fee, "cash_after": self.cash, "strategy": strategy, "stop": stop, "lot_id": lot.lot_id})
        return lot

    def sell(self, symbol: str, qty: float, ref_price: float, ts, reason: str, lot_ids: list[str] | None = None) -> dict:
        held = self.qty(symbol)
        if qty <= 0:
            raise SpotConstraintError("数量必须为正")
        if qty > held + 1e-12:
            raise SpotConstraintError(f"卖出 {qty} 超过持仓 {held}")
        qty = min(qty, held)
        fill = ref_price * (1 - self.slippage_bps / 1e4)
        fee = fill * qty * self.fee_bps / 1e4
        remaining = qty; pnl = 0.0; closed = []
        pool = [l for l in self.lots[symbol] if (lot_ids is None or l.lot_id in lot_ids)]
        for lot in pool:
            if remaining <= 1e-15:
                break
            take = min(lot.qty, remaining)
            pnl += (fill - lot.price) * take
            lot.qty -= take; remaining -= take
            closed.append({"lot_id": lot.lot_id, "qty": take, "entry": lot.price, "strategy": lot.strategy, "risk_usd": lot.risk_usd, "init_stop": lot.init_stop, "entry_ts": lot.ts, "mae": lot.mae, "mfe": lot.mfe})
        if remaining > 1e-9:
            raise SpotConstraintError("指定批次数量不足")
        self.lots[symbol] = [l for l in self.lots[symbol] if l.qty > 1e-15]
        self.cash += fill * qty - fee; self.fees_paid += fee
        self.realized += pnl - fee
        rec = {"ts": str(ts), "symbol": symbol, "side": "SELL", "qty": qty, "price": fill, "fee": fee, "cash_after": self.cash, "reason": reason, "gross_pnl": pnl, "net_pnl": pnl - fee, "closed_lots": closed}
        self.trade_log.append(rec)
        return rec

    def snapshot(self, prices: dict) -> dict:
        return {"cash": self.cash, "holdings": {s: self.qty(s) for s in self.lots if self.qty(s) > 0}, "equity": self.equity(prices), "unrealized": self.unrealized(prices), "realized": self.realized, "fees_paid": self.fees_paid, "exposure": self.exposure(prices)}
