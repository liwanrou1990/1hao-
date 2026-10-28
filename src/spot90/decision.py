"""买入决策：A 立即买入 / B 未来 W 天限价等待 / C 持币观望。只用模型输出与当前价格，不用任何未来信息。

EV 比较口径：EV_A = E[ret_A]；EV_B(d) = P(成交)·E[ret_B|成交]（未成交 = 0 收益、持币）。
等待的机会成本 = (1−P(成交))·max(E[ret_A], 0)。门槛（可配置）：P(盈利) ≥ 65%、收益 ≥ 8%、P(回撤>15%) ≤ 35%、B 另需 P(成交) ≥ 30%。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import cfg


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def evaluate_candidates(close: float, predA: dict, predB: pd.DataFrame, thresholds: dict | None = None) -> dict:
    c = cfg()
    th = thresholds or c["thresholds"]
    rm = th.get("return_metric", "median")
    min_fill = float(c["limit_order"]["min_fill_prob"])
    ev_a = _f(predA.get("ret_mean"))
    cands = []

    def _cand(kind, d, price, p_fill, p_profit, ret_med, ret_mean, p_dd):
        ret_metric = ret_med if rm == "median" else ret_mean
        reasons = []
        if p_profit is None or ret_metric is None or p_dd is None:
            reasons.append("模型无输出")
        else:
            if p_profit < th["min_profit_prob"]:
                reasons.append(f"盈利概率 {p_profit:.0%} < {th['min_profit_prob']:.0%}")
            if ret_metric < th["min_expected_net_return"]:
                reasons.append(f"预期净收益 {ret_metric:.1%} < {th['min_expected_net_return']:.0%}")
            if p_dd > th["max_prob_drawdown_gt_15"]:
                reasons.append(f"回撤>{th['drawdown_threshold']:.0%} 概率 {p_dd:.0%} > {th['max_prob_drawdown_gt_15']:.0%}")
        if kind == "B" and (p_fill is None or p_fill < min_fill):
            reasons.append(f"14 天成交概率 {0 if p_fill is None else p_fill:.0%} < {min_fill:.0%}")
        ev = None if ret_mean is None or p_fill is None else p_fill * ret_mean
        opp = None if (kind == "A" or p_fill is None or ev_a is None) else (1 - p_fill) * max(ev_a, 0.0)
        return {"kind": kind, "d_pct": d, "price": price, "p_fill": p_fill, "p_profit": p_profit, "ret_median": ret_med, "ret_mean": ret_mean,
                "p_dd": p_dd, "ev": ev, "opportunity_cost": opp, "passes": not reasons, "fail_reasons": reasons}

    cands.append(_cand("A", 0.0, close, 1.0, _f(predA.get("p_profit")), _f(predA.get("ret_q50")), ev_a, _f(predA.get("p_dd"))))
    for _, r in predB.sort_values("d_pct").iterrows():
        d = float(r["d_pct"])
        if d <= 0:
            continue
        cands.append(_cand("B", d, close * (1 - d / 100), _f(r.get("p_fill")), _f(r.get("p_profit")), _f(r.get("ret_q50")), _f(r.get("ret_mean")), _f(r.get("p_dd"))))
    passing = [x for x in cands if x["passes"] and x["ev"] is not None]
    chosen = max(passing, key=lambda x: (x["ev"], x["p_fill"])) if passing else None
    a = cands[0]
    best_b = max([x for x in passing if x["kind"] == "B"], key=lambda x: x["ev"], default=None)
    worth_waiting = bool(best_b and (a["ev"] is None or best_b["ev"] > (a["ev"] or -9) + 0.005))
    if chosen is None:
        action = "暂不买入"
    elif chosen["kind"] == "A":
        action = "买入"
    else:
        action = "等待"
    buy_prices = [x["price"] for x in passing]
    wait_prices = [x["price"] for x in passing if x["kind"] == "B" and (a["ev"] is None or x["ev"] >= (a["ev"] or -9))]
    out = {
        "action": action, "chosen": chosen, "worth_waiting": worth_waiting,
        "buy_range": [min(buy_prices), max(buy_prices)] if buy_prices else None,
        "wait_range": [min(wait_prices), max(wait_prices)] if wait_prices else None,
        "ev_buy_now": a["ev"], "ev_best_wait": best_b["ev"] if best_b else None,
        "candidates": cands,
        "price_band_90d": {k: (None if _f(predA.get(f"ret_{k}")) is None else close * (1 + _f(predA.get(f"ret_{k}")))) for k in ("q05", "q25", "q50", "q75", "q95")},
        "thresholds": {k: th[k] for k in ("min_profit_prob", "min_expected_net_return", "max_prob_drawdown_gt_15", "drawdown_threshold")} | {"min_fill_prob": min_fill, "return_metric": rm, "drawdown_metric": th.get("drawdown_metric", "mae")},
    }
    return out


def decide_batch(closes: np.ndarray, predA: pd.DataFrame, predB: pd.DataFrame, keys: pd.DataFrame) -> pd.DataFrame:
    """回测用：对每个 (symbol, ts) 给出 action / chosen d / chosen price。predB 必须含 symbol, ts, d_pct。"""
    rows = []
    gb = {k: g for k, g in predB.groupby(["symbol", "ts"], sort=False)}
    for i, (idx, k) in enumerate(keys.iterrows()):
        pb = gb.get((k["symbol"], k["ts"]))
        if pb is None:
            rows.append({"action": "暂不买入", "d_pct": np.nan, "ev": np.nan}); continue
        res = evaluate_candidates(float(closes[i]), predA.loc[idx].to_dict(), pb)
        ch = res["chosen"]
        rows.append({"action": res["action"], "d_pct": ch["d_pct"] if ch else np.nan, "ev": ch["ev"] if ch else np.nan,
                     "ev_buy_now": res["ev_buy_now"], "worth_waiting": res["worth_waiting"]})
    return pd.DataFrame(rows, index=keys.index)
