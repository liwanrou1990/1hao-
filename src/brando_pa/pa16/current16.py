"""第十六阶段“当前研究估计”与前瞻观察日志。
- 当前读数全部标注为“当前研究估计（未经验证，仅研究性观察）”；本阶段没有任何“前瞻信号 / 可执行买入”。
- 前瞻观察日志：从 2026-11-10 起手动追加（不是常驻服务），只记录读数，不产生订单；每条带上一条哈希。
- online=True 时从 OKX 公开接口补最近已收盘日线（不写回任何历史数据文件）并读实时价；否则只用快照。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..pa11.signals import BreakoutRetestDetector, FailedBreakdownDetector, PullbackConfirmation
from ..pa11.structure import DailyStructureDetector, structure_state
from ..pa11.zones import SupportResistanceZones
from . import data16
from .engine16 import ARM_DAYS, D_LEVELS, X1_LEVELS, X2_LEVELS

OBS_LOG = PROJECT_ROOT / "data_store" / "brando_pa" / "phase16_minercycle" / "forward_observations.jsonl"
LABEL = "当前研究估计（未经验证，仅研究性观察；不是买卖信号）"


def extend_with_okx(d: pd.DataFrame, timeout: float = 15.0) -> tuple[pd.DataFrame, dict]:
    """用 OKX 已收盘日线补到最新（只在内存中；confirm=1 才用）。返回 (扩展后的日线, 实时行情)。"""
    import httpx
    live = {}
    try:
        r = httpx.get("https://www.okx.com/api/v5/market/candles", params={"instId": "BTC-USDT", "bar": "1Dutc", "limit": 20}, timeout=timeout); r.raise_for_status()
        rows = [x for x in r.json()["data"] if x[8] == "1"]
        add = pd.DataFrame([{"ts": pd.Timestamp(int(x[0]), unit="ms", tz="UTC"), "open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[5]), "source": "okx_live_confirmed", "ohlc_ok": True} for x in rows])
        add = add[add["ts"] > d["ts"].max()].sort_values("ts")
        d = pd.concat([d, add], ignore_index=True)
        t = httpx.get("https://www.okx.com/api/v5/market/ticker", params={"instId": "BTC-USDT"}, timeout=timeout).json()["data"][0]
        live = {"price": float(t["last"]), "ts_utc": str(pd.Timestamp(int(t["ts"]), unit="ms", tz="UTC")), "source": "okx_spot_ticker"}
    except Exception as e:  # noqa: BLE001
        live = {"error": f"{type(e).__name__}"}
    return d.reset_index(drop=True), live


def c_confirm_at(d: pd.DataFrame, i: int) -> dict:
    det = DailyStructureDetector(d[["ts", "open", "high", "low", "close", "volume"]].assign(volume=d["volume"].fillna(0.0))); z = SupportResistanceZones(det.sw, None, None, det.atr); zones = z.zones_at(i, det.close[i])
    res = {}
    for nm, D in (("回调确认", PullbackConfirmation()), ("突破回踩", BreakoutRetestDetector()), ("假跌破收复", FailedBreakdownDetector())):
        try:
            x = D.check(det, z, i, zones); res[nm] = {"signal": bool(x["signal"]), "reason": x["reason"]}
        except Exception as e:  # noqa: BLE001
            res[nm] = {"signal": False, "reason": f"不可用：{type(e).__name__}"}
    st = structure_state(det.sw, i)
    return {"detectors": res, "any": any(v["signal"] for v in res.values()), "last_confirmed_swing_low": st["last_low"], "last_confirmed_swing_high": st["last_high"], "atr14": float(det.atr[i]) if np.isfinite(det.atr[i]) else None}


def readings(online: bool = False) -> dict:
    d = data16.load_btc_daily(); live = {}
    if online:
        d, live = extend_with_okx(d)
    f = data16.signal_frame(d); i = len(d) - 1; row = f.iloc[i]; ts = f.index[i]
    price_close = float(row["close"]); price = live.get("price") or price_close
    lo, mid, hi = (float(row[k]) for k in ("cost_low", "cost_mid", "cost_high"))
    wk = f[f["is_week_close"]].dropna(subset=["sma40w"]).iloc[-1]
    recent = f.loc[ts - pd.Timedelta(days=ARM_DAYS):ts]; armed = bool((recent["r"] <= 1.0).any())
    wk_ok = bool(wk["w_close"] > wk["sma20w"] and wk["sma20w"] >= wk["sma20w_4ago"])
    cc = c_confirm_at(d, i); inv = cc["last_confirmed_swing_low"]
    inv_ok = inv is not None and inv < price
    exit_cost = [{"r": lv, "price": lv * mid} for lv in X1_LEVELS]; exit_mvrv = [{"mvrv": lv, "price": lv * float(row["realized_price"])} for lv in X2_LEVELS]
    first_exit = exit_cost[0]["price"]
    rr = {}
    for c in (10, 25, 50):
        cost = 2 * c / 1e4
        rr[str(c)] = ((first_exit * (1 - c / 1e4) - price * (1 + c / 1e4)) / ((price * (1 + c / 1e4)) - inv * (1 - c / 1e4))) if inv_ok else None
    h_last = data16.HALVINGS[-1]; days_since = (ts - h_last).days
    out = {"label": LABEL, "data_as_of_daily_close": str(ts.date()), "onchain_and_cost_as_of": str((ts - pd.Timedelta(days=1)).date()), "price_close": price_close, "live": live, "price_used": price,
           "cost_band": {"low": lo, "mid": mid, "high": hi, "allin_mid": float(row["cost_allin_mid"]), "reliability": str(row["reliability"]), "fleet_j_per_th": float(row["fleet_jth"]), "best_model": str(row["best_model"]), "network_power_gw": float(row["network_power_gw"]),
                         "note": "价格无关的矿机代际模型（事后修正版分母）；无外部真实数据校准；区间 = 效率 ×0.85@0.04 / ×1@0.06 / ×1.3@0.08 美元每度"},
           "ratio": {"price_over_mid": price / mid, "price_over_low": price / lo, "price_over_high": price / hi},
           "halving": {"last": str(h_last.date()), "days_since": days_since, "phase": days_since / 1458, "note": "只是背景标签；第十五阶段减半门控三段全部更差"},
           "valuation": {"mvrv": float(row["mvrv"]), "realized_price": float(row["realized_price"]), "puell": float(row["puell"])},
           "weekly": {"week_close_date": str(wk.name.date()), "w_close": float(wk["w_close"]), "sma20w": float(wk["sma20w"]), "sma40w": float(wk["sma40w"]), "sma20w_rising": bool(wk["sma20w"] >= wk["sma20w_4ago"]), "above_40w": bool(wk["w_close"] > wk["sma40w"])},
           "sma200": float(row["sma200"]),
           "candidate_entry_zone": {"levels": [{"r": lv, "price": lv * mid} for lv in D_LEVELS], "band_low_to_mid": [lo, mid], "label": "候选长期建仓区域 = 当前研究估计（成本会随算力 / 减半移动）"},
           "entry_status": {"A_A0_D_triggered_now": bool(price / mid <= 1.0), "D_levels_hit_now": [lv for lv in D_LEVELS if price / mid <= lv], "armed_B_C_last_540d": armed, "B_weekly_condition": wk_ok, "B_would_buy": bool(armed and wk_ok),
                            "C_confirmation_today": cc["any"], "C_detectors": cc["detectors"], "C_would_buy": bool(armed and cc["any"])},
           "invalidation": {"last_confirmed_daily_swing_low": inv, "applicable": inv_ok, "note": "最近已确认日线摆动低点（k=3）；高于现价 → 不适用（结构已失效）", "weekly_trend_failure_level_sma40w": float(wk["sma40w"])},
           "candidate_exit_zones": {"by_cost_ratio": exit_cost, "by_mvrv": exit_mvrv, "label": "候选牛市退出区域 = 当前研究估计（成本与实现价格都会变化）"},
           "trading_cost": {str(c): {"one_side_bps": c, "round_trip_bps": 2 * c} for c in (10, 25, 50)},
           "reward_risk_to_first_cost_exit": rr, "forward_signal": "无（本阶段策略未通过验证；只记录研究性观察，不产生可执行信号）"}
    return out


def append_observation(online: bool = True, path: Path = OBS_LOG) -> dict:
    """追加一条前瞻观察（只读输入，只写本日志）。"""
    path.parent.mkdir(parents=True, exist_ok=True); r = readings(online=online)
    prev = "0" * 64
    if path.exists() and path.stat().st_size:
        prev = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])["hash"]
    rec = {"recorded_at_utc": str(pd.Timestamp.now(tz="UTC")), "kind": "phase16_research_observation", "prev_hash": prev, "readings": r}
    rec["hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k != "hash"}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


if __name__ == "__main__":
    import sys
    rec = append_observation(online="--offline" not in sys.argv)
    print(json.dumps({"recorded_at_utc": rec["recorded_at_utc"], "data_as_of": rec["readings"]["data_as_of_daily_close"], "price": rec["readings"]["price_used"], "ratio_mid": round(rec["readings"]["ratio"]["price_over_mid"], 3), "hash": rec["hash"][:12]}, ensure_ascii=False))
