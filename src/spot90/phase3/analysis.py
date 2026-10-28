"""第三阶段分析。
* 选择期：2020-01-01 ≤ ts < 2025-06-03（折 0-10）；留出期：2025-06-03 ≤ ts ≤ 2026-07-15（折 11-13）。所有模型/特征选择只看选择期。
  （说明：留出期的价格在第一、二阶段报告中已被评估过；第三阶段的新配置、新校准与新规则均未用它做任何决定。）
* 重叠窗口：按日配对差值做块自助（块长 = H + 10，最少 20）；策略权益用“每日投入 1/H 份、持有 H 天”的分批组合，等价于把重叠持仓平均。
* 多重比较：6 个 ML 配置 × 4 周期 × 2 币种 = 48 次 vs E 基准的比较 → Bonferroni 水平 1 − 0.05/48。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ..config import report_dir
from .labels import HORIZONS
from .models import TARGETS
from .runner import SELECTION_END

CONFIGS = ["E_baserate", "E_trendvol", "A_lightgbm", "A_catboost", "B_lightgbm", "B_catboost", "C_lightgbm", "C_catboost"]
ML = [c for c in CONFIGS if not c.startswith("E_")]
N_COMPARISONS = len(ML) * len(HORIZONS) * 2
BONF = 1 - 0.05 / N_COMPARISONS


def _pdir() -> Path:
    return report_dir() / "phase3" / "preds"


def load_horizon(H: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    y = pd.read_parquet(_pdir() / f"labels_H{H}.parquet"); y["ts"] = pd.to_datetime(y["ts"], utc=True)
    preds = {}
    for c in CONFIGS:
        p = _pdir() / f"H{H}_{c}.parquet"
        if p.exists():
            d = pd.read_parquet(p); d["ts"] = pd.to_datetime(d["ts"], utc=True); preds[c] = d
    return y, preds


def block_ci(d: np.ndarray, block: int, levels=(0.95, BONF), n: int = 600, seed: int = 7) -> dict:
    d = np.asarray(d, float); d = d[np.isfinite(d)]
    if len(d) < 2 * block:
        return {"mean": float(d.mean()) if len(d) else None, "n": int(len(d))}
    rng = np.random.default_rng(seed); nb = int(np.ceil(len(d) / block)); st = []
    for _ in range(n):
        starts = rng.integers(0, len(d) - block + 1, size=nb); idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[: len(d)]
        st.append(d[idx].mean())
    out = {"mean": float(d.mean()), "n": int(len(d))}
    for lv in levels:
        al = (1 - lv) / 2
        out[f"ci{int(round(lv*1000))}"] = [float(np.quantile(st, al)), float(np.quantile(st, 1 - al))]
    return out


def ece(p, y, bins=10):
    p = np.asarray(p, float); y = np.asarray(y, float); e = 0.0
    for lo, hi in zip(np.linspace(0, 1, bins + 1)[:-1], np.linspace(0, 1, bins + 1)[1:]):
        k = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if k.any(): e += k.mean() * abs(p[k].mean() - y[k].mean())
    return float(e)


def prob_metrics(p: pd.DataFrame, y: pd.DataFrame, mask: np.ndarray) -> dict:
    out = {"n": int(mask.sum())}
    if mask.sum() < 50:
        return out
    for t in TARGETS:
        yy = y.loc[p.index[mask], t].to_numpy(float)
        for suf in ("", "_cal"):
            pp = np.clip(p.loc[mask, f"p_{t}{suf}"].to_numpy(float), 1e-6, 1 - 1e-6)
            if len(np.unique(yy)) < 2:
                continue
            out[f"{t}{suf}"] = {"auc": float(roc_auc_score(yy, pp)), "brier": float(brier_score_loss(yy, pp)), "logloss": float(log_loss(yy, pp)),
                                "ece": ece(pp, yy), "pred_mean": float(pp.mean()), "obs_rate": float(yy.mean())}
    r = y.loc[p.index[mask], "ret"].to_numpy()
    q05, q95 = p.loc[mask, "ret_q05"].to_numpy(float), p.loc[mask, "ret_q95"].to_numpy(float)
    out["coverage_90"] = float(((r >= q05) & (r <= q95)).mean())
    out["q50_mae"] = float(np.mean(np.abs(r - p.loc[mask, "ret_q50"].to_numpy(float))))
    return out


def tranche_equity(sig: pd.Series, bars_close: pd.Series, H: int) -> dict:
    """sig: 以 ts 为索引的 0/1 信号（决策日收盘）；每个信号日投入 1/H 份资金，从次日起持有 H 天；现金 0 收益。
    返回年化收益、年化波动、Sharpe、最大回撤（组合权益曲线）。"""
    c = bars_close.sort_index(); lr = np.log(c).diff()
    s = sig.reindex(c.index).fillna(0).to_numpy(float)
    active = pd.Series(s, index=c.index).shift(1).fillna(0).rolling(H, min_periods=1).sum() / H   # t 日持仓比例 = 过去 H 个信号的均值
    port = (active * lr).fillna(0)
    eq = np.exp(port.cumsum())
    dd = (eq / eq.cummax() - 1).min()
    n_years = len(port) / 365.25
    ann_ret = float(eq.iloc[-1] ** (1 / n_years) - 1) if n_years > 0 else None
    vol = float(port.std() * np.sqrt(365))
    return {"ann_return": ann_ret, "ann_vol": vol, "sharpe": float(port.mean() / port.std() * np.sqrt(365)) if port.std() > 0 else None,
            "max_drawdown": float(dd), "avg_exposure": float(active.mean())}


def strategy_stats(p: pd.DataFrame, y: pd.DataFrame, mask: np.ndarray, close: dict[str, pd.Series], H: int, thr: float = 0.5, prob_col: str = "p_profit_cal") -> dict:
    """规则：校准后上涨概率 ≥ thr 则买入持有 H 天，否则现金。与同期“每天买入”对比。"""
    sub = p[mask]; yy = y.loc[sub.index]
    buy = (sub[prob_col].to_numpy(float) >= thr)
    r = yy["ret"].to_numpy(); mae = yy["mae"].to_numpy()
    out = {"n_days": int(len(sub)), "n_buy": int(buy.sum()), "share_buy": float(buy.mean()),
           "positions_win_rate": float((r[buy] > 0).mean()) if buy.any() else None, "positions_mean_ret": float(r[buy].mean()) if buy.any() else None,
           "positions_mean_mae": float(mae[buy].mean()) if buy.any() else None, "positions_p_mae15": float((mae[buy] < -0.15).mean()) if buy.any() else None,
           "all_days_mean_ret": float(np.where(buy, r, 0).mean()), "buyhold_mean_ret": float(r.mean()), "buyhold_win_rate": float((r > 0).mean()),
           "diff_vs_buyhold": block_ci(np.where(buy, r, 0) - r, max(H + 10, 20))}
    eq = {}
    for sym in sub["symbol"].unique():
        m = (sub["symbol"] == sym).to_numpy()
        sig = pd.Series(buy[m].astype(float), index=sub.loc[m, "ts"])
        cl = close[sym]; cl = cl[(cl.index >= sig.index.min()) & (cl.index <= sig.index.max() + pd.Timedelta(days=H + 1))]
        eq[sym] = {"strategy": tranche_equity(sig, cl, H), "buyhold": tranche_equity(pd.Series(1.0, index=sig.index), cl, H)}
    out["equity"] = eq
    return out


def analyze(bars: dict) -> dict:
    close = {s: b["1d"].set_index("ts")["close"] for s, b in bars.items()}
    res = {"selection_end": str(SELECTION_END.date()), "n_comparisons": N_COMPARISONS, "bonferroni_level": BONF, "horizons": {}}
    for H in HORIZONS:
        y, preds = load_horizon(H)
        if not preds:
            continue
        hres = {"configs": {}, "paired_vs_E_trendvol": {}, "selection": {}}
        base = preds.get("E_trendvol")
        for name, p in preds.items():
            hres["configs"][name] = {}
            for sym in ("BTCUSDT", "ETHUSDT"):
                for period, pm in (("selection", p["ts"] < SELECTION_END), ("holdout", p["ts"] >= SELECTION_END)):
                    mask = (pm & (p["symbol"] == sym)).to_numpy()
                    entry = prob_metrics(p, y, mask)
                    entry["strategy"] = strategy_stats(p, y, mask, close, H) if mask.sum() > 50 else None
                    hres["configs"][name][f"{sym}|{period}"] = entry
                    if base is not None and name != "E_trendvol":
                        b = base.loc[p.index[mask]]
                        yy = y.loc[p.index[mask], "profit"].to_numpy(float)
                        d_brier = (p.loc[mask, "p_profit_cal"].to_numpy(float) - yy) ** 2 - (b["p_profit_cal"].to_numpy(float) - yy) ** 2
                        yy15 = y.loc[p.index[mask], "mae15"].to_numpy(float)
                        d_b15 = (p.loc[mask, "p_mae15_cal"].to_numpy(float) - yy15) ** 2 - (b["p_mae15_cal"].to_numpy(float) - yy15) ** 2
                        hres["paired_vs_E_trendvol"].setdefault(name, {})[f"{sym}|{period}"] = {"brier_profit": block_ci(d_brier, max(H + 10, 20)), "brier_mae15": block_ci(d_b15, max(H + 10, 20))}
        # 选择：按选择期校准 Brier（profit）在 6 个 ML 配置中选最优，再看留出期
        for sym in ("BTCUSDT", "ETHUSDT"):
            rows = []
            for name in ML:
                if name in hres["configs"] and "profit_cal" in hres["configs"][name].get(f"{sym}|selection", {}):
                    rows.append((name, hres["configs"][name][f"{sym}|selection"]["profit_cal"]["brier"]))
            if not rows:
                continue
            best = min(rows, key=lambda x: x[1])[0]
            sel = hres["paired_vs_E_trendvol"].get(best, {})
            e_sel = hres["configs"]["E_trendvol"].get(f"{sym}|selection", {}).get("profit_cal", {}); e_hold = hres["configs"]["E_trendvol"].get(f"{sym}|holdout", {}).get("profit_cal", {})
            hs = hres["configs"][best].get(f"{sym}|selection", {}).get("profit_cal", {}); hh = hres["configs"][best].get(f"{sym}|holdout", {}).get("profit_cal", {})
            ci_sel = sel.get(f"{sym}|selection", {}).get("brier_profit", {}); ci_hold = sel.get(f"{sym}|holdout", {}).get("brier_profit", {})
            bk = f"ci{int(round(BONF*1000))}"
            validated = bool(ci_sel.get("ci950") and ci_sel["ci950"][1] < 0 and ci_hold.get("mean") is not None and ci_hold["mean"] < 0)
            validated_bonf = bool(ci_sel.get(bk) and ci_sel[bk][1] < 0 and ci_hold.get("mean") is not None and ci_hold["mean"] < 0)
            hres["selection"][sym] = {"best_config": best, "selection_brier": hs.get("brier"), "selection_auc": hs.get("auc"), "E_selection_brier": e_sel.get("brier"), "E_selection_auc": e_sel.get("auc"),
                                      "holdout_brier": hh.get("brier"), "holdout_auc": hh.get("auc"), "E_holdout_brier": e_hold.get("brier"), "E_holdout_auc": e_hold.get("auc"),
                                      "paired_selection": ci_sel, "paired_holdout": ci_hold, "validated_95": validated, "validated_bonferroni": validated_bonf}
        res["horizons"][H] = hres
    return res
