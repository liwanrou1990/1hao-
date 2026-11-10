"""订单流与价格结构条件（预登记 §3–§4，参数固定）：逐日判定 L / A / B / V / R / Q 与证据等级。
输入：由真实逐笔成交生成的日表（archive22.daily 或实时采集的同结构日表），列 ts, open, high, low, close, volume, buy_usdt, sell_usdt, tot, net, imb, hours, missing_ids。
第 i 天的判定只使用第 i 天收盘及以前的数据（摆动点按第十一阶段规则延迟 3 天确认）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pa11.signals import BreakoutRetestDetector
from ..pa11.structure import DailyStructureDetector
from ..pa11.zones import SupportResistanceZones

P = {"L_above_atr": 1.0, "L_break_atr": 0.5, "A_days": 5, "A_net_share": -0.05, "A_drift_atr": 0.5, "B_days": 3, "B_min_imb": 0.03, "B_prior_days": 10, "B_delta": 0.05,
     "V_mult": 1.5, "V_base_days": 20, "V_close_pos": 0.6, "confirm_A_lookback": 6, "Q_days": 20}
LEVELS = ("买盘确认", "观察中", "证据不足")


def _zone_for(zones, close):
    z = SupportResistanceZones.zone_containing(zones, close)
    return z if z is not None else SupportResistanceZones.nearest_support(zones, close)


def evaluate(daily: pd.DataFrame, start_idx: int = 30, p: dict = P) -> pd.DataFrame:
    d = daily.reset_index(drop=True).copy(); d["ts"] = pd.to_datetime(d["ts"], utc=True)
    det = DailyStructureDetector(d[["ts", "open", "high", "low", "close", "volume"]]); zobj = SupportResistanceZones(det.sw, None, None, det.atr); brt = BreakoutRetestDetector()
    net = d["net"].to_numpy(float); tot = d["tot"].to_numpy(float); low = d["low"].to_numpy(float); high = d["high"].to_numpy(float); close = d["close"].to_numpy(float); n = len(d)
    day_ok = ((d["hours"] == 24) & (d["missing_ids"] == 0)).to_numpy(); gapless_day = np.r_[True, (d["ts"].diff().dt.days.iloc[1:] == 1).to_numpy()]
    sma = pd.Series(close).rolling(200).mean().to_numpy(); rows = []; A_hist = []
    for i in range(n):
        a = det.atr[i]; r = {"ts": d["ts"].iloc[i], "close": close[i], "atr": float(a) if np.isfinite(a) else None, "imb": float(d["imb"].iloc[i]), "above_sma200": bool(np.isfinite(sma[i]) and close[i] > sma[i]) if np.isfinite(sma[i]) else None}
        if i < start_idx or not np.isfinite(a):
            A_hist.append(False); rows.append(r | {"L": False, "A": False, "B": False, "V": False, "R": False, "Q": False, "level": "证据不足", "why": "历史不足（ATR / 结构预热）"}); continue
        zones = zobj.zones_at(i, close[i]); z = _zone_for(zones, close[i]); lo5 = low[i - p["A_days"] + 1:i + 1].min()
        L = bool(z is not None and close[i] - z.high <= p["L_above_atr"] * a and lo5 >= z.low - p["L_break_atr"] * a)
        s5n = net[i - p["A_days"] + 1:i + 1].sum(); s5t = tot[i - p["A_days"] + 1:i + 1].sum()
        A = bool(z is not None and s5n <= p["A_net_share"] * s5t and close[i] >= close[i - p["A_days"]] - p["A_drift_atr"] * a and lo5 >= z.low - p["L_break_atr"] * a)
        imb3 = net[i - p["B_days"] + 1:i + 1].sum() / tot[i - p["B_days"] + 1:i + 1].sum(); pr = slice(i - p["B_days"] - p["B_prior_days"] + 1, i - p["B_days"] + 1); imbp = net[pr].sum() / tot[pr].sum()
        B = bool(imb3 >= p["B_min_imb"] and imb3 - imbp >= p["B_delta"])
        med = np.median(tot[i - p["V_base_days"]:i]); rng = max(high[i] - low[i], 1e-12); cpos = (close[i] - low[i]) / rng
        V = bool(tot[i] >= p["V_mult"] * med and cpos >= p["V_close_pos"] and net[i] >= 0)
        br = brt.check(det, zobj, i, zones); R = False
        if br["signal"]:
            k = br["retest_idx"]; R = bool(net[k:i + 1].sum() >= 0)
        q0 = max(0, i - p["Q_days"] + 1); Q = bool(day_ok[q0:i + 1].all() and gapless_day[q0 + 1:i + 1].all() and i - q0 + 1 == p["Q_days"])
        A_hist.append(A); recentA = any(A_hist[max(0, i - p["confirm_A_lookback"] + 1):i + 1])
        conf = (Q and L and recentA and (B or V)) or (Q and R); obs = Q and L and (A or B or V)
        level = "买盘确认" if conf else ("观察中" if obs else "证据不足")
        why = []
        if not Q:
            why.append("数据质量条件未满足（最近 20 天有缺天或成交编号缺口）")
        if not L:
            why.append("不在已确认支撑区附近" if z is not None else "下方没有已确认区域")
        r |= {"L": L, "A": A, "recentA": recentA, "B": B, "V": V, "R": R, "Q": Q, "level": level, "why": "；".join(why), "zone": [round(z.low, 2), round(z.high, 2)] if z is not None else None, "net5_share": float(s5n / s5t), "imb3": float(imb3), "imb_prior10": float(imbp),
              "tot_vs_med20": float(tot[i] / med) if med > 0 else None, "close_pos": float(cpos), "breakout_retest": bool(br["signal"])}
        rows.append(r)
    return pd.DataFrame(rows)
