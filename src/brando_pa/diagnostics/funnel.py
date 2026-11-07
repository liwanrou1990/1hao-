"""候选信号漏斗（第二阶段 §三）。

阶段定义（顺序即过滤顺序）：
  setup        策略内部：设置建立（A/B）或区域触及（C）             ← strategy_counters
  candidate    策略给出候选信号（价格行为条件全部满足）               ← signals_log 行
  trend        趋势过滤：mtf_4h_trend / mtf_1d_regime / regime_policy / regime_trend
  price        价格确认质量：volume / volatility / stop_distance
  cost         成本检查：cost_to_risk
  rr           盈亏比检查：net_rr
  risk         风险检查：sizing（仓位可行）+ portfolio（组合限制）
  order        有效订单（进入待执行）
  fill         实际成交（市价：下一根开盘；限价：穿越挂单价），含成交时取消
统计维度：symbol × direction × strategy（× exec_tf 由调用方跨运行合并）。
同时输出：候选入场价与结构止损价的平均距离（% 与 ATR 倍数）、成本占预计收益比例、有效信号平均净盈亏比、限价未成交数量与原因、
并给出 cost_to_risk 语义审计（公式、样例、与“保本止损”无关的说明）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STAGE_OF = {"direction_policy": "trend", "mtf_4h_trend": "trend", "mtf_1d_regime": "trend", "mtf_zone_htf": "trend", "regime_policy": "trend", "regime_trend": "trend", "regime_highvol": "trend",
            "volume": "price", "volatility": "price", "stop_distance": "price", "cost_to_risk": "cost", "net_rr": "rr", "funding": "trend", "onchain_netflow": "trend", "portfolio": "risk"}
STAGES = ["setup", "candidate", "trend", "price", "cost", "rr", "risk", "order", "fill"]


def _first_stage_fail(row) -> str | None:
    fails = [f for f in str(row.get("fails", "")).split(",") if f]
    if not fails:
        if row.get("sizing_ok") is False:
            return "risk"
        return None
    return STAGE_OF.get(fails[0], "price")


def funnel_table(sig: pd.DataFrame, trades: pd.DataFrame, cancelled: list[dict], limit_log: list[dict], counters: dict, group_cols=("symbol", "direction", "strategy")) -> pd.DataFrame:
    if sig is None or sig.empty:
        return pd.DataFrame()
    s = sig.copy()
    s["fail_stage"] = s.apply(_first_stage_fail, axis=1)
    s["order_created"] = s.get("order_created", pd.Series(False, index=s.index)).fillna(False).astype(bool)
    rows = []
    canc = pd.DataFrame(cancelled) if cancelled else pd.DataFrame(columns=["symbol", "strategy", "reason"])
    for keys, g in s.groupby(list(group_cols)):
        keys = keys if isinstance(keys, tuple) else (keys,)
        d = dict(zip(group_cols, keys))
        n = len(g)
        passed = {}
        remaining = n
        for st in ("trend", "price", "cost", "rr", "risk"):
            rej = int((g["fail_stage"] == st).sum())
            passed[st] = remaining - rej
            remaining -= rej
        orders = int(g["order_created"].sum())
        tr = trades
        if trades is not None and not trades.empty:
            m = np.ones(len(trades), bool)
            for k, v in d.items():
                if k in trades:
                    m &= (trades[k] == v).to_numpy()
            tr = trades[m]
        fills = int(len(tr)) if tr is not None else 0
        cm = np.ones(len(canc), bool)
        for k, v in d.items():
            if k in canc:
                cm &= (canc[k] == v).to_numpy()
        n_canc = int(cm.sum())
        ok = g[g["accepted"] == True]  # noqa: E712
        setup_n = None
        if counters:
            sym = d.get("symbol"); strat = d.get("strategy")
            cnt = counters.get(sym, {}).get(strat, {}) if sym else {}
            setup_n = cnt.get("setup_armed", cnt.get("zone_touched"))
        reasons = pd.Series([f for fs in g["fails"] for f in str(fs).split(",") if f]).value_counts().head(6).to_dict()
        rows.append(d | {"setup": setup_n, "candidate": n, "after_trend": passed["trend"], "after_price": passed["price"], "after_cost": passed["cost"], "after_rr": passed["rr"], "after_risk": passed["risk"],
                         "order": orders, "fill": fills, "cancelled_at_fill": n_canc,
                         "avg_stop_distance_pct": float(g["stop_distance_pct"].mean()) if "stop_distance_pct" in g else None,
                         "avg_stop_distance_atr": float(g["stop_distance_atr"].mean()) if "stop_distance_atr" in g else None,
                         "median_cost_to_risk": float(g["cost_to_risk"].median()) if "cost_to_risk" in g else None,
                         "median_cost_share_of_reward": float(g["cost_share_of_reward"].median()) if "cost_share_of_reward" in g else None,
                         "avg_rr_gross_all": float(g["rr_gross"].mean()), "avg_net_rr_valid": float(ok["net_rr"].mean()) if len(ok) else None,
                         "avg_stop_distance_pct_valid": float(ok["stop_distance_pct"].mean()) if len(ok) and "stop_distance_pct" in ok else None,
                         "top_reject_reasons": reasons})
    df = pd.DataFrame(rows)
    if limit_log:
        ll = pd.DataFrame(limit_log)
        agg = ll.groupby(["symbol", "strategy", "direction"])["status"].value_counts().unstack(fill_value=0).reset_index()
        df = df.merge(agg, on=[c for c in ("symbol", "strategy", "direction") if c in df.columns and c in agg.columns], how="left")
    return df


def cost_to_risk_audit(c: dict, example: dict | None = None) -> dict:
    """审计“往返成本 / 止损风险 ≤ 25%”的语义：分母是初始结构止损距离（信号收盘价 − 止损价），与后续保本/移动止损无关；
    分子是预计往返成本（入场 taker 费+滑点、按止损价出场的 taker 费+滑点+止损额外滑点、按最长持仓时间估算的资金费）。"""
    from ..risk.costs import estimate_round_trip_cost
    ex = example or {"entry": 100000.0, "stop": 99500.0, "instrument": "perp", "direction": "long", "hold_bars": 96, "bar_seconds": 900}
    cost = estimate_round_trip_cost(ex["entry"], ex["stop"], c["costs"], ex["instrument"], ex["direction"], ex["hold_bars"], ex["bar_seconds"])
    dist = abs(ex["entry"] - ex["stop"])
    return {"formula": "cost_to_risk = (entry×(fee+slip) + stop×(fee+slip+stop_slip) + funding_est) / |entry − stop_initial|",
            "denominator": "初始结构止损距离（含 sl_buffer_atr 缓冲），不是保本止损/移动止损后的距离；移动止损发生在持仓期间，不参与开仓过滤",
            "example": ex | {"stop_distance": dist, "stop_distance_pct": dist / ex["entry"], "cost_per_unit": cost["cost_per_unit"], "cost_bps": cost["cost_bps"], "ratio": cost["cost_per_unit"] / dist,
                             "components": {"entry_cost": cost["entry_cost"], "exit_cost_at_stop": cost["exit_cost_stop"], "funding_est": cost["funding_est"]}},
            "threshold": c["filters"]["cost"]["max_cost_to_risk"],
            "semantic_check": "分母使用 Signal.risk_per_unit = |ref_price − stop_price|，在 FilterPipeline.apply 时 stop_price 为策略给出的初始止损，trail/breakeven 只在 PositionManager.manage 中修改持仓对象，不回写信号；审计结论：无混淆。",
            "caveat": "资金费按 max_hold_bars 全程估算属于保守上界（多数交易更早退出）；止损额外滑点 10 bps 为假设。"}


def funnel_markdown(df: pd.DataFrame, title: str) -> str:
    if df is None or df.empty:
        return f"### {title}\n\n无候选。\n"
    cols = ["symbol", "direction", "strategy", "setup", "candidate", "after_trend", "after_price", "after_cost", "after_rr", "after_risk", "order", "fill", "cancelled_at_fill", "avg_stop_distance_pct", "avg_stop_distance_atr", "median_cost_to_risk", "median_cost_share_of_reward", "avg_net_rr_valid"]
    cols = [c for c in cols if c in df.columns]
    L = [f"### {title}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                vals.append(f"{v:.2%}" if "pct" in c or "share" in c or "cost_to_risk" in c else f"{v:.2f}")
            else:
                vals.append("—" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
        L.append("| " + " | ".join(vals) + " |")
    L.append("")
    for _, r in df.iterrows():
        L.append(f"- {r['symbol']} {r['direction']} {r['strategy']} 主要拒绝原因：{r['top_reject_reasons']}")
    return "\n".join(L) + "\n"
