"""数据质量与回测真实性审计（第二阶段 §二）。

检查项（全部写入 JSON + Markdown，不修改数据）：
 1. 每个 symbol×周期：重复时间戳、周期边界错位、缺口（缺几根、何时）、OHLC 非法、极端异常（|log 收益| > 8σ 或单根振幅 > 25%）、零成交量根、最新收盘时间与现在的差
 2. 跨周期一致性：用 1h 合成 4h / 用 4h 合成 1d / 用 1d 合成 1w，与下载的高周期逐根比较 OHLC 相对误差（应 ≈ 0；否则说明周期边界或收盘时间错误）
 3. 现货 vs 永续：本仓只有 Binance 现货 K 线；用可得的永续 mark price 样本（8 小时点）对比同一时刻现货收盘，给出基差分布；标注永续 K 线/资金费率的历史覆盖缺口
 4. 资金费率：可得区间、均值、分位；与回测所用保守假设（0.01%/8h 多头付）对比；缺失区间明确标记“不可得，不得视为零”
 5. 成本假设清单：手续费、滑点、止损滑点、价差（现货/合约）、限价未成交处理
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, own_data_dir, report_dir
from ..data.store import BarStore
from ..schema import resample_bars, tf_seconds

TFS = ["1w", "1d", "4h", "1h", "30m", "15m"]


def audit_bars(df: pd.DataFrame, tf: str, now: pd.Timestamp) -> dict:
    if df is None or df.empty:
        return {"n_bars": 0, "status": "missing"}
    sec = tf_seconds(tf)
    ts = df["ts"]
    ep = (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds().round().astype("int64")
    misaligned = int((((ep - 4 * 86400) % sec) != 0).sum()) if tf == "1w" else int(((ep % sec) != 0).sum())
    dup = int(ts.duplicated().sum())
    diffs = ts.diff().dt.total_seconds().dropna()
    gap_rows = diffs[diffs > sec * 1.5]
    gaps = [{"after": ts.loc[:i].iloc[-2].isoformat(), "before": ts.loc[i].isoformat(), "missing_bars": int(round(diffs.loc[i] / sec)) - 1} for i in gap_rows.index]
    bad_ohlc = int((~((df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) & (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9) & (df["low"] > 0))).sum())
    r = np.log(df["close"]).diff(); sd = r.std()
    outliers = df.loc[(r.abs() > 8 * sd)] if sd and np.isfinite(sd) else df.iloc[0:0]
    rng = (df["high"] - df["low"]) / df["close"]
    huge = df.loc[rng > 0.25]
    zero_vol = int((df["volume"] <= 0).sum())
    last_close = ts.iloc[-1] + pd.Timedelta(seconds=sec)
    return {"n_bars": int(len(df)), "first": ts.iloc[0].isoformat(), "last_open": ts.iloc[-1].isoformat(), "last_close": last_close.isoformat(), "age_hours": round((now - last_close).total_seconds() / 3600, 1),
            "duplicates": dup, "misaligned": misaligned, "n_gaps": len(gaps), "missing_bars_total": int(sum(g["missing_bars"] for g in gaps)), "gaps": gaps[:40], "bad_ohlc": bad_ohlc,
            "return_outliers_8sigma": [{"ts": t.isoformat(), "ret": round(float(x), 4)} for t, x in zip(outliers["ts"], r.loc[outliers.index])][:20],
            "range_gt_25pct": [{"ts": t.isoformat(), "range_pct": round(float(x), 4)} for t, x in zip(huge["ts"], rng.loc[huge.index])][:20],
            "n_return_outliers_8sigma": int(len(outliers)), "n_range_gt_25pct": int(len(huge)), "zero_volume_bars": zero_vol, "sources": df["source"].value_counts().to_dict(), "status": "ok" if (dup == 0 and misaligned == 0 and bad_ohlc == 0) else "issues"}


def cross_tf_consistency(bars: dict[str, pd.DataFrame]) -> list[dict]:
    out = []
    for src, dst in (("1h", "4h"), ("4h", "1d"), ("1d", "1w"), ("15m", "1h"), ("30m", "1h")):
        if src not in bars or dst not in bars or bars[src].empty or bars[dst].empty:
            continue
        rs = resample_bars(bars[src], src, dst)
        m = rs.merge(bars[dst], on="ts", suffixes=("_rs", "_dl"))
        if m.empty:
            out.append({"pair": f"{src}->{dst}", "overlap": 0}); continue
        err = {k: float(((m[f"{k}_rs"] - m[f"{k}_dl"]).abs() / m[f"{k}_dl"]).max()) for k in ("open", "high", "low", "close")}
        verr = float(((m["volume_rs"] - m["volume_dl"]).abs() / m["volume_dl"].replace(0, np.nan)).median())
        out.append({"pair": f"{src}->{dst}", "overlap": int(len(m)), "max_rel_err": {k: round(v, 6) for k, v in err.items()}, "median_volume_rel_err": round(verr, 6),
                    "verdict": "一致" if max(err.values()) < 1e-6 else ("基本一致" if max(err.values()) < 1e-3 else "不一致（周期边界/收盘时间可疑）")})
    return out


def perp_vs_spot(symbol: str, bars_1h: pd.DataFrame) -> dict:
    p = own_data_dir() / "derivatives" / f"{symbol}_funding_oi_powakadata.csv"
    if not p.exists():
        return {"available": False, "reason": "无永续样本文件"}
    d = pd.read_csv(p); d["ts"] = pd.to_datetime(d["datetime_utc"], utc=True)
    d = d.sort_values("ts")
    gaps = d["ts"].diff().dt.total_seconds().div(3600)
    segs = []
    start = d["ts"].iloc[0]
    for i in range(1, len(d)):
        if gaps.iloc[i] > 8.5:
            segs.append((start, d["ts"].iloc[i - 1])); start = d["ts"].iloc[i]
    segs.append((start, d["ts"].iloc[-1]))
    fr = d["funding_rate"].dropna()
    out = {"available": True, "source": "powakadata/powakadata-crypto-funding-sample（GitHub 免费样本，Binance USDT 永续）", "rows": int(len(d)),
           "covered_segments": [{"from": a.isoformat(), "to": b.isoformat(), "days": round((b - a).days + 0.33, 1)} for a, b in segs],
           "funding_8h": {"mean": float(fr.mean()), "median": float(fr.median()), "p05": float(fr.quantile(0.05)), "p95": float(fr.quantile(0.95)), "min": float(fr.min()), "max": float(fr.max()),
                          "share_positive": float((fr > 0).mean()), "mean_annualized": float(fr.mean() * 3 * 365)},
           "funding_by_year_mean_8h": {int(k): float(v) for k, v in d.groupby(d["ts"].dt.year)["funding_rate"].mean().items()},
           "assumption_in_backtest_8h": float(cfg()["costs"]["funding"]["assumed_rate_8h"])}
    # 基差：mark price（8h 点）vs 同一时刻现货 1h 收盘（该 8h 点所在 1h K 线的上一根收盘）
    mk = d.dropna(subset=["mark_price"])[["ts", "mark_price"]]
    if not mk.empty and bars_1h is not None and not bars_1h.empty:
        sp = bars_1h[["ts", "close"]].copy(); sp["ts"] = sp["ts"] + pd.Timedelta(hours=1)    # 收盘时刻
        m = mk.merge(sp, on="ts", how="inner")
        if not m.empty:
            basis = (m["mark_price"] / m["close"] - 1)
            out["basis_mark_vs_spot"] = {"n": int(len(m)), "from": m["ts"].min().isoformat(), "to": m["ts"].max().isoformat(), "mean_bps": float(basis.mean() * 1e4), "median_bps": float(basis.median() * 1e4),
                                         "p05_bps": float(basis.quantile(0.05) * 1e4), "p95_bps": float(basis.quantile(0.95) * 1e4), "abs_max_bps": float(basis.abs().max() * 1e4)}
    oi = d.dropna(subset=["open_interest"])
    out["open_interest_rows"] = int(len(oi))
    out["gap_statement"] = "永续历史 K 线不可得；资金费率仅上述片段可得（样本集为截断样本），其余区间按配置的保守假设计入，绝不按零成本处理。"
    return out


def run_audit(symbols: list[str] | None = None, now: pd.Timestamp | None = None) -> dict:
    c = cfg(); symbols = symbols or c["symbols"]; now = now or pd.Timestamp.now(tz="UTC"); st = BarStore()
    rep = {"generated_at": datetime.now(timezone.utc).isoformat(), "symbols": {}, "cost_assumptions": {
        "spot": c["costs"]["spot"], "perp": c["costs"]["perp"], "stop_slippage_bps": c["costs"]["stop_slippage_bps"], "funding": c["costs"]["funding"],
        "entry_fill": c["backtest"]["entry_fill"], "same_bar_rule": c["backtest"]["same_bar_rule"], "gap_fill_at_open": c["costs"]["gap_fill_at_open"],
        "notes": ["现货 K 线来自 Binance 现货（公开缓存），合约方向用同一价格序列回测；永续与现货的基差在下方以可得样本量化",
                  "滑点 5 bps/边 为固定假设，不含盘口价差随波动扩大的影响；止损额外 10 bps",
                  "限价单（第二阶段入场模式 2）只有在后续 K 线价格穿越挂单价 ≥ 穿透阈值时才算成交，否则记为未成交"]}}
    for s in symbols:
        bars = {tf: st.load(s, tf) for tf in TFS}
        rep["symbols"][s] = {"bars": {tf: audit_bars(df, tf, now) for tf, df in bars.items()}, "cross_timeframe": cross_tf_consistency(bars), "perp_vs_spot": perp_vs_spot(s, bars.get("1h"))}
    out_dir = report_dir() / "audit"; out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_dir / "data_quality.json", rep, indent=2, default=str)
    atomic_write_text(out_dir / "data_quality.md", to_markdown(rep))
    rep["paths"] = {"json": str(out_dir / "data_quality.json"), "md": str(out_dir / "data_quality.md")}
    return rep


def to_markdown(rep: dict) -> str:
    L = [f"# 数据质量与回测真实性审计（{rep['generated_at'][:16]} UTC）", ""]
    for s, v in rep["symbols"].items():
        L += [f"## {s}", "", "| 周期 | 根数 | 起 | 最新收盘 | 落后(h) | 重复 | 错位 | 缺口数/缺根 | 非法OHLC | 8σ异常 | 振幅>25% | 零量 | 状态 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for tf, b in v["bars"].items():
            if b.get("n_bars", 0) == 0:
                L.append(f"| {tf} | 0 | — | — | — | — | — | — | — | — | — | — | 缺失 |"); continue
            L.append(f"| {tf} | {b['n_bars']} | {b['first'][:10]} | {b['last_close'][:16]} | {b['age_hours']} | {b['duplicates']} | {b['misaligned']} | {b['n_gaps']}/{b['missing_bars_total']} | {b['bad_ohlc']} | {b['n_return_outliers_8sigma']} | {b['n_range_gt_25pct']} | {b['zero_volume_bars']} | {b['status']} |")
        L += ["", "跨周期一致性（低周期合成 vs 下载的高周期）：", ""]
        for x in v["cross_timeframe"]:
            L.append(f"- {x['pair']}：重叠 {x.get('overlap')} 根，OHLC 最大相对误差 {x.get('max_rel_err')}，成交量中位相对误差 {x.get('median_volume_rel_err')} → {x.get('verdict', '')}")
        pv = v["perp_vs_spot"]
        L += ["", "现货 vs 永续 / 资金费率：", ""]
        if pv.get("available"):
            L.append(f"- 来源：{pv['source']}；可得片段：" + "；".join(f"{g['from'][:10]} → {g['to'][:10]}（{g['days']} 天）" for g in pv["covered_segments"]))
            f8 = pv["funding_8h"]
            L.append(f"- 资金费率（8h）：均值 {f8['mean']:.6f}（年化 {f8['mean_annualized']:.2%}），中位 {f8['median']:.6f}，P5/P95 {f8['p05']:.6f}/{f8['p95']:.6f}，正值占比 {f8['share_positive']:.0%}；回测假设 {pv['assumption_in_backtest_8h']:.6f}/8h（多头付）")
            L.append(f"- 按年均值（8h）：{ {k: round(v_, 6) for k, v_ in pv['funding_by_year_mean_8h'].items()} }")
            if "basis_mark_vs_spot" in pv:
                b = pv["basis_mark_vs_spot"]
                L.append(f"- 永续 mark price 相对现货收盘的基差（{b['from'][:10]} → {b['to'][:10]}，{b['n']} 个 8h 点）：均值 {b['mean_bps']:.1f} bps，中位 {b['median_bps']:.1f} bps，P5/P95 {b['p05_bps']:.1f}/{b['p95_bps']:.1f} bps，最大 |基差| {b['abs_max_bps']:.1f} bps")
            L.append(f"- {pv['gap_statement']}")
        else:
            L.append(f"- 不可得：{pv.get('reason')}")
        L.append("")
    L += ["## 成本与成交假设", "", "```", json.dumps(rep["cost_assumptions"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(L)
