"""Paper (simulated) trading recorder.

Records each TRADE signal, the simulated fill (quote + modeled slippage),
the execution deviation (fill vs quote), the net return after costs, and the
running equity / max drawdown. It NEVER connects a wallet or executes a real
transaction — all trades require human confirmation out of band. Signals come
only from real-data TRADE decisions; with no real data there are no signals.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class PaperTrade:
    ts: str
    symbol: str
    side: str                    # long | short
    quote_price: float
    edge_bps: float
    slippage_bps: float
    cost_bps: float
    fill_price: float
    exec_deviation_bps: float    # (fill-quote)/quote in bps
    realized_bps: Optional[float] = None   # filled in on close (real outcome)
    net_bps: Optional[float] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class PaperBook:
    trades: List[PaperTrade] = field(default_factory=list)

    def open_trade(self, symbol, side, quote_price, edge_bps, slippage_bps, cost_bps):
        # simulated fill applies adverse slippage to the quote
        sign = 1 if side == "long" else -1
        fill = quote_price * (1 + sign * slippage_bps / 1e4)
        dev = (fill - quote_price) / quote_price * 1e4
        t = PaperTrade(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       symbol=symbol, side=side, quote_price=quote_price,
                       edge_bps=edge_bps, slippage_bps=slippage_bps, cost_bps=cost_bps,
                       fill_price=fill, exec_deviation_bps=dev)
        self.trades.append(t)
        return t

    def close_trade(self, t: PaperTrade, exit_price: float):
        sign = 1 if t.side == "long" else -1
        gross = sign * (exit_price - t.fill_price) / t.fill_price * 1e4
        t.realized_bps = gross
        t.net_bps = gross - t.cost_bps - abs(t.exec_deviation_bps)
        return t

    def metrics(self) -> dict:
        closed = [t for t in self.trades if t.net_bps is not None]
        n = len(closed)
        if n == 0:
            return {"n_trades": len(self.trades), "n_closed": 0,
                    "net_return_bps": 0.0, "win_rate": None, "max_drawdown_bps": 0.0}
        nets = [t.net_bps for t in closed]
        wins = sum(1 for x in nets if x > 0)
        equity = 0.0; peak = 0.0; mdd = 0.0
        for x in nets:
            equity += x; peak = max(peak, equity); mdd = min(mdd, equity - peak)
        return {"n_trades": len(self.trades), "n_closed": n,
                "net_return_bps": sum(nets), "win_rate": wins / n,
                "max_drawdown_bps": mdd}

    def to_dict(self):
        return {"trades": [t.to_dict() for t in self.trades], "metrics": self.metrics()}
