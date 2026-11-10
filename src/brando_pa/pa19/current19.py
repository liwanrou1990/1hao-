"""第十九阶段当前读数与独立前瞻观察日志（只追加、哈希链、不产生订单）。
所有输出标注为“研究性观察”；本阶段没有任何策略通过判定，因此“已验证的模拟策略信号”恒为“无”。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..pa16.current16 import extend_with_okx
from . import states19 as S

OBS_LOG = PROJECT_ROOT / "data_store" / "brando_pa" / "phase19_longterm" / "forward_observations.jsonl"
LABEL = "研究性观察（候选区域不等于买入信号；本阶段没有已验证的模拟策略信号）"


def readings(online: bool = False) -> dict:
    d = S.load_daily(); live = {}
    if online:
        d, live = extend_with_okx(d); d["ts"] = pd.to_datetime(d["ts"], utc=True); d["ohlc_ok"] = d["ohlc_ok"].fillna(True).astype(bool)
    cand, f, w = S.candidates(d); i = len(f) - 1; r = f.iloc[i]; wl = w.iloc[-1]; price = live.get("price") or float(r["close"])
    sh = sorted({p for (_, p, cf) in w.attrs["swing_highs"] if cf <= len(w) - 1}); sl = sorted({p for (_, p, cf) in w.attrs["swing_lows"] if cf <= len(w) - 1})
    res_above = [p for p in sh if p > price][:2]; sup_below = [p for p in sl if p < price][-2:]
    st = str(wl["state"]); s200 = float(r["sma200"]); a = float(r["atr14"]); rising = bool(f["sma200"].iloc[i] > f["sma200"].iloc[i - 20])
    unmet = {"A_趋势回调": [x for x, ok in ((f"周线状态须为长期上涨或正常回调（当前：{S.STATE_CN[st]}）", st in ("UPTREND", "PULLBACK")), (f"200 日均线须上行（当前{'上行' if rising else '未上行'}）", rising),
                                               (f"过去 5 日最低价须触及 200 日均线 ± 1 ATR（{s200 - a:,.0f} – {s200 + a:,.0f}）", any(f["low"].iloc[t] <= f["sma200"].iloc[t] + f["atr14"].iloc[t] for t in range(i - 4, i + 1))),
                                               ("今日收盘须 > 昨日最高、收盘位置 ≥ 0.6、量比 ≥ 0.8", bool(r["close"] > f["high"].iloc[i - 1] and r["close_pos"] >= 0.6 and r["volume"] >= 0.8 * r["vol_ma20"]))) if not ok],
             "B_突破回踩": [x for x, ok in ((f"须先收盘突破 365 日最高收盘（{f['close'].iloc[i - 365:i].max():,.0f}）", bool(f["close"].iloc[i - 30:i + 1].max() > f["close"].iloc[i - 395:i - 30].max())), (f"周线状态不能是长期下跌（当前：{S.STATE_CN[st]}）", st != "DOWNTREND")) if not ok],
             "C_熊市后趋势恢复": [x for x, ok in ((f"此前 52 周须有 ≥ 26 周收于 40 周均线下（当前 {int(wl['below_count_52']) if np.isfinite(wl['below_count_52']) else '—'} 周）", bool(np.isfinite(wl["below_count_52"]) and wl["below_count_52"] >= 26)),
                                                    (f"本周须是首次收回 40 周均线（上周收盘 ≤ 40 周均线；当前周收盘 {wl['close']:,.0f}，40 周均线 {wl['sma40']:,.0f}）", bool(wl["close"] > wl["sma40"] and w["close"].iloc[-2] <= w["sma40"].iloc[-2])), ("须出现已确认的周线更高低点", bool(np.isfinite(wl["prev_sl"]) and wl["last_sl"] > wl["prev_sl"]))) if not ok]}
    todays = cand[cand["i"] == i].drop(columns=["i"]).assign(ts=lambda x: x["ts"].astype(str)).to_dict("records") if len(cand) else []
    recent = cand[cand["i"] >= i - 30].drop(columns=["i"]).assign(ts=lambda x: x["ts"].astype(str)).to_dict("records") if len(cand) else []
    return {"label": LABEL, "data_as_of": str(r["ts"].date()), "weekly_as_of": str(wl["week_end"].date()), "price": price, "live": live, "weekly_state": st, "weekly_state_cn": S.STATE_CN[st],
            "weekly": {"close": float(wl["close"]), "sma40": float(wl["sma40"]), "slope8": float(wl["slope8"]), "sma20": float(wl["sma20"]), "atrw": float(wl["atrw"]), "trend_failure": bool(wl["trend_failure"])},
            "daily": {"sma200": s200, "sma200_rising": rising, "sma50": float(r["sma50"]), "atr14": a}, "resistance_above": res_above, "support_below": sup_below,
            "candidates_today": todays, "candidates_last_30d": recent, "unmet_conditions": unmet, "orderflow_note": S.ORDERFLOW_NOTE, "validated_strategy_signal": "无"}


def append_observation(online: bool = True, path: Path = OBS_LOG) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True); r = readings(online=online); prev = "0" * 64
    if path.exists() and path.stat().st_size:
        prev = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])["hash"]
    rec = {"recorded_at_utc": str(pd.Timestamp.now(tz="UTC")), "kind": "phase19_independent_forward_observation", "prev_hash": prev, "readings": r}
    rec["hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k != "hash"}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


if __name__ == "__main__":
    import sys
    rec = append_observation(online="--offline" not in sys.argv); rd = rec["readings"]
    print(json.dumps({"recorded_at_utc": rec["recorded_at_utc"], "data_as_of": rd["data_as_of"], "price": rd["price"], "weekly_state": rd["weekly_state_cn"], "candidates_today": len(rd["candidates_today"]), "hash": rec["hash"][:12]}, ensure_ascii=False))
