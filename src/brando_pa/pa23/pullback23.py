"""第二十三阶段：周线趋势 + 日线回调买入点（预登记 docs/brando_pa_validation/phase23_pullback/preregistration.md）。
只用仓库内已有日线；只做历史模拟；不交易；不改动任何现有策略。第 i 天的判定只用第 i 天收盘及以前的数据，成交在第 i+1 天开盘。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..holding.engine import HoldResult
from ..pa18 import phase18
from ..pa19 import states19 as S
from ..pa20.alloc20 import metrics
from ..pa20.execution20 import final_liquidation_with_impact, liquidity_table, make_ledger
from ..pa22.validate22 import pick, wilson

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase23_pullback"
BUCKETS = {"浅档 5–10%": (0.05, 0.10), "中档 10–15%": (0.10, 0.15), "深档 15–25%": (0.15, 0.25000001)}
EXITS = ("H30", "H90", "H180", "TF")
HOLD = {"H30": 30, "H90": 90, "H180": 180}
START = {"BTCUSDT": "2016-01-01", "ETHUSDT": "2017-01-01"}
FREEZE_DAY = pd.Timestamp("2026-11-10", tz="UTC")
COSTS = {"0 bps": (0.0, 0.0, False), "15 bps + 冲击": (10.0, 5.0, True), "25 bps + 冲击": (20.0, 5.0, True), "50 bps + 冲击": (45.0, 5.0, True)}


def load(sym: str, clean_wicks: bool = False) -> pd.DataFrame:
    """clean_wicks（事后稳健性检验，不用于主结论）：最低价 < 开收盘较小值的 50% 或最高价 > 较大值的 2 倍时视为单一交易所闪崩影线，用开收盘范围替换。"""
    if sym == "BTCUSDT":
        d = S.load_daily()
    else:
        d = phase18.load_bars()["ETHUSDT"].copy(); d["ts"] = pd.to_datetime(d["ts"], utc=True); d["ohlc_ok"] = True
    d = d[["ts", "open", "high", "low", "close", "volume", "ohlc_ok"]].reset_index(drop=True)
    if clean_wicks:
        lo_b = d[["open", "close"]].min(axis=1); hi_b = d[["open", "close"]].max(axis=1)
        d["low"] = np.where(d["low"] < 0.5 * lo_b, lo_b, d["low"]); d["high"] = np.where(d["high"] > 2 * hi_b, hi_b, d["high"])
    w = S.weekly(d); f = S.daily_frame(d, w); f["w_ok"] = (f["w_close"] > f["w_sma40"]) & (f["w_slope8"] > 0); f["w_tf"] = f["w_trend_failure"].fillna(False).astype(bool)
    return f


def signals(f: pd.DataFrame, start: str) -> pd.DataFrame:
    """每天的回调深度、确认与档位；每个 60 天高点在同一档只取第一次确认。"""
    hi = f["high"].to_numpy(float); lo = f["low"].to_numpy(float); op = f["open"].to_numpy(float); cl = f["close"].to_numpy(float); ok = f["w_ok"].to_numpy(bool); ts = f["ts"]
    i0 = int(pd.DatetimeIndex(ts).searchsorted(pd.Timestamp(start, tz="UTC"))); rows = []; seen = set()
    for i in range(max(i0, 60), len(f)):
        win = hi[i - 59:i + 1]; hidx = i - 59 + int(len(win) - 1 - np.argmax(win[::-1]))        # 60 天最高价（相同取最近一次）
        seg = lo[hidx:i + 1]; lidx = hidx + int(len(seg) - 1 - np.argmin(seg[::-1])); H, L = hi[hidx], lo[lidx]; D = 1 - L / H
        conf = cl[i] > hi[i - 1] and cl[i] > op[i] and i - lidx <= 9
        b = next((k for k, (a, z) in BUCKETS.items() if a <= D < z), None)
        r = {"i": i, "ts": ts.iloc[i], "H": H, "H_day": ts.iloc[hidx], "L": L, "L_day": ts.iloc[lidx], "depth": D, "confirm": bool(conf), "weekly_ok": bool(ok[i]), "bucket": b, "too_deep": bool(D > 0.25)}
        if conf and ok[i] and b is not None and (b, hidx) not in seen:
            seen.add((b, hidx)); r["signal"] = True
        else:
            r["signal"] = False
        rows.append(r)
    return pd.DataFrame(rows)


def _tf_exit(f: pd.DataFrame, i: int) -> int | None:
    """第 i+1 天买入后，第一个周线趋势失效的日子 k（≥ i+1），在 k+1 开盘卖出；返回卖出日下标。"""
    tf = f["w_tf"].to_numpy(bool)
    for k in range(i + 1, len(f) - 1):
        if tf[k]:
            return k + 1
    return None


def events(f: pd.DataFrame, sig: pd.DataFrame, side_bps: float = 15.0) -> pd.DataFrame:
    """事件统计：每个信号次日开盘买入；持有 h 天后的下一个开盘卖出；最大不利波动 = 持有期内最低价 ÷ 买入价 − 1。"""
    op = f["open"].to_numpy(float); lo = f["low"].to_numpy(float); cl = f["close"].to_numpy(float); n = len(f); c = side_bps / 1e4; rows = []
    for _, s in sig[sig["signal"]].iterrows():
        i = int(s["i"]); e = op[i + 1] if i + 1 < n else np.nan; r = {"bucket": s["bucket"], "signal_day": s["ts"], "entry_day": f["ts"].iloc[i + 1] if i + 1 < n else None, "entry": e, "H": s["H"], "depth": s["depth"], "discount": 1 - e / s["H"] if np.isfinite(e) else np.nan}
        for ex in EXITS:
            j = (i + 1 + HOLD[ex]) if ex in HOLD else _tf_exit(f, i)
            if j is None or j >= n or not np.isfinite(e):
                r[f"ret_{ex}"] = np.nan; r[f"mae_{ex}"] = np.nan; r[f"maec_{ex}"] = np.nan; r[f"days_{ex}"] = np.nan; continue
            r[f"ret_{ex}"] = op[j] * (1 - c) / (e * (1 + c)) - 1; r[f"mae_{ex}"] = lo[i + 1:j].min() / e - 1; r[f"maec_{ex}"] = cl[i + 1:j].min() / e - 1; r[f"days_{ex}"] = j - (i + 1)
        rows.append(r)
    return pd.DataFrame(rows)


def control_events(f: pd.DataFrame, start: str, side_bps: float = 15.0) -> pd.DataFrame:
    ts = f["ts"]; m = (f["w_ok"] & (ts >= pd.Timestamp(start, tz="UTC"))).to_numpy(); idx = pick(m, 30)
    sig = pd.DataFrame({"i": idx, "ts": ts.iloc[idx].to_numpy(), "H": f["close"].iloc[idx].to_numpy(), "depth": np.nan, "bucket": "对照：周线条件成立的任意一天", "signal": True})
    return events(f, sig, side_bps)


def strategy(f: pd.DataFrame, sig: pd.DataFrame, sym: str, bucket: str, exit_: str, start: str, fee: float = 10.0, slip: float = 5.0, impact: bool = True, initial: float = 100_000.0, weight: float = 1.0, return_nav: bool = False):
    """完整交易：满仓或空仓；持仓期间忽略新信号；成本 = 手续费 + 基础滑点（+ 平方根冲击）。
    weight（第二十四阶段，默认 1.0 = 第二十三阶段原样）：买入时只投入当时净值的该比例，其余持有 USDT。return_nav：同时返回逐日净值。"""
    liq = {sym: liquidity_table(f[["ts", "open", "high", "low", "close", "volume"]])} if impact else {sym: liquidity_table(f[["ts", "open", "high", "low", "close", "volume"]]).assign(quote_vol=1e30, sigma=0.0)}
    L = make_ledger(initial, fee, slip, liq); op = f["open"].to_numpy(float); cl = f["close"].to_numpy(float); ts = f["ts"]; n = len(f)
    sigs = set(sig[(sig["signal"]) & (sig["bucket"] == bucket)]["i"].astype(int)); i0 = int(pd.DatetimeIndex(ts).searchsorted(pd.Timestamp(start, tz="UTC")))
    pos = None; nav = []; fills = []; trades = []
    for i in range(i0, n):
        if pos is not None and i == pos["exit_i"]:
            q = L.qty(sym); rec = L.sell(sym, q, op[i], ts.iloc[i], pos["reason"]); fills.append({"ts": ts.iloc[i], "side": "SELL", "qty": rec["qty"], "ref_price": op[i], "fill_price": rec["price"], "fee": rec["fee"], "slippage": (op[i] - rec["price"]) * rec["qty"]})
            trades.append(pos["t"] | {"exit_day": str(ts.iloc[i].date()), "exit_price": float(rec["price"]), "exit_reason": pos["reason"], "hold_days": i - pos["entry_i"], "proceeds": float(rec["qty"] * rec["price"] - rec["fee"])})
            trades[-1]["pnl_usdt"] = trades[-1]["proceeds"] - trades[-1]["cost_usdt"]; trades[-1]["ret"] = trades[-1]["proceeds"] / trades[-1]["cost_usdt"] - 1; pos = None
        if pos is None and (i - 1) in sigs and i - 1 >= i0:
            s = sig[sig["i"] == i - 1].iloc[0]; cash0 = L.cash; q = weight * cash0 * 0.999 / (op[i] * (1 + (slip + 100) / 1e4) * (1 + fee / 1e4))
            lot = L.buy(sym, q, op[i], ts.iloc[i], bucket, 0.0, 0.0, 0.0); fee_paid = L.trade_log[-1]["fee"]; cost = cash0 - L.cash
            fills.append({"ts": ts.iloc[i], "side": "BUY", "qty": lot.qty, "ref_price": op[i], "fill_price": lot.price, "fee": fee_paid, "slippage": (lot.price - op[i]) * lot.qty})
            if exit_ in HOLD:
                ex_i, reason = i + HOLD[exit_], f"持有 {HOLD[exit_]} 天到期"
            else:
                k = next((k for k in range(i, n - 1) if f["w_tf"].iloc[k]), None); ex_i, reason = (k + 1 if k is not None else n + 10), "周线趋势失效"
            pos = {"entry_i": i, "exit_i": ex_i, "reason": reason, "t": {"symbol": sym, "bucket": bucket, "exit_rule": exit_, "signal_day": str(s["ts"].date()), "entry_day": str(ts.iloc[i].date()), "entry_price": float(lot.price), "high60": float(s["H"]), "depth": float(s["depth"]), "discount_vs_high60": float(1 - op[i] / s["H"]), "qty": float(lot.qty), "cost_usdt": float(cost)}}
        q = L.qty(sym); eq = L.cash + q * cl[i]
        nav.append({"ts": ts.iloc[i], "equity": eq, "liquidation": L.cash + q * cl[i] * (1 - L.base_slip / 1e4) * (1 - L.fee_bps / 1e4), "exposure": q * cl[i] / eq if eq > 0 else 0.0, "holdings": {sym: q}, "closes": {sym: cl[i]}})
    if pos is not None:
        trades.append(pos["t"] | {"exit_day": None, "exit_price": None, "exit_reason": "仍持有（期末按清仓口径估值）", "hold_days": n - 1 - pos["entry_i"]})
    navdf = pd.DataFrame(nav); res = HoldResult(f"{sym}|{bucket}|{exit_}", {}, L.fee_bps, L.base_slip, initial, navdf, pd.DataFrame(), pd.DataFrame(fills), {})
    fin = final_liquidation_with_impact(L, {sym: cl[-1]}, ts.iloc[-1]); m = metrics(res, L, fin)
    closed = [t for t in trades if t.get("exit_day")]
    m |= {"n_trades": len(trades), "win_rate_trades": float(np.mean([t["ret"] > 0 for t in closed])) if closed else None, "avg_hold_days": float(np.mean([t["hold_days"] for t in trades])) if trades else None,
          "time_in_market": float((navdf["exposure"] > 0.01).mean())}
    return (m, trades, navdf) if return_nav else (m, trades)


def zigzag_phases(f: pd.DataFrame, thr: float = 0.5) -> tuple[pd.Series, list]:
    """事后划分（只用于描述，不用于交易）：从高点下跌 ≥ 50% 或从低点上涨 ≥ 50% 确认一个转折点；转折点之间上升段 = 牛市，下跌段 = 熊市。"""
    c = f["close"].to_numpy(float); n = len(c); piv = [0]; trend = None; hi = lo = 0
    for i in range(1, n):
        if c[i] > c[hi]:
            hi = i
        if c[i] < c[lo]:
            lo = i
        if trend in (None, "up") and c[i] <= c[hi] * (1 - thr):
            piv.append(hi); trend = "down"; lo = i
        elif trend in (None, "down") and c[i] >= c[lo] * (1 + thr):
            piv.append(lo); trend = "up"; hi = i
    piv = sorted(set(piv + [n - 1])); lab = np.empty(n, dtype=object)
    for a, b in zip(piv[:-1], piv[1:]):
        lab[a:b + 1] = "牛市" if c[b] >= c[a] else "熊市"
    return pd.Series(lab, index=f.index), [str(f["ts"].iloc[k].date()) for k in piv]
