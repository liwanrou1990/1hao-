"""压力测试：成本加倍、滑点三倍、深度缩减、成交失败率上升、价格跳空冲击、波动放大。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..costs.model import CostModel, CostProfile
from .engine import daily_frame, run_daily


@dataclass
class StressScenario:
    name: str
    cost_mult: float = 1.0
    slippage_mult: float = 1.0
    depth_mult: float = 1.0
    fill_prob: float | None = None
    shock_pct: float = 0.0      # 在每日随机时点注入一次跳空（负为下跌）
    vol_mult: float = 1.0


SCENARIOS = [
    StressScenario("基准"),
    StressScenario("成本×2", cost_mult=2.0),
    StressScenario("滑点/冲击×3", slippage_mult=3.0),
    StressScenario("流动性深度÷5", depth_mult=0.2),
    StressScenario("成交失败率20%", fill_prob=0.8),
    StressScenario("日内-3%跳空", shock_pct=-0.03),
    StressScenario("波动×1.5", vol_mult=1.5),
]


def _apply_price_shock(bars: pd.DataFrame, shock_pct: float, vol_mult: float, seed: int = 11) -> pd.DataFrame:
    if shock_pct == 0 and vol_mult == 1.0:
        return bars
    df = bars.copy().reset_index(drop=True)
    rng = np.random.default_rng(seed)
    day = pd.to_datetime(df["ts_ny"]).dt.date
    for d, idx in df.groupby(day).groups.items():
        idx = list(idx)
        base = df.loc[idx, "close"].to_numpy()
        logr = np.diff(np.log(base), prepend=np.log(base[0]))
        logr[0] = 0
        logr = logr * vol_mult
        if shock_pct:
            j = int(rng.integers(30, len(idx) - 30)) if len(idx) > 61 else len(idx) // 2
            logr[j] += np.log(1 + shock_pct)
        new_close = base[0] * np.exp(np.cumsum(logr))
        ratio = new_close / base
        for c in ["open", "high", "low", "close"]:
            df.loc[idx, c] = df.loc[idx, c].to_numpy() * ratio
    return df


def run_stress(bars: pd.DataFrame, strategy, profile: CostProfile, capital_usd: float,
               scenarios: list[StressScenario] | None = None) -> pd.DataFrame:
    rows = []
    for sc in scenarios or SCENARIOS:
        prof = profile.scaled(cost_mult=sc.cost_mult, slippage_mult=sc.slippage_mult, depth_mult=sc.depth_mult,
                              fill_prob_override=sc.fill_prob)
        cm = CostModel(prof, deterministic=sc.fill_prob is None, rng=np.random.default_rng(3))
        b = _apply_price_shock(bars, sc.shock_pct, sc.vol_mult)
        df = daily_frame(run_daily(b, strategy, cm, capital_usd, with_hindsight=False))
        r = df["net_return"]
        rows.append({"scenario": sc.name, "mean_daily_net": float(r.mean()), "total_net": float((1 + r).prod() - 1),
                     "worst_day": float(r.min()), "n_trades": int(df["n_trades"].sum()), "cost_usd": float(df["cost_usd"].sum()),
                     "unverifiable_days": int(df["unverifiable"].sum())})
    return pd.DataFrame(rows)
