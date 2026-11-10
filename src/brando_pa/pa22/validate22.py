"""独立样本验证（预登记 §5）：买盘确认事件 vs 对照，30 / 90 / 180 天扣费后收益、最大不利波动、盈利比例（Wilson 95%）、样本量与独立样本数。
开发段 2021-09 – 2023-12，留出段 2024-01 起（只到期事件）。判定标准事先固定，不调参重试。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import COST_ROUND_TRIP_BPS

HORIZONS = (30, 90, 180)
COOLDOWN = 30
DEV_END = pd.Timestamp("2023-12-31", tz="UTC")


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n; den = 1 + z * z / n; c = (p + z * z / (2 * n)) / den; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den; return (c - h, c + h)


def pick(mask: np.ndarray, cooldown: int = COOLDOWN) -> list[int]:
    out, last = [], -10 ** 9
    for i in np.flatnonzero(mask):
        if i - last > cooldown:
            out.append(int(i)); last = i
    return out


def outcomes(daily: pd.DataFrame, idx: list[int], h: int) -> pd.DataFrame:
    """事件日 i 收盘确认 → 第 i+1 天开盘买入（单边 15 bps）→ 第 i+h 天收盘卖出（单边 15 bps）。期间最低价计最大不利波动。只保留已到期事件。"""
    o = daily["open"].to_numpy(float); c = daily["close"].to_numpy(float); lo = daily["low"].to_numpy(float); ts = pd.to_datetime(daily["ts"], utc=True); side = COST_ROUND_TRIP_BPS / 2 / 1e4; rows = []
    for i in idx:
        if i + h >= len(daily):
            continue
        e = o[i + 1]; x = c[i + h]
        rows.append({"event_day": ts.iloc[i], "entry": e, "exit": x, "ret_net": x * (1 - side) / (e * (1 + side)) - 1, "mae": lo[i + 1:i + h + 1].min() / e - 1})
    return pd.DataFrame(rows)


def independent(days: pd.Series, h: int) -> int:
    n, last = 0, None
    for t in sorted(days):
        if last is None or (t - last).days >= h:
            n += 1; last = t
    return n


def stats(r: pd.DataFrame, h: int) -> dict:
    if r.empty:
        return {"n": 0, "n_independent": 0}
    k = int((r["ret_net"] > 0).sum()); lo, hi = wilson(k, len(r))
    return {"n": int(len(r)), "n_independent": independent(r["event_day"], h), "win_rate": k / len(r), "wilson_lo": lo, "wilson_hi": hi, "median_ret": float(r["ret_net"].median()), "mean_ret": float(r["ret_net"].mean()),
            "p25_ret": float(r["ret_net"].quantile(.25)), "p75_ret": float(r["ret_net"].quantile(.75)), "median_mae": float(r["mae"].median()), "worst_mae": float(r["mae"].min())}


def study(daily_by_sym: dict, states_by_sym: dict) -> dict:
    """返回 {段 → 方案 → 期限 → 统计}，段 = dev / holdout / all；方案 = confirm（买盘确认）/ control_L（只看位置）/ all_days（全部日子）/ observe（观察中）；另按 SMA200 上下分。"""
    res = {}; events = []
    for seg in ("dev", "holdout", "all"):
        res[seg] = {}
        for name in ("confirm", "observe", "control_L", "all_days"):
            res[seg][name] = {}
            for h in HORIZONS:
                parts = []
                for s, d in daily_by_sym.items():
                    st = states_by_sym[s]; ts = pd.to_datetime(st["ts"], utc=True)
                    segm = (ts <= DEV_END) if seg == "dev" else ((ts > DEV_END) if seg == "holdout" else np.ones(len(ts), bool))
                    base = {"confirm": st["level"] == "买盘确认", "observe": st["level"] == "观察中", "control_L": st["L"] & st["Q"], "all_days": st["Q"]}[name].to_numpy() & np.asarray(segm)
                    idx = pick(base) if name != "all_days" else list(np.flatnonzero(base))
                    r = outcomes(d, idx, h)
                    if not r.empty:
                        amap = dict(zip(ts, st["above_sma200"])); r["symbol"] = s; r["above_sma200"] = r["event_day"].map(amap)
                        parts.append(r)
                        if seg == "all" and name == "confirm":
                            events += [{"symbol": s, "h": h, **x} for x in r.to_dict("records")]
                R = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["event_day", "ret_net", "mae", "symbol", "above_sma200"])
                res[seg][name][h] = {"pooled": stats(R, h), **{s: stats(R[R["symbol"] == s], h) for s in daily_by_sym}, "above_sma200": stats(R[R["above_sma200"] == True], h), "below_sma200": stats(R[R["above_sma200"] == False], h)}  # noqa: E712
    return {"results": res, "events": events}


def verdict(res: dict) -> dict:
    c = res["holdout"]["confirm"][90]["pooled"]; k = res["holdout"]["control_L"][90]["pooled"]; checks = {}
    checks["n_independent_ge_10"] = {"ok": c.get("n_independent", 0) >= 10, "detail": f"留出段独立事件 {c.get('n_independent', 0)}（需 ≥ 10）"}
    if c.get("n", 0) and k.get("n", 0):
        checks["median_edge_ge_5pp"] = {"ok": c["median_ret"] - k["median_ret"] >= 0.05, "detail": f"90 天中位扣费收益 确认 {c['median_ret']:+.1%} vs 只看位置 {k['median_ret']:+.1%}"}
        checks["wilson_lo_gt_control"] = {"ok": c["wilson_lo"] > k["win_rate"], "detail": f"确认盈利比例 Wilson 下限 {c['wilson_lo']:.0%} vs 对照盈利比例 {k['win_rate']:.0%}"}
        checks["mae_not_worse"] = {"ok": c["median_mae"] >= k["median_mae"], "detail": f"中位最大不利波动 确认 {c['median_mae']:.1%} vs 对照 {k['median_mae']:.1%}"}
    else:
        checks["samples"] = {"ok": False, "detail": "留出段没有足够的到期事件"}
    ok = all(v["ok"] for v in checks.values())
    return {"verdict": "具备增量证据" if ok else "证据不足", "checks": checks, "rule": "预登记 §5：四项全部满足才算具备增量证据；不满足时不调参重试"}
