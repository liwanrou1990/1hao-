"""样本外（OOS）交易序列的完整指标：§八要求的全部项目 + 费用敏感性（按交易记录重算，不重跑回测）+ 块自助置信区间。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import trade_stats


def equity_from_trades(tr: pd.DataFrame, eq0: float) -> pd.Series:
    t = tr.sort_values("exit_ts")
    return eq0 + t["net_pnl"].cumsum()


def drawdown_from_trades(tr: pd.DataFrame, eq0: float) -> float:
    if tr.empty:
        return 0.0
    eq = pd.concat([pd.Series([eq0]), equity_from_trades(tr, eq0)], ignore_index=True)
    return float((eq / eq.cummax() - 1).min())


def daily_sharpe_sortino(tr: pd.DataFrame, eq0: float) -> tuple[float | None, float | None]:
    if tr.empty:
        return None, None
    t = tr.copy(); t["day"] = pd.to_datetime(t["exit_ts"], utc=True).dt.floor("D")
    span = pd.date_range(t["day"].min(), t["day"].max(), freq="D", tz="UTC")
    daily = t.groupby("day")["net_pnl"].sum().reindex(span, fill_value=0.0) / eq0
    sd = daily.std(ddof=1)
    dn = daily[daily < 0]; dsd = np.sqrt((dn ** 2).mean()) if len(dn) else np.nan
    sh = float(daily.mean() / sd * np.sqrt(365)) if sd and sd > 0 else None
    so = float(daily.mean() / dsd * np.sqrt(365)) if dsd and dsd > 0 else None
    return sh, so


def block_bootstrap_ci(x: np.ndarray, n_boot: int = 2000, block: int = 5, seed: int = 42, stat=np.mean) -> dict:
    x = np.asarray(x, float); n = len(x)
    if n == 0:
        return {"lower": None, "upper": None, "point": None}
    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    n_blocks = int(np.ceil(n / block))
    vals = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        vals[b] = stat(x[idx])
    return {"lower": float(np.percentile(vals, 2.5)), "upper": float(np.percentile(vals, 97.5)), "point": float(stat(x)), "n": int(n), "block": block}


def cost_sensitivity(tr: pd.DataFrame) -> dict:
    """按交易记录重算：手续费 ×k、滑点 ×k、资金费 ×k 下的净盈亏与 ExpR（不重跑回测，止损触发不变 → 属于一阶近似，报告中注明）。"""
    if tr.empty:
        return {}
    out = {}
    fees = tr["fees"].to_numpy(float); slip = tr["slippage_cost"].to_numpy(float) if "slippage_cost" in tr else np.zeros(len(tr)); fund = tr["funding"].to_numpy(float)
    gross = tr["gross_pnl"].to_numpy(float); risk = tr["risk_usd"].to_numpy(float)
    base_net = gross - fees - fund      # 滑点已含在 gross 里（成交价），这里把它显式加回再按倍数扣
    for label, kf, ks, kd in (("base", 1, 1, 1), ("fees_x0.5", 0.5, 1, 1), ("fees_x1.5", 1.5, 1, 1), ("fees_x2", 2, 1, 1), ("slip_x0", 1, 0, 1), ("slip_x2", 1, 2, 1), ("slip_x3", 1, 3, 1), ("funding_x0", 1, 1, 0), ("funding_x3", 1, 1, 3), ("all_x2", 2, 2, 2)):
        net = (gross + slip) - kf * fees - ks * slip - kd * fund
        out[label] = {"net_pnl": float(net.sum()), "expectancy_r": float(np.mean(net / risk)), "win_rate": float((net > 0).mean()), "pf": _pf(net)}
    out["breakeven_fee_multiplier"] = _breakeven_mult(gross + slip - fund, fees + slip)
    return out


def _pf(net: np.ndarray):
    w = net[net > 0].sum(); l = net[net < 0].sum()
    return float(w / abs(l)) if l != 0 else None


def _breakeven_mult(gross_ex_cost: np.ndarray, cost: np.ndarray):
    c = cost.sum()
    return float(gross_ex_cost.sum() / c) if c > 0 else None


def full_oos_report(tr: pd.DataFrame, eq0: float = 10000.0, folds_stats: list[dict] | None = None) -> dict:
    if tr is None or tr.empty:
        return {"n_trades": 0, "verdict": "no_trades"}
    ts = trade_stats(tr)
    r = tr["r_multiple"].to_numpy(float)
    sh, so = daily_sharpe_sortino(tr, eq0)
    out = {"n_trades": ts["n_trades"], "net_pnl": ts["total_net_pnl"], "net_return": ts["total_net_pnl"] / eq0, "expectancy_r": ts["expectancy_r"], "win_rate": ts["win_rate"], "profit_factor": ts["profit_factor"],
           "max_drawdown": drawdown_from_trades(tr, eq0), "sharpe": sh, "sortino": so, "max_consecutive_losses": ts["max_consecutive_losses"], "avg_win": ts["avg_win"], "avg_loss": ts["avg_loss"],
           "by_year": _grp(tr, "year"), "by_direction": _grp(tr, "direction"), "by_regime4": _grp(tr, "regime4"), "by_symbol": _grp(tr, "symbol"), "by_exit_reason": _grp(tr, "exit_reason"),
           "cost_sensitivity": cost_sensitivity(tr), "bootstrap_mean_r_95ci": block_bootstrap_ci(r), "bootstrap_net_return_95ci": block_bootstrap_ci(tr["net_pnl"].to_numpy(float) / eq0, stat=np.sum),
           "total_fees": ts["total_fees"], "total_funding": ts["total_funding"], "total_slippage": float(tr["slippage_cost"].sum()) if "slippage_cost" in tr else None}
    if folds_stats:
        exp = [f.get("expectancy_r") for f in folds_stats if f.get("n_trades", 0) > 0 and f.get("expectancy_r") is not None]
        out["fold_nonneg_share"] = float(np.mean([e >= 0 for e in exp])) if exp else None
        out["n_folds_with_trades"] = len(exp)
    return out


def _grp(tr: pd.DataFrame, col: str) -> dict:
    out = {}
    if col not in tr:
        return out
    for k, g in tr.groupby(col):
        out[str(k)] = {"n": int(len(g)), "net_pnl": float(g["net_pnl"].sum()), "expectancy_r": float(g["r_multiple"].mean()), "win_rate": float((g["net_pnl"] > 0).mean()), "pf": _pf(g["net_pnl"].to_numpy(float))}
    return out
