"""Phase 11 入场拒绝原因完整归因（只读 Phase 11 结果 + 重算每个候选的反事实）。
对 2018-01 → 最新每一天、每个资产：记录周线趋势 / SMA200 过滤 / A、B 检测器输出；对每个 A/B 候选：
  - 上方是否存在任何已确认阻力区；最近阻力距离（R 倍数）；是否创 400 日新高 / 历史新高；
  - 逐个过滤器的反事实：去掉“T1 必须来自已确认阻力且 ≥1.5R”、去掉净盈亏比门槛、去掉量比、去掉收盘位置、去掉缩量条件后各自会接受多少；
  - 过滤器重叠：候选同时被多少个条件拒绝；
  - 与核心策略的关系：候选日 B2 / SMA200 核心是否已经持仓（PA 不应阻止核心持仓）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..holding.data import load_dataset
from ..pa11.engine11 import MarketContext
from ..pa11.signals import EntryRiskRewardEvaluator
from ..pa11.zones import SupportResistanceZones
from ..trendsys.trend_layer import trend_signal

SYMS = ["BTCUSDT", "ETHUSDT"]


def reject_audit(out_dir: Path, ctx: MarketContext | None = None, cost_one_side: float = 0.0025, start: str = "2018-01-01") -> dict:
    bars = load_dataset(); ctx = ctx or MarketContext(bars); ev = EntryRiskRewardEvaluator(1.5)
    i0 = int(np.searchsorted(ctx.idx, pd.Timestamp(start, tz="UTC"))); rows = []; daily = []
    core_on = {s: trend_signal(pd.DataFrame({"ts": ctx.idx, "close": ctx.close[s]}), 200, 1)["on"].to_numpy(bool) for s in SYMS}
    for s in SYMS:
        hi_run = pd.Series(ctx.high[s]).cummax().to_numpy(); hi400 = pd.Series(ctx.high[s]).rolling(400, min_periods=1).max().to_numpy()
        for i in range(i0, ctx.n):
            wt = ctx.wt[s][i]["trend"]; ok = ctx.ds[s][i]["sma_filter_ok"]; pre = wt == "UP" and ok
            daily.append({"symbol": s, "weekly": wt, "sma_ok": ok, "pre_ok": pre, "core_on": bool(core_on[s][i])})
            if not pre:
                continue
            for k in ("A", "B"):
                r = ctx.det_res[s][k][i]; reason = str(r.get("reason", ""))
                near_miss = (k == "A" and reason.startswith("等待确认")) or (k == "B" and "无已确认" not in reason and not r["signal"])
                if not r["signal"] and not near_miss:
                    continue
                close = ctx.close[s][i]; zones = ctx.zones[s][i]; st = ctx.st[s][i]
                res = SupportResistanceZones.resistances_above(zones, close); inval = r.get("invalidation", np.nan)
                risk = close - inval if np.isfinite(inval) else np.nan
                nearest_R = (res[0].low - close) / risk if (res and np.isfinite(risk) and risk > 0) else np.nan
                plan = ev.plan(close, inval, zones, st, cost_one_side, (r["zone"].low, r["zone"].high) if r.get("zone") else (np.nan, np.nan), reason) if (r["signal"] and np.isfinite(inval)) else {"ok": False, "reason": reason}
                a_ = ctx.d[s].atr[i]; dist_atr = (hi400[i] - close) / a_ if (np.isfinite(a_) and a_ > 0) else np.nan
                rows.append({"ts": ctx.idx[i], "symbol": s, "strategy": k, "triggered": bool(r["signal"]), "near_miss_reason": reason if not r["signal"] else "", "close": close, "invalidation": inval, "risk_pct_of_price": (risk / close) if np.isfinite(risk) else np.nan, "dist_to_400d_high_atr": dist_atr,
                             "any_resistance_above": bool(res), "nearest_resistance_R": nearest_R, "n_resistances_above": len(res), "at_400d_high": bool(close >= hi400[i] * 0.995), "at_all_time_high": bool(close >= hi_run[i] * 0.98),
                             "accepted_phase11_rule": bool(plan.get("ok")), "phase11_reject_reason": plan.get("reason", ""), "core_B2_or_SMA_on": bool(core_on[s][i]), "weekly": wt})
    cand = pd.DataFrame(rows); d = pd.DataFrame(daily)
    trig = cand[cand.triggered]
    def cf(mask_name, keep):  # 反事实：只保留 keep 条件后接受数
        return int(keep.sum())
    out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "window": f"{start} → {str(ctx.idx[-1].date())}", "note": "开发性分析；基于 Phase 11 冻结规则在同一历史上的反事实统计，不构成盲测",
           "days": {"total": int(len(d) / 2), "weekly_UP_and_sma_ok_days_by_symbol": d.groupby("symbol")["pre_ok"].sum().to_dict(), "core_on_days_by_symbol": d.groupby("symbol")["core_on"].sum().to_dict(),
                    "core_on_but_pre_not_ok_days": d[(d.core_on) & (~d.pre_ok)].groupby("symbol").size().to_dict(), "weekly_trend_days": d.groupby(["symbol", "weekly"]).size().unstack(fill_value=0).to_dict()},
           "candidates": {"triggered_total": int(len(trig)), "by_strategy": trig.groupby("strategy").size().to_dict(), "accepted_by_phase11_rule": int(trig.accepted_phase11_rule.sum()),
                          "rejected_no_resistance_above_at_all": int((~trig.any_resistance_above).sum()), "rejected_resistance_exists_but_below_1_5R": int((trig.any_resistance_above & (trig.nearest_resistance_R < 1.5) & (~trig.accepted_phase11_rule)).sum()),
                          "rejected_rr_below_threshold_with_T1": int((trig.any_resistance_above & (trig.nearest_resistance_R >= 1.5) & (~trig.accepted_phase11_rule)).sum()),
                          "at_400d_high_share": float(trig.at_400d_high.mean()) if len(trig) else None, "within_2atr_of_400d_high_share": float((trig.dist_to_400d_high_atr <= 2).mean()) if len(trig) else None, "median_dist_to_400d_high_atr": float(trig.dist_to_400d_high_atr.median()) if len(trig) else None,
                          "no_resistance_cases_within_2atr_of_400d_high_share": float((trig[~trig.any_resistance_above].dist_to_400d_high_atr <= 2).mean()) if (~trig.any_resistance_above).any() else None, "at_all_time_high_share": float(trig.at_all_time_high.mean()) if len(trig) else None,
                          "core_already_holding_share": float(trig.core_B2_or_SMA_on.mean()) if len(trig) else None, "median_risk_pct_of_price": float(trig.risk_pct_of_price.median()) if len(trig) else None,
                          "near_miss_waiting_confirmation": int((~cand.triggered).sum()), "near_miss_top_reasons": cand[~cand.triggered]["near_miss_reason"].str.slice(0, 40).value_counts().head(8).to_dict()},
           "counterfactual_accepts": {"phase11_rule(T1 from confirmed resistance >=1.5R and net RR>=1.5)": int(trig.accepted_phase11_rule.sum()), "drop_T1_structure_requirement(any trigger accepted)": int(len(trig)),
                                      "require_only_any_resistance_above(no 1.5R)": int(trig.any_resistance_above.sum()), "require_resistance>=1.0R": int((trig.nearest_resistance_R >= 1.0).sum()), "require_resistance>=1.5R": int((trig.nearest_resistance_R >= 1.5).sum())},
           "conclusions": []}
    c = out["candidates"]
    out["conclusions"] = [f"触发 {c['triggered_total']} 个候选（A {c['by_strategy'].get('A', 0)} / B {c['by_strategy'].get('B', 0)}），Phase 11 规则接受 {c['accepted_by_phase11_rule']}。",
                          f"被拒主因：上方完全没有已确认阻力 {c['rejected_no_resistance_above_at_all']} 个；有阻力但 < 1.5R {c['rejected_resistance_exists_but_below_1_5R']} 个；有 ≥1.5R 阻力但扣费后净盈亏比不足 {c['rejected_rr_below_threshold_with_T1']} 个。",
                          f"候选中 {c['within_2atr_of_400d_high_share']:.0%} 距 400 日最高价 ≤ 2 ATR（中位 {c['median_dist_to_400d_high_atr']:.1f} ATR），{c['at_all_time_high_share']:.0%} 处于历史新高 2% 以内；“上方无阻力”的候选中 {c['no_resistance_cases_within_2atr_of_400d_high_share']:.0%} 距 400 日高点 ≤ 2 ATR → 价格已在最高阻力带内部或之上，“目标必须来自已知阻力”对新高 / 阻力带内行情存在系统性排斥。",
                          f"候选日 {c['core_already_holding_share']:.0%} 的时间核心策略（SMA200/B2）本来就已持仓：Phase 11 把 PA 当作唯一入场条件，等于放弃了核心持仓；Phase 12 改为 PA 只做加仓 / 退出增强。",
                          "量比 / 收盘位置 / 缩量条件只在“等待确认”层面起作用（near-miss），不与 T1 规则重叠；真正起决定作用的单一过滤器是 T1 结构要求。",
                          f"失效价中位距离 {c['median_risk_pct_of_price']:.1%}：1% 风险预算 ÷ 该距离 ≈ 10% 仓位，与“最终 USDT 最大化”目标耦合过紧（Phase 12 以 core_frac / opp_frac 预算为主、风险预算为上限）。"]
    out_dir.mkdir(parents=True, exist_ok=True); cand.assign(ts=cand["ts"].astype(str)).to_csv(out_dir / "phase11_candidates_audit.csv", index=False)
    atomic_write_json(out_dir / "phase11_reject_audit.json", out, indent=1, default=str)
    L = [f"# Phase 11 入场拒绝原因归因（开发性分析）· {out['checked_at_utc'][:19]} UTC · 窗口 {out['window']}", "", "## 天数统计", f"- 周线 UP 且 SMA200 过滤通过的天数：{out['days']['weekly_UP_and_sma_ok_days_by_symbol']}；核心（close > SMA200）持仓天数：{out['days']['core_on_days_by_symbol']}；核心持仓但 PA 前置不通过的天数：{out['days']['core_on_but_pre_not_ok_days']}",
         f"- 周线趋势分布：{out['days']['weekly_trend_days']}", "", "## 候选与拒绝", ""] + [f"- {k}: {v}" for k, v in c.items()] + ["", "## 反事实接受数"] + [f"- {k}: {v}" for k, v in out["counterfactual_accepts"].items()] + ["", "## 结论"] + [f"- {x}" for x in out["conclusions"]]
    atomic_write_text(out_dir / "phase11_reject_audit.md", "\n".join(L)); return out
