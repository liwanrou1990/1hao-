"""Event-edge estimator.

Estimates whether a DAO governance event type produces a *tradeable* forward
edge, using ONLY real historical price/volume series. It computes, per
(event_type, asset, horizon): the realized forward-return distribution,
benchmark-excess return, win rate, cost-adjusted return, a t-statistic and a
seeded bootstrap confidence interval.

Integrity:
- Strict chronological train/validation/out-of-sample split. Edge parameters
  come only from train+validation events, which are strictly earlier in time
  than OOS events. No lookahead: the decision-time price uses observations at
  t <= event_ts; the realized forward price is historical (settled).
- An edge is "confirmed" only if the in-sample mean excess is positive AND
  statistically significant AND the out-of-sample cost-adjusted mean excess is
  also positive. Otherwise edge_bps is None -> downstream NO_TRADE.
- Never invents an edge: if there is no real priced data, status is
  INSUFFICIENT_DATA and edge_bps is None.
"""
from __future__ import annotations
import math
import random
import statistics
from statistics import NormalDist
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Tuple

# series_provider(asset, from_ts, to_ts) -> list of (ts, price, volume) ascending
SeriesProvider = Callable[[str, int, int], List[Tuple[int, float, Optional[float]]]]


@dataclass
class Event:
    ts: int
    asset: str
    event_type: str


def price_at_or_before(series, ts) -> Optional[float]:
    """Decision-time price: last observation at obs_ts <= ts (no lookahead)."""
    p = None
    for o_ts, price, _ in series:
        if o_ts <= ts:
            p = price
        else:
            break
    return p


def price_at_or_after(series, ts) -> Optional[float]:
    """Realized forward price: first observation at obs_ts >= ts (historical)."""
    for o_ts, price, _ in series:
        if o_ts >= ts:
            return price
    return None


def realized_vol(series, t0: int, lookback_s: int) -> Optional[float]:
    pts = [p for (o, p, _) in series if t0 - lookback_s <= o <= t0]
    if len(pts) < 3:
        return None
    rets = [math.log(pts[i] / pts[i - 1]) for i in range(1, len(pts)) if pts[i - 1] > 0]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets)


