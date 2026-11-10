"""B1（BTC SMA200）、B3（ETH SMA200）、B2（模型 B + 熔断）错过机会分析（预登记 §2）。
日线复现（与 Mac 账户 A / REF / B 同规则；单边 15 bps），只读：不修改任何策略参数或规则。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg
from ..holding.data import load_dataset
from ..holding.engine import HoldingBacktester
from ..pa16 import data16

FEE, SLIP = 10.0, 5.0
CLASSES = ("合理避险", "保护有效但回补更贵", "短期来回", "不必要的长期空仓", "进行中")


def run(name: str, start: str, end: str | None = None):
    c = cfg()
    if name == "B1_2014":
        d = data16.load_btc_daily(); bars = {"BTCUSDT": d[["ts", "open", "high", "low", "close", "volume"]]}
        return HoldingBacktester(bars, {"name": name, "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, FEE, SLIP, start, end, sma=200).run(), bars
    bars = load_dataset(c)
    spec = {"B1": {"assets": ["BTCUSDT"], "mode": "trend"}, "B3": {"assets": ["ETHUSDT"], "mode": "trend"}, "B2": {"assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_mode": "state_machine"}}[name]
    return HoldingBacktester(bars, {"name": name, **spec, "risk_exit_dd": None}, FEE, SLIP, start, end, sma=200, risk_params=c.get("trendsys", {}).get("risk", {})).run(), bars


def flat_episodes(res, bars: dict) -> list[dict]:
    nav = res.nav; fills = res.fills.copy(); fills["ts"] = pd.to_datetime(fills["ts"], utc=True); out = []
    for sym in res.spec["assets"]:
        b = bars[sym].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); px = b.set_index("ts")["close"].astype(float)
        held = nav.set_index("ts")["holdings"].apply(lambda h: h.get(sym, 0.0) > 1e-12)
        prev = False; start = None
        for t, h in held.items():
            if prev and not h:
                start = t
            if (not prev) and h and start is not None:
                out.append(_episode(sym, start, t, fills, px)); start = None
            prev = h
        if start is not None:
            out.append(_episode(sym, start, None, fills, px))
    return out


def _episode(sym, t0, t1, fills, px) -> dict:
    s = fills[(fills["symbol"] == sym) & (fills["side"] == "SELL") & (fills["ts"] == t0)]; exit_px = float((s["fill_price"] * s["qty"]).sum() / s["qty"].sum()); exit_val = float((s["fill_price"] * s["qty"]).sum())
    if t1 is not None:
        bb = fills[(fills["symbol"] == sym) & (fills["side"] == "BUY") & (fills["ts"] == t1)]; re_px = float((bb["fill_price"] * bb["qty"]).sum() / bb["qty"].sum())
    else:
        re_px = float(px.iloc[-1])
    seg = px[(px.index >= t0) & (px.index <= (t1 if t1 is not None else px.index[-1]))]
    dd = float(seg.min() / exit_px - 1); up = float(seg.max() / exit_px - 1); days = int(((t1 if t1 is not None else px.index[-1]) - t0).days)
    missed = exit_val * (re_px / exit_px - 1)
    if t1 is None:
        cls = "进行中"
    elif re_px <= exit_px:
        cls = "合理避险"
    elif dd <= -0.20:
        cls = "保护有效但回补更贵"
    elif days <= 30:
        cls = "短期来回"
    else:
        cls = "不必要的长期空仓"
    return {"symbol": sym, "exit_date": str(t0.date()), "reentry_date": None if t1 is None else str(t1.date()), "days_flat": days, "exit_price": exit_px, "reentry_price": re_px, "reentry_over_exit": re_px / exit_px - 1,
            "max_drawdown_while_flat": dd, "max_gain_while_flat": up, "exit_value_usdt": exit_val, "missed_usdt_if_positive": missed, "class": cls}


def participation(res, bars: dict) -> dict:
    nav = res.nav.set_index("ts"); sr = nav["liquidation"].pct_change().dropna()
    b = bars["BTCUSDT"].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); br = b.set_index("ts")["close"].astype(float).pct_change().reindex(sr.index)
    up, dn = br > 0, br < 0
    out = {"vs_BTC": {"up_capture": float(sr[up].sum() / br[up].sum()), "down_capture": float(sr[dn].sum() / br[dn].sum()), "time_in_market": float((nav["exposure"] > 0.01).mean())}}
    if "ETHUSDT" in bars and len(res.spec["assets"]) == 2:
        e = bars["ETHUSDT"].copy(); e["ts"] = pd.to_datetime(e["ts"], utc=True); er = e.set_index("ts")["close"].astype(float).pct_change().reindex(sr.index); mix = 0.5 * br + 0.5 * er
        out["vs_5050"] = {"up_capture": float(sr[mix > 0].sum() / mix[mix > 0].sum()), "down_capture": float(sr[mix < 0].sum() / mix[mix < 0].sum())}
    yr = {}
    bc = b.set_index("ts")["close"].astype(float)
    for y, g in nav["liquidation"].groupby(nav.index.year):
        prev = nav["liquidation"][nav.index < g.index[0]]; base = float(prev.iloc[-1]) if len(prev) else float(res.initial)
        bp = bc[bc.index < g.index[0]]; bbase = float(bp.iloc[-1]) if len(bp) else float(bc.loc[g.index[0]])
        yr[str(y)] = {"strategy": float(g.iloc[-1] / base - 1), "btc": float(bc.loc[g.index[-1]] / bbase - 1)}
    out["yearly"] = yr
    return out


def analyze(name: str, start: str, end: str | None = None) -> dict:
    res, bars = run(name, start, end); eps = flat_episodes(res, bars)
    summ = {}
    for cls in CLASSES:
        sel = [e for e in eps if e["class"] == cls]
        summ[cls] = {"n": len(sel), "days_flat_total": int(sum(e["days_flat"] for e in sel)), "missed_usdt_sum": float(sum(e["missed_usdt_if_positive"] for e in sel))}
    f = res.final
    return {"name": name, "start": start, "end": str(res.nav["ts"].iloc[-1].date()), "liquidation_net_profit_usdt": f["liquidation_net_profit_usdt"], "fees_plus_slippage_usdt": f["fees_usdt"] + f["slippage_usdt"], "n_fills": f["n_fills"],
            "max_drawdown": float((res.nav["liquidation"] / res.nav["liquidation"].cummax() - 1).min()), "episodes": eps, "by_class": summ, "participation": participation(res, bars),
            "note": "missed_usdt = 卖出时持仓价值 × (回补价 / 卖出价 − 1)；正数 = 错失，负数 = 节省；是近似值，未计复利"}
