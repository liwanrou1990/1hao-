"""第二十六阶段补充审计：样本外 4 个回调候选（A、B 相同）为何被入场前风险收益检查拒绝，以及事后按原定失效价复盘。
分类规则（看结果前写定）：
  · 原因核对：最近上方阻力区下沿相对拟入场价的 R 倍数（R = 拟入场价 − 失效价）＜ 1.5 即被拒；
  · 入场太晚：若在支撑区上沿入场（同一失效价），最近上方阻力 ≥ 1.5R；
  · 止损距离太宽：入场价不变，失效价改为“实际回调低点 − 0.25 ATR”时，最近上方阻力 ≥ 1.5R；
  · 上方空间确实不够：以上两种都不够。按此顺序取第一个成立的作为主因，其余条件也逐条列出。
事后复盘：按下一根日线开盘价入场（含 25 bps 单边成本），目标 = 入场 + 1.5R，失效 = 原定失效价；
目标按当日最高价触及、失效按收盘跌破（与回测引擎一致），另列“盘中最低价触及失效价”的口径。同一天两者都发生时记为“同日，先后不可判”。只读，无交易接口。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..pa11.signals import EntryRiskRewardEvaluator
from ..pa11.zones import SupportResistanceZones
from . import ab26 as M

C1 = (M.FEE_BPS + M.SLIP_BPS) / 1e4


def _r(res_low: float, entry: float, inval: float) -> float | None:
    risk = entry - inval
    return float((res_low - entry) / risk) if risk > 0 else None


def nearest_res(zones, price):
    r = SupportResistanceZones.resistances_above(zones, price); return r[0] if r else None


def hindsight(ctx, s: str, i: int, inval: float) -> dict:
    o, h, l, c = ctx.open[s], ctx.high[s], ctx.low[s], ctx.close[s]
    if i + 1 >= ctx.n:
        return {"note": "无后续数据"}
    fill = o[i + 1] * (1 + C1); risk = fill - inval
    if risk <= 0:
        return {"fill": float(fill), "note": "开盘已低于失效价"}
    tgt = fill + 1.5 * risk; out = {"fill_next_open": float(fill), "risk_at_fill_pct": float(risk / fill), "target_1_5R": float(tgt)}
    def first(close_rule: bool):
        mfe = -np.inf
        for j in range(i + 1, ctx.n):
            hit_t = h[j] >= tgt; hit_f = (c[j] < inval) if close_rule else (l[j] <= inval)
            if hit_t and hit_f:
                return {"event": "同日（先后不可判）", "date": str(ctx.idx[j].date()), "days": j - i, "mfe_R_before": float(max(mfe, (h[j] - fill) / risk))}
            if hit_t:
                return {"event": "先到 +1.5R", "date": str(ctx.idx[j].date()), "days": j - i, "mfe_R_before": float((h[j] - fill) / risk)}
            if hit_f:
                return {"event": "先失效", "date": str(ctx.idx[j].date()), "days": j - i, "mfe_R_before": float(max(mfe, (h[j] - fill) / risk))}
            mfe = max(mfe, (h[j] - fill) / risk)
        return {"event": "至数据末尾两者均未发生", "date": str(ctx.idx[-1].date()), "days": ctx.n - 1 - i, "mfe_R_before": float(mfe)}
    out["close_rule"] = first(True); out["intraday_low_rule"] = first(False)
    j_end = min(ctx.n - 1, i + 60); out["max_high_60d_R"] = float((h[i + 1:j_end + 1].max() - fill) / risk); out["min_low_60d_R"] = float((l[i + 1:j_end + 1].min() - fill) / risk)
    return out


def audit(out_dir: Path | None = None) -> list[dict]:
    out = Path(out_dir or M.OUT_DIR); bars = M.load(); ctx = M.contexts(bars)["A"]
    eps = M.episodes(ctx, "BTCUSDT"); eps = pd.concat([eps, M.episodes(ctx, "ETHUSDT")], ignore_index=True)
    a, b = M.WINDOWS["样本外"]; eps = eps[(pd.to_datetime(eps.signal_date) >= pd.Timestamp(a)) & (pd.to_datetime(eps.signal_date) <= pd.Timestamp(b))]
    rows = []
    for _, e in eps.iterrows():
        s = e.symbol; i = int(e.signal_idx); r = ctx.det_res[s]["A"][i]; z = r["zone"]; inval = float(r["invalidation"]); entry = float(ctx.close[s][i]); a_ = float(ctx.d[s].atr[i])
        st = ctx.st[s][i]; hi = st["last_high_idx"]; H = float(st["last_high"]); seg_low = float(ctx.low[s][hi + 1:i + 1].min()); zones = ctx.zones[s][i]
        res = SupportResistanceZones.resistances_above(zones, entry)
        plan = EntryRiskRewardEvaluator(1.5).plan(entry, inval, zones, st, C1, (z.low, z.high), r["reason"])
        n1 = res[0] if res else None; R1 = _r(n1.low, entry, inval) if n1 else None
        # 反事实 1：在支撑区上沿入场
        e0 = float(z.high); n0 = nearest_res(zones, e0); R_early = _r(n0.low, e0, inval) if (n0 and e0 > inval) else None
        # 反事实 2：失效价收紧到实际回调低点 − 0.25 ATR
        inval_t = seg_low - 0.25 * a_; R_tight = _r(n1.low, entry, inval_t) if n1 else None
        if R1 is not None and R1 >= 1.5:
            cls = "阻力空间足够（被拒另有原因，见计划理由）"
        elif R_early is not None and R_early >= 1.5:
            cls = "入场太晚"
        elif R_tight is not None and R_tight >= 1.5:
            cls = "止损距离太宽"
        else:
            cls = "上方空间确实不够"
        later = [str(ctx.idx[j].date()) for j in range(i, min(ctx.n, i + 30)) if ctx.det_res[s]["A"][j]["signal"] and ctx.st[s][j]["last_high_idx"] == hi]
        rows.append({"symbol": s, "signal_date": e.signal_date, "episode_high": H, "episode_high_date": str(ctx.idx[hi].date()), "pullback_low": seg_low, "depth": float(e.depth),
                     "support_zone": [float(z.low), float(z.high)], "planned_entry_close": entry, "invalidation": inval, "risk_pct": float((entry - inval) / entry), "risk_atr": float((entry - inval) / a_), "atr14": a_,
                     "entry_vs_zone_high_atr": float((entry - z.high) / a_), "recovered_share_of_pullback": float((entry - seg_low) / (H - seg_low)) if H > seg_low else None,
                     "resistances_above": [{"low": float(x.low), "high": float(x.high), "strength": int(x.strength), "R": _r(x.low, entry, inval)} for x in res[:3]], "R_to_episode_high": _r(H, entry, inval),
                     "R_nearest_resistance": R1, "R_if_entry_at_zone_high": R_early, "tight_invalidation": inval_t, "R_with_tight_stop": R_tight, "classification": cls, "plan_reason": plan["reason"], "plan_ok": bool(plan["ok"]),
                     "signal_days_same_episode_30d": later, "hindsight": hindsight(ctx, s, i, inval)})
    (out / "audit_oos_pullback.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8"); return rows


if __name__ == "__main__":
    for r in audit():
        print(r["symbol"], r["signal_date"], r["classification"])