def _summary(returns: List[float], cost_bps: float, seed: int = 1337, boots: int = 2000) -> dict:
    n = len(returns)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "win_rate": None,
                "t_stat": None, "ci95": None, "cost_adj_mean": None,
                "cost_adj_bps": None}
    mean = statistics.fmean(returns)
    std = statistics.pstdev(returns) if n > 1 else 0.0
    win = sum(1 for r in returns if r > 0) / n
    se = (std / math.sqrt(n)) if (std > 0 and n > 1) else None
    t_stat = (mean / se) if se else None
    # seeded bootstrap CI of the mean
    rng = random.Random(seed)
    means = []
    for _ in range(boots):
        samp = [returns[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(samp))
    means.sort()
    lo = means[int(0.025 * boots)]
    hi = means[min(int(0.975 * boots), boots - 1)]
    cost = cost_bps / 1e4
    cadj = mean - cost
    # max drawdown of the cumulative per-event net-return path (chronological)
    equity = 0.0; peak = 0.0; mdd = 0.0
    for r in returns:
        equity += (r - cost); peak = max(peak, equity); mdd = min(mdd, equity - peak)
    return {"n": n, "mean": mean, "std": std, "win_rate": win,
            "t_stat": t_stat, "ci95": [lo, hi],
            "cost_adj_mean": cadj, "cost_adj_bps": cadj * 1e4,
            "max_drawdown": mdd, "max_drawdown_bps": mdd * 1e4}


@dataclass
class EdgeResult:
    asset: str
    event_type: str
    horizon_s: int
    status: str                      # OK | INSUFFICIENT_DATA
    in_sample: dict = field(default_factory=dict)
    out_of_sample: dict = field(default_factory=dict)
    confirmed: bool = False
    edge_bps: Optional[float] = None   # OOS cost-adjusted excess, only if confirmed
    reason: str = ""
    n_hypotheses: Optional[int] = None
    bonferroni_t: Optional[float] = None

    def to_dict(self):
        return asdict(self)


def _excess_returns(events, series_provider, benchmark_provider, horizon_s,
                    pre_window_s):
    """Per-event benchmark-excess forward return (real data only)."""
    out = []
    for e in events:
        frm = e.ts - pre_window_s
        to = e.ts + horizon_s + pre_window_s
        s = series_provider(e.asset, frm, to)
        if not s:
            continue
        p0 = price_at_or_before(s, e.ts)
        p1 = price_at_or_after(s, e.ts + horizon_s)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        r = p1 / p0 - 1.0
        if benchmark_provider is not None:
            bs = benchmark_provider(e.ts, e.ts + horizon_s, frm, to)
            if bs is not None:
                r = r - bs
        out.append(r)
    return out


def estimate(events: List[Event], series_provider: SeriesProvider,
             horizons: List[int], cost_bps: float,
             benchmark_provider=None, pre_window_s: int = 7 * 24 * 3600,
             train: float = 0.5, val: float = 0.25,
             min_n: int = 8, t_threshold: float = 1.65, alpha: float = 0.05,
             min_oos: int = 5, seed: int = 1337) -> List[EdgeResult]:
    """Return one EdgeResult per (event_type, asset, horizon)."""
    results: List[EdgeResult] = []
    by_key = {}
    for e in events:
        by_key.setdefault((e.event_type, e.asset), []).append(e)

    pending = []  # (res, in_s, oos_s)
    for (etype, asset), evs in by_key.items():
        evs = sorted(evs, key=lambda x: x.ts)  # chronological
        n = len(evs)
        a = int(n * train); b = int(n * (train + val))
        insample = evs[:b]          # train + validation (earlier in time)
        oos = evs[b:]               # strictly later
        for h in horizons:
            in_r = _excess_returns(insample, series_provider, benchmark_provider, h, pre_window_s)
            oos_r = _excess_returns(oos, series_provider, benchmark_provider, h, pre_window_s)
            in_s = _summary(in_r, cost_bps, seed=seed)
            oos_s = _summary(oos_r, cost_bps, seed=seed + 1)
            res = EdgeResult(asset=asset, event_type=etype, horizon_s=h,
                             status="OK" if in_s["n"] > 0 else "INSUFFICIENT_DATA",
                             in_sample=in_s, out_of_sample=oos_s)
            pending.append((res, in_s, oos_s))

    # multiple-testing correction: Bonferroni over all tested hypotheses
    m = sum(1 for (_, i, _) in pending if i["n"] > 0) or 1
    try:
        z_bonf = NormalDist().inv_cdf(1 - alpha / m)   # one-sided, family-wise
    except Exception:
        z_bonf = 3.0
    bonf_t = max(t_threshold, z_bonf)

    for res, in_s, oos_s in pending:
        if in_s["n"] == 0:
            res.reason = "无真实历史定价数据（样本为空）"
            results.append(res); continue
        g_n = in_s["n"] >= min_n
        g_mean = (in_s["mean"] or 0) > 0
        # significance: strict AND of (Bonferroni t) and (bootstrap CI low > 0);
        # a degenerate zero-variance positive sample counts as significant.
        if in_s["std"] == 0:
            g_sig = (in_s["mean"] or 0) > 0
        else:
            g_t = in_s["t_stat"] is not None and in_s["t_stat"] >= bonf_t
            g_ci = in_s["ci95"] is not None and in_s["ci95"][0] > 0
            g_sig = g_t and g_ci
        g_oos = (oos_s["n"] or 0) >= min_oos and (oos_s["cost_adj_mean"] or 0) > 0
        gates = [("样本量>=min_n", g_n), ("均值>0", g_mean),
                 (f"多重检验显著(t>=%.2f且CI下界>0)" % bonf_t, g_sig),
                 ("样本外成本调整后>0", g_oos)]
        res.n_hypotheses = m
        res.bonferroni_t = bonf_t
        res.confirmed = all(ok for _, ok in gates)
        if res.confirmed:
            res.edge_bps = max(0.0, oos_s["cost_adj_bps"])
            res.reason = "通过全部确认门（多重检验显著 + 样本外成本调整后为正）"
        else:
            failed = [name for name, ok in gates if not ok]
            res.edge_bps = None
            res.reason = "未确认，缺: " + ", ".join(failed)
        results.append(res)
    return results
