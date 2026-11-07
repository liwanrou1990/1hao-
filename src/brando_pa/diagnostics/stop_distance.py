"""策略 A 止损距离诊断（第三阶段 §三）。

输入：回测 signals_log（含 diag_* 列）与 trades；输出：
  * 止损距离/ATR 分布（全部候选 / 通过过滤 / 因 stop_distance 被拒），按执行周期、标的、方向、区域周期分组
  * 分解：止损距离 = 区域高度（从确认侧边界到另一侧边界）+ 回踩深度（回踩穿过确认侧边界的深度，可为负）+ 确认偏移（确认收盘到边界）+ ATR 缓冲
  * 结构止损是否落在“区域另一侧”（structure_is_zone_far_side）的比例
  * 成交后 MAE/止损距离 分布：若多数亏损交易 MAE 接近 1（被扫损）且赢家 MFE 远大于 1，说明止损宽度与波动不匹配
  * 诊断图（PNG，matplotlib）
结论判据写在 verdict()：区域高度占比 > 50% → “关键区域过宽/止损挂在区域另一侧”；确认偏移占比 > 30% → “确认滞后”；回踩深度占比 > 30% → “入场过迟”。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def decompose(sl: pd.DataFrame, sl_buffer_atr: float) -> pd.DataFrame:
    d = sl.copy()
    for col in ("diag_zone_height_atr", "diag_retest_depth_atr", "diag_confirm_to_zone_edge_atr"):
        if col not in d:
            d[col] = np.nan
    d["buffer_atr"] = sl_buffer_atr
    # 结构点在区域另一侧时：距离 = 区域高度 + 确认偏移 + 缓冲（回踩深度被区域边界吸收）；否则 = 回踩深度 + 确认偏移 + 缓冲
    far = d.get("diag_structure_is_zone_far_side", pd.Series(False, index=d.index)).fillna(False).astype(bool)
    d["part_zone_height"] = np.where(far, d["diag_zone_height_atr"], 0.0)
    d["part_retest_depth"] = np.where(far, 0.0, d["diag_retest_depth_atr"].clip(lower=0))
    d["part_confirm_offset"] = d["diag_confirm_to_zone_edge_atr"].clip(lower=0)
    d["part_buffer"] = d["buffer_atr"]
    d["recon"] = d[["part_zone_height", "part_retest_depth", "part_confirm_offset", "part_buffer"]].sum(axis=1)
    return d


def summarize(sl: pd.DataFrame, trades: pd.DataFrame, sl_buffer_atr: float, exec_tf: str) -> dict:
    d = decompose(sl, sl_buffer_atr)
    out = {"exec_tf": exec_tf, "n_candidates": int(len(d))}
    def q(x: pd.Series) -> dict:
        x = x.dropna()
        return {"n": int(len(x)), "p10": float(x.quantile(.1)), "p25": float(x.quantile(.25)), "median": float(x.median()), "p75": float(x.quantile(.75)), "p90": float(x.quantile(.9)), "mean": float(x.mean())} if len(x) else {"n": 0}
    out["stop_atr_all"] = q(d["stop_distance_atr"])
    out["stop_atr_accepted"] = q(d.loc[d["accepted"] == True, "stop_distance_atr"])  # noqa: E712
    out["stop_atr_rejected_stop_distance"] = q(d.loc[d["first_fail"] == "stop_distance", "stop_distance_atr"])
    out["stop_pct_all"] = q(d["stop_distance_pct"])
    out["share_structure_on_zone_far_side"] = float(d.get("diag_structure_is_zone_far_side", pd.Series(dtype=float)).fillna(False).astype(bool).mean()) if len(d) else None
    parts = d[["part_zone_height", "part_retest_depth", "part_confirm_offset", "part_buffer"]].mean()
    tot = float(parts.sum()) or 1.0
    out["decomposition_mean_atr"] = {k: float(v) for k, v in parts.items()} | {"reconstructed_total": tot, "actual_mean": float(d["stop_distance_atr"].mean())}
    out["decomposition_share"] = {k: float(v / tot) for k, v in parts.items()}
    out["by_zone_tfs"] = {str(k): q(g["stop_distance_atr"]) | {"zone_height_atr_median": float(g["diag_zone_height_atr"].median())} for k, g in d.groupby("zone_tfs")}
    out["by_symbol_direction"] = {f"{k[0]}/{k[1]}": q(g["stop_distance_atr"]) for k, g in d.groupby(["symbol", "direction"])}
    out["first_fail_counts"] = d["first_fail"].fillna("accepted").value_counts().to_dict()
    out["timing"] = {"bars_armed_to_retest_median": float(d["diag_bars_armed_to_retest"].median()) if "diag_bars_armed_to_retest" in d else None,
                     "bars_retest_to_confirm_median": float(d["diag_bars_retest_to_confirm"].median()) if "diag_bars_retest_to_confirm" in d else None}
    if trades is not None and not trades.empty:
        t = trades.copy(); risk = (t["entry_price"] - t["stop_init"]).abs()
        t["mae_r"] = t["mae"] / risk; t["mfe_r"] = t["mfe"] / risk
        win = t[t["net_pnl"] > 0]; los = t[t["net_pnl"] <= 0]
        out["trades"] = {"n": int(len(t)), "stop_pct_median": float((risk / t["entry_price"]).median()), "winners_mfe_r_median": float(win["mfe_r"].median()) if len(win) else None,
                         "losers_mae_r_median": float(los["mae_r"].median()) if len(los) else None, "losers_stopped_share": float(los["exit_reason"].isin(["stop", "stop_gap", "stop_same_bar"]).mean()) if len(los) else None,
                         "mae_r_quantiles_all": q(t["mae_r"]), "share_mae_lt_0_5R_then_win": float(((t["mae_r"] < 0.5) & (t["net_pnl"] > 0)).mean()),
                         "share_losers_mae_between_0_5_and_1": float(((los["mae_r"] >= 0.5) & (los["mae_r"] < 1.0)).mean()) if len(los) else None}
    out["verdict"] = verdict(out)
    return out


def verdict(summ: dict) -> list[str]:
    sh = summ.get("decomposition_share", {}); v = []
    if sh.get("part_zone_height", 0) > 0.5:
        v.append(f"主因：结构止损挂在 1W/1D 区域的另一侧，区域高度占止损距离 {sh['part_zone_height']:.0%}（区域半宽上限 1% 价格，在执行周期 ATR 单位下即数倍 ATR）→ 属于“关键区域对执行周期过宽 + 止损结构选择”问题，不是确认条件滞后")
    if sh.get("part_confirm_offset", 0) > 0.3:
        v.append(f"确认滞后显著：确认收盘距区域边界占 {sh['part_confirm_offset']:.0%}")
    if sh.get("part_retest_depth", 0) > 0.3:
        v.append(f"入场过迟/回踩过深：回踩深度占 {sh['part_retest_depth']:.0%}")
    tr = summ.get("trades", {})
    if tr and tr.get("losers_mae_r_median") is not None:
        v.append(f"成交后：亏损交易 MAE/止损中位 {tr['losers_mae_r_median']:.2f}R、被扫损占 {tr['losers_stopped_share']:.0%}；赢家 MFE/止损中位 {tr['winners_mfe_r_median']:.2f}R → 止损宽度与实际波动的关系：{'亏损多为真正走反（止损并非被噪声扫掉）' if tr['losers_mae_r_median'] > 0.8 else '相当比例亏损在 0.5~1R 间被扫，止损偏窄'}")
    return v


def charts(sl: pd.DataFrame, trades: pd.DataFrame, sl_buffer_atr: float, out_dir: Path, tag: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"]
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    d = decompose(sl, sl_buffer_atr); paths = []
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    ax[0].hist(d["stop_distance_atr"].dropna().clip(upper=12), bins=48, color="#2f6fdb", alpha=.8); ax[0].axvline(4.0, color="#b22222", ls="--", label="stop_distance_max 4 ATR"); ax[0].set_title(f"Strategy A stop distance / ATR ({tag})"); ax[0].legend()
    parts = d[["part_zone_height", "part_retest_depth", "part_confirm_offset", "part_buffer"]].mean()
    ax[1].bar(["zone height", "retest depth", "confirm offset", "ATR buffer"], parts.values, color=["#b22222", "#c9a227", "#2f6fdb", "#888"]); ax[1].set_title("mean decomposition (ATR)")
    if trades is not None and not trades.empty:
        risk = (trades["entry_price"] - trades["stop_init"]).abs()
        ax[2].scatter(trades["mae"] / risk, trades["mfe"] / risk, c=np.where(trades["net_pnl"] > 0, "#2e8b57", "#b22222"), s=12, alpha=.7); ax[2].axvline(1, color="#888", ls=":"); ax[2].axhline(1, color="#888", ls=":")
        ax[2].set_xlabel("MAE / stop"); ax[2].set_ylabel("MFE / stop"); ax[2].set_title("filled trades: MAE vs MFE (R)"); ax[2].set_xlim(0, 1.6); ax[2].set_ylim(0, 6)
    fig.tight_layout(); p = out_dir / f"stop_distance_{tag}.png"; fig.savefig(p, dpi=110); plt.close(fig); paths.append(str(p))
    return paths


def run(sl_path: Path, trades_path: Path, exec_tf: str, out_dir: Path, sl_buffer_atr: float = 0.5, tag: str | None = None) -> dict:
    sl = pd.read_csv(sl_path); tr = pd.read_csv(trades_path) if Path(trades_path).exists() else pd.DataFrame()
    tag = tag or exec_tf
    summ = summarize(sl, tr, sl_buffer_atr, exec_tf)
    summ["charts"] = charts(sl, tr, sl_buffer_atr, out_dir, tag)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"stop_distance_{tag}.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2, default=str))
    return summ
