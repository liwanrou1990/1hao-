"""Event-driven backtest on REAL historical on-chain/market data.

Strictly no lookahead: the decision at an event's timestamp uses only data with
ts <= event_ts; the realized outcome uses price at event_ts + horizon (which is
historical, already settled). Splits the event timeline into train / validation
/ out-of-sample (OOS) windows. If historical data is unavailable (egress
blocked), returns INSUFFICIENT_DATA rather than inventing a series.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from .sources import DefiLlamaSource
from .provenance import OK


@dataclass
class Event:
    ts: int                 # unix timestamp of the on-chain event
    coin_key: str
    label: str              # e.g. "proposal_passed"


@dataclass
class SplitMetrics:
    name: str
    n_trades: int = 0
    wins: int = 0
    net_return: float = 0.0        # sum of per-trade net returns (fraction)
    returns: List[float] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.n_trades if self.n_trades else None

    @property
    def max_drawdown(self) -> float:
        peak = 0.0; equity = 0.0; mdd = 0.0
        for r in self.returns:
            equity += r
            peak = max(peak, equity)
            mdd = min(mdd, equity - peak)
        return mdd


def _split(events: List[Event], train=0.5, val=0.25):
    events = sorted(events, key=lambda e: e.ts)
    n = len(events)
    a = int(n * train); b = int(n * (train + val))
    return events[:a], events[a:b], events[b:]


def run_backtest(events: List[Event], horizon_s: int, cost_bps: float,
                 price_at: Callable[[str, int], Optional[float]]) -> dict:
    """price_at(coin_key, ts) -> real historical price or None (no fabrication)."""
    train, val, oos = _split(events)
    out = {"horizon_s": horizon_s, "cost_bps": cost_bps,
           "no_lookahead": True, "splits": {}}
    total_priced = 0
    for name, evs in (("train", train), ("validation", val), ("oos", oos)):
        m = SplitMetrics(name)
        for e in evs:
            p0 = price_at(e.coin_key, e.ts)              # decision-time price (<= event)
            p1 = price_at(e.coin_key, e.ts + horizon_s)  # realized (historical)
            if p0 is None or p1 is None:
                m.failures.append(f"missing price for {e.coin_key}@{e.ts}")
                continue
            total_priced += 1
            gross = (p1 - p0) / p0
            net = gross - cost_bps / 1e4
            m.n_trades += 1
            m.returns.append(net)
            m.net_return += net
            if net > 0:
                m.wins += 1
        out["splits"][name] = {
            "n_trades": m.n_trades, "win_rate": m.win_rate,
            "net_return": m.net_return, "max_drawdown": m.max_drawdown,
            "failures": m.failures[:10], "n_failures": len(m.failures),
        }
    if total_priced == 0:
        out["status"] = "INSUFFICIENT_DATA"
        out["reason"] = ("无法获取真实历史价格（数据源不可达）；"
                         "不以模拟行情替代，回测结果不可用。")
    else:
        out["status"] = OK
    return out


def defillama_price_at(dl: Optional[DefiLlamaSource] = None):
    dl = dl or DefiLlamaSource()

    def _fn(coin_key: str, ts: int) -> Optional[float]:
        rec = dl.price_at(coin_key, ts)
        if rec.ok and isinstance(rec.data, dict):
            info = rec.data.get("coins", {}).get(coin_key)
            if info and "price" in info:
                return float(info["price"])
        return None
    return _fn
