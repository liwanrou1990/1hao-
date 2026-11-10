"""第十九阶段市场状态与候选买点（预登记 §2–§3）。只使用决策时已完成的 K 线与已确认的摆动点。
周线状态在周日收盘后更新；日线第 t 日收盘使用“截至 t 已完成的最后一根周线”的状态。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pa11.structure import DailyStructureDetector, atr
from ..pa11.zones import SupportResistanceZones
from ..pa16 import data16

STATES = ("BASE", "DOWNTREND", "BREAKOUT_RETEST", "DISTRIBUTION", "PULLBACK", "UPTREND", "RANGE")
STATE_CN = {"BASE": "底部构筑", "DOWNTREND": "长期下跌", "BREAKOUT_RETEST": "突破回踩", "DISTRIBUTION": "高位分配 / 趋势衰退", "PULLBACK": "上涨中的正常回调", "UPTREND": "长期上涨", "RANGE": "震荡"}
COST_ONE_SIDE = 0.0015
ORDERFLOW_NOTE = "价格行为确认，尚无真实订单流确认"


def load_daily() -> pd.DataFrame:
    d = data16.load_btc_daily(); d["ts"] = pd.to_datetime(d["ts"], utc=True)
    return d.reset_index(drop=True)


def weekly(d: pd.DataFrame) -> pd.DataFrame:
    s = d.set_index("ts")
    w = s.resample("W-SUN", label="right", closed="right").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "ohlc_ok": "all"}).dropna(subset=["close"])
    w = w.reset_index().rename(columns={"ts": "week_end"})
    w = w[w["week_end"] <= d["ts"].max()].reset_index(drop=True)                    # 只保留已完整收盘的周线（最后一根不完整周丢弃）
    w["sma40"] = w["close"].rolling(40).mean(); w["sma10"] = w["close"].rolling(10).mean(); w["sma20"] = w["close"].rolling(20).mean()
    w["slope8"] = w["sma40"] / w["sma40"].shift(8) - 1; w["atrw"] = atr(w[["high", "low", "close"]], 14).to_numpy()
    n = len(w); h, l = w["high"].to_numpy(float), w["low"].to_numpy(float); k = 2
    sh = [(i, h[i], i + k) for i in range(k, n - k) if all(h[i] > h[i - j] for j in range(1, k + 1)) and all(h[i] > h[i + j] for j in range(1, k + 1))]
    sl = [(i, l[i], i + k) for i in range(k, n - k) if all(l[i] < l[i - j] for j in range(1, k + 1)) and all(l[i] < l[i + j] for j in range(1, k + 1))]
    w.attrs["swing_highs"], w.attrs["swing_lows"] = sh, sl
    c = w["close"].to_numpy(float); prior52 = np.full(n, np.nan)
    for j in range(56, n):
        prior52[j] = h[j - 56:j - 4].max()
    w["prior52_high"] = prior52
    states, last_sh, prev_sh, last_sl, prev_sl, bo_level = [], [], [], [], [], []
    for j in range(n):
        hs = [p for (_, p, cf) in sh if cf <= j]; ls = [p for (_, p, cf) in sl if cf <= j]
        last_sh.append(hs[-1] if hs else np.nan); prev_sh.append(hs[-2] if len(hs) > 1 else np.nan); last_sl.append(ls[-1] if ls else np.nan); prev_sl.append(ls[-2] if len(ls) > 1 else np.nan)
        lvl = np.nan
        for kk in range(max(0, j - 12), j):
            if np.isfinite(prior52[kk]) and c[kk] > prior52[kk]:
                lvl = prior52[kk]
        bo_level.append(lvl)
    w["last_sh"], w["prev_sh"], w["last_sl"], w["prev_sl"], w["breakout_level"] = last_sh, prev_sh, last_sl, prev_sl, bo_level
    for j in range(n):
        r = w.iloc[j]
        if not np.isfinite(r["sma40"]) or not np.isfinite(r["slope8"]):
            states.append("RANGE"); continue
        lo26 = w["low"].iloc[max(0, j - 25):j + 1]; weeks_since_low = j - int(lo26.values.argmin()) - max(0, j - 25)
        s4 = w["slope8"].iloc[j - 4] if j >= 4 else np.nan
        if (r["close"] < r["sma40"] or r["slope8"] <= 0) and weeks_since_low >= 8:
            st = "BASE"
        elif r["close"] < r["sma40"] and r["slope8"] < 0:
            st = "DOWNTREND"
        elif np.isfinite(r["breakout_level"]) and np.isfinite(r["atrw"]) and r["low"] <= r["breakout_level"] + r["atrw"] and r["close"] >= r["breakout_level"] - 0.5 * r["atrw"]:
            st = "BREAKOUT_RETEST"
        elif r["close"] > r["sma40"] and np.isfinite(s4) and r["slope8"] < s4 and r["slope8"] < 0.02 and np.isfinite(r["prev_sh"]) and r["last_sh"] < r["prev_sh"]:
            st = "DISTRIBUTION"
        elif r["close"] > r["sma40"] and r["slope8"] > 0 and r["close"] < r["sma10"]:
            st = "PULLBACK"
        elif r["close"] > r["sma40"] and r["slope8"] > 0:
            st = "UPTREND"
        else:
            st = "RANGE"
        states.append(st)
    w["state"] = states
    below = (w["close"] < w["sma40"]).astype(int)
    w["below_count_52"] = below.shift(1).rolling(52, min_periods=52).sum()
    w["trend_failure"] = ((w["close"] < w["sma40"]) & (w["close"].shift(1) < w["sma40"].shift(1))) | (w["state"] == "DOWNTREND")
    return w


def daily_frame(d: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    """日线特征 + 截至当日已完成的最后一根周线的状态（周日当天包含本周）。"""
    f = d.copy(); f["sma200"] = f["close"].rolling(200).mean(); f["sma50"] = f["close"].rolling(50).mean(); f["atr14"] = atr(f[["high", "low", "close"]], 14).to_numpy()
    f["vol_ma20"] = f["volume"].rolling(20).mean(); rng = (f["high"] - f["low"]).replace(0, np.nan); f["close_pos"] = (f["close"] - f["low"]) / rng
    j = np.searchsorted(w["week_end"].to_numpy(), f["ts"].to_numpy(), side="right") - 1
    for col in ("state", "sma40", "sma20", "slope8", "atrw", "last_sl", "prev_sl", "last_sh", "trend_failure", "close"):
        vals = w[col].to_numpy(); f["w_" + col] = [vals[x] if x >= 0 else (np.nan if col != "state" else "RANGE") for x in j]
    f["week_idx"] = j; f["is_week_close"] = f["ts"].dt.dayofweek == 6
    return f


def _plan(entry: float, inval: float, w: pd.DataFrame, j: int, kind: str) -> dict:
    """入场前的退出计划：目标区 = 入场上方最近两个已确认周摆动高点；没有则 +2R / +4R。"""
    R = entry - inval; sh = [p for (_, p, cf) in w.attrs["swing_highs"] if cf <= j and p > entry * 1.01]
    above = sorted(set(sh))[:2]
    if len(above) >= 2:
        t1, t2, basis = above[0], above[1], "上方已确认周摆动高点"
    elif len(above) == 1:
        t1, t2, basis = above[0], max(above[0], entry + 4 * R), "上方已确认周摆动高点 + 4R"
    else:
        t1, t2, basis = entry + 2 * R, entry + 4 * R, "上方无已确认阻力（历史高位附近）：+2R / +4R"
    c = COST_ONE_SIDE
    rr = ((t1 * (1 - c)) - entry * (1 + c)) / ((entry * (1 + c)) - inval * (1 - c)) if R > 0 else np.nan
    return {"entry_ref": entry, "invalidation": inval, "max_loss_pct": (entry * (1 + c) - inval * (1 - c)) / (entry * (1 + c)) if R > 0 else np.nan, "target_1": t1, "target_2": t2, "target_basis": basis, "rr_net": rr, "kind": kind}


def candidates(d: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """返回 (候选列表, 日线特征, 周线)。候选在第 i 日收盘形成，计划以第 i 日收盘价为参考（实际成交在 i+1 开盘）。"""
    d = d if d is not None else load_daily(); w = weekly(d); f = daily_frame(d, w); n = len(f); out = []
    ok = f["ohlc_ok"].to_numpy(bool); c, h, lo, o = (f[k].to_numpy(float) for k in ("close", "high", "low", "open")); a = f["atr14"].to_numpy(float); s200 = f["sma200"].to_numpy(float)
    st = f["w_state"].to_numpy(); vol = f["volume"].to_numpy(float); vma = f["vol_ma20"].to_numpy(float); cp = f["close_pos"].to_numpy(float)
    det = DailyStructureDetector(d[["ts", "open", "high", "low", "close", "volume"]].assign(volume=d["volume"].fillna(0.0))); zobj = SupportResistanceZones(det.sw, None, None, det.atr)
    last_a = -10_000
    # A 趋势回调
    for i in range(205, n):
        if not ok[i] or st[i] not in ("UPTREND", "PULLBACK") or i - last_a < 10 or not np.isfinite(a[i]):
            continue
        if not (c[i] > h[i - 1] and cp[i] >= 0.6 and np.isfinite(vma[i]) and vol[i] >= 0.8 * vma[i]):
            continue
        rising = s200[i] > s200[i - 20]; sup = None
        if rising and any(lo[t] <= s200[t] + a[t] and lo[t] >= s200[t] - 2 * a[t] for t in range(i - 4, i + 1)):
            sup = (s200[i] - a[i], s200[i] + a[i], "上升中的 200 日均线 ± 1 ATR")
        else:
            zs = [z for z in zobj.zones_at(i, c[i]) if z.high < c[i]]
            if zs:
                z = max(zs, key=lambda z: z.high)
                if any(lo[t] <= z.high for t in range(i - 4, i + 1)):
                    sup = (z.low, z.high, f"已确认日线支撑区（强度 {z.strength}）")
        if sup is None:
            continue
        last_a = i; j = int(f["week_idx"].iloc[i]); inval = sup[0] - 0.25 * a[i]
        out.append({"i": i, "type": "A", "ts": f["ts"].iloc[i], "zone_low": sup[0], "zone_high": sup[1], "structure": f"周线 {STATE_CN[st[i]]}；过去 5 日触及{sup[2]}；收盘 > 昨高、收盘位置 {cp[i]:.2f}", "volume": f"量比 {vol[i] / vma[i]:.2f}（{ORDERFLOW_NOTE}）", **_plan(c[i], inval, w, j, "A")})
    # B 突破后回踩
    roll_max = pd.Series(c).shift(1).rolling(365, min_periods=365).max().to_numpy(); used = set(); bo_k = None; bo_lvl = None
    for i in range(366, n):
        if np.isfinite(roll_max[i]) and c[i] > roll_max[i]:
            bo_k, bo_lvl = i, roll_max[i]; continue
        if bo_k is None or bo_k in used or not ok[i] or st[i] == "DOWNTREND" or not np.isfinite(a[i]):
            continue
        if not (3 <= i - bo_k <= 30):
            continue
        seg = range(bo_k + 1, i + 1)
        retested = any(lo[t] <= bo_lvl + a[t] for t in seg); held = all(c[t] >= bo_lvl - 0.5 * a[t] for t in seg)
        if retested and held and c[i] > h[i - 1]:
            used.add(bo_k); j = int(f["week_idx"].iloc[i]); inval = bo_lvl - a[i]
            out.append({"i": i, "type": "B", "ts": f["ts"].iloc[i], "zone_low": bo_lvl - 0.5 * a[i], "zone_high": bo_lvl + 0.5 * a[i], "structure": f"{(i - bo_k)} 日前收盘突破 365 日最高收盘 {bo_lvl:,.0f}；回踩未跌破；今日收盘 > 昨高", "volume": f"量比 {vol[i] / vma[i]:.2f}（{ORDERFLOW_NOTE}）" if np.isfinite(vma[i]) else ORDERFLOW_NOTE, **_plan(c[i], inval, w, j, "B")})
    # C 熊市后趋势恢复（周线）：每段熊市只取一次——两次 C 之间须有 ≥ 26 个周收盘低于 40 周均线
    below = (w["close"] < w["sma40"]).to_numpy(); last_fire = None; fts = f["ts"]
    for jj in range(1, len(w)):
        r = w.iloc[jj]
        if not np.isfinite(r["sma40"]) or not np.isfinite(r["below_count_52"]) or r["below_count_52"] < 26:
            continue
        if last_fire is not None and below[last_fire + 1:jj].sum() < 26:
            continue
        cross = r["close"] > r["sma40"] and w["close"].iloc[jj - 1] <= w["sma40"].iloc[jj - 1]
        if cross and np.isfinite(r["prev_sl"]) and r["last_sl"] > r["prev_sl"]:
            last_fire = jj; i = int(f.index[fts <= r["week_end"]][-1]); inval = float(r["last_sl"])
            out.append({"i": i, "type": "C", "ts": fts.iloc[i], "zone_low": r["close"] - 0.5 * r["atrw"], "zone_high": r["close"] + 0.5 * r["atrw"], "structure": f"此前 52 周有 {int(r['below_count_52'])} 周收于 40 周均线下；本周首次收回 40 周均线；周摆动低点抬高 {r['prev_sl']:,.0f} → {r['last_sl']:,.0f}", "volume": ORDERFLOW_NOTE, **_plan(float(c[i]), inval, w, jj, "C")})
    cand = pd.DataFrame(out).sort_values("i").reset_index(drop=True) if out else pd.DataFrame()
    return cand, f, w
