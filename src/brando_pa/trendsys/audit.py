"""SMA200 基准审计：用与第五阶段完全独立的日线级实现复算，并逐项核对
  (1) 200 日预热（不足 200 根无信号）；(2) 信号在日线收盘产生、在其后第一根执行 K 线开盘成交；(3) 现金/持仓不为负、权益恒等式；
  (4) 手续费与净值计算；(5) 持仓期间的回撤；(6) 无未来数据（截断一致性 + 若用“当日”收盘而非“已收盘”会怎样）。
目标：复现第五阶段 BTC +232% / ETH +114%（full_1h、零成本）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..trend.engine import sma200_benchmark
from .engine import TrendSystemBacktester


def independent_sma_sim(d1: pd.DataFrame, ex: pd.DataFrame, start, end, initial: float, sma: int = 200, fee_bps: float = 0.0, slip_bps: float = 0.0, cash_buffer: float = 0.001, shift: int = 0) -> dict:
    """纯 numpy 的日线信号 + 执行 K 线成交复算。shift=1 表示（错误地）使用尚未收盘的下一根日线，用于演示未来数据的影响。"""
    d1 = d1.reset_index(drop=True); c = d1["close"].to_numpy(float)
    s = pd.Series(c).rolling(sma).mean().to_numpy()
    sig = (c > s) & np.isfinite(s)
    d_close_ns = (pd.to_datetime(d1["ts"], utc=True) + pd.Timedelta(days=1)).dt.tz_convert(None).to_numpy().astype("datetime64[ns]").astype("int64")
    ex = ex.reset_index(drop=True); ets = pd.to_datetime(ex["ts"], utc=True)
    bar_ns = int((ets.iloc[1] - ets.iloc[0]).value)
    e_close_ns = ets.dt.tz_convert(None).to_numpy().astype("datetime64[ns]").astype("int64") + bar_ns; o = ex["open"].to_numpy(float); cl = ex["close"].to_numpy(float)
    m = np.ones(len(ex), bool)
    if start is not None:
        m &= (ets >= pd.Timestamp(start, tz="UTC")).to_numpy()
    if end is not None:
        m &= (ets <= pd.Timestamp(end, tz="UTC")).to_numpy()
    sel = np.flatnonzero(m)
    # 每根执行 K 线收盘时刻对应的最后一根已收盘日线
    jj = np.searchsorted(d_close_ns, e_close_ns, side="right") - 1
    cash = initial; qty = 0.0; fees = 0.0; fills = []; eq = np.empty(len(sel)); want_prev = None
    first_signal_day = int(np.argmax(np.isfinite(s))) if np.isfinite(s).any() else None
    for k, i in enumerate(sel):
        j = int(jj[i]) + shift
        want = bool(sig[j]) if 0 <= j < len(sig) else False
        sday = str(d1["ts"].iloc[min(max(j, 0), len(d1) - 1)])[:10]
        if want_prev is not None and want_prev != (qty > 0):
            if want_prev:
                px = o[i] * (1 + slip_bps / 1e4); q = cash * (1 - cash_buffer) / (px * (1 + fee_bps / 1e4))
                if q * o[i] >= 10:
                    fee = px * q * fee_bps / 1e4; cash -= px * q + fee; fees += fee; qty = q
                    fills.append({"ts": ets.iloc[i], "side": "BUY", "price": px, "qty": q, "fee": fee, "signal_day": sday})
            else:
                px = o[i] * (1 - slip_bps / 1e4); fee = px * qty * fee_bps / 1e4; cash += px * qty - fee; fees += fee
                fills.append({"ts": ets.iloc[i], "side": "SELL", "price": px, "qty": qty, "fee": fee, "signal_day": sday}); qty = 0.0
        want_prev = want
        eq[k] = cash + qty * cl[i]
        assert cash >= -1e-9 and qty >= 0
    return {"equity": pd.DataFrame({"ts": ets.iloc[sel].to_numpy(), "equity": eq}), "fills": pd.DataFrame(fills), "fees": fees, "final_cash": cash, "final_qty": qty,
            "first_sma_day": str(d1["ts"].iloc[first_signal_day])[:10] if first_signal_day is not None else None, "n_daily_bars_before_first_sma": first_signal_day}


def hold_drawdowns(fills: pd.DataFrame, close: pd.Series) -> list[dict]:
    """每段持仓期间标的的最大回撤（相对持仓期内最高价）与相对买入价的最大不利偏移。"""
    out = []
    if fills is None or fills.empty:
        return out
    f = fills.sort_values("ts").reset_index(drop=True); px = close.copy(); px.index = pd.to_datetime(px.index, utc=True)
    buy = None
    for _, r in f.iterrows():
        if r["side"] == "BUY":
            buy = r
        elif r["side"] == "SELL" and buy is not None:
            seg = px[(px.index >= pd.Timestamp(buy["ts"])) & (px.index <= pd.Timestamp(r["ts"]))]
            if len(seg):
                out.append({"entry_ts": str(buy["ts"])[:16], "exit_ts": str(r["ts"])[:16], "days": (pd.Timestamp(r["ts"]) - pd.Timestamp(buy["ts"])).total_seconds() / 86400, "ret": float(r["price"] / buy["price"] - 1), "max_dd_in_hold": float((seg / seg.cummax() - 1).min()), "mae_vs_entry": float(seg.min() / float(buy["price"]) - 1)})
            buy = None
    if buy is not None:
        seg = px[px.index >= pd.Timestamp(buy["ts"])]
        out.append({"entry_ts": str(buy["ts"])[:16], "exit_ts": "open", "days": (seg.index[-1] - pd.Timestamp(buy["ts"])).total_seconds() / 86400, "ret": float(seg.iloc[-1] / buy["price"] - 1), "max_dd_in_hold": float((seg / seg.cummax() - 1).min()), "mae_vs_entry": float(seg.min() / float(buy["price"]) - 1)})
    return out


def audit_symbol(mc, c: dict, start, end, initial: float, fee_bps: float = 0.0, slip_bps: float = 0.0, expected_return: float | None = None) -> dict:
    sma = int(c["trendsys"]["trend"]["sma"])
    eq5, f5 = sma200_benchmark(mc, start, end, initial, sma, fee_bps, slip_bps)
    ind = independent_sma_sim(mc.bars["1d"], mc.ex, start, end, initial, sma, fee_bps, slip_bps)
    r6 = TrendSystemBacktester("A_sma200", "none", c, [mc.symbol], mc.exec_tf, start, end, fee_bps, slip_bps).run({mc.symbol: mc})
    e5 = eq5["equity"].to_numpy(float); ei = ind["equity"]["equity"].to_numpy(float); e6 = r6.equity["equity"].to_numpy(float)
    out = {"symbol": mc.symbol, "window": [str(start), str(end)], "fee_bps": fee_bps, "slip_bps": slip_bps,
           "phase5_benchmark": {"total_return": float(e5[-1] / initial - 1), "n_buys": int((f5["side"] == "BUY").sum()) if not f5.empty else 0, "max_drawdown": float((eq5["equity"] / eq5["equity"].cummax() - 1).min())},
           "independent_reimpl": {"total_return": float(ei[-1] / initial - 1), "n_buys": int((ind["fills"]["side"] == "BUY").sum()) if not ind["fills"].empty else 0, "fees_paid": ind["fees"]},
           "trendsys_A_none": {"total_return": float(e6[-1] / initial - 1), "n_buys": int((r6.fills["side"] == "BUY").sum()) if not r6.fills.empty else 0, "fees_paid": r6.final["fees_paid"]},
           "max_abs_rel_diff_phase5_vs_independent": float(np.max(np.abs(e5 / ei - 1))) if len(e5) == len(ei) else None,
           "max_abs_rel_diff_phase5_vs_trendsys": float(np.max(np.abs(e5 / e6 - 1))) if len(e5) == len(e6) else None,
           "same_length": bool(len(e5) == len(ei) == len(e6))}
    if expected_return is not None:
        out["expected_total_return_from_phase5_report"] = expected_return
        out["reproduced"] = bool(abs(out["phase5_benchmark"]["total_return"] - expected_return) < 0.005)
    # (1) 预热
    d1 = mc.bars["1d"]
    out["warmup"] = {"first_sma_day": ind["first_sma_day"], "daily_bars_before_first_sma": ind["n_daily_bars_before_first_sma"], "first_fill": str(ind["fills"]["ts"].iloc[0]) if not ind["fills"].empty else None,
                     "ok": bool(ind["fills"].empty or pd.Timestamp(ind["fills"]["ts"].iloc[0]) > pd.Timestamp(ind["first_sma_day"], tz="UTC"))}
    # (2) 成交时点：每笔成交都在日线收盘后的第一根执行 K 线开盘（UTC 00:00 开盘的 K 线）
    if not f5.empty:
        fts = pd.to_datetime(f5["ts"], utc=True)
        out["fill_timing"] = {"all_fills_at_first_bar_after_daily_close": bool(((fts.dt.hour == 0) & (fts.dt.minute == 0)).all()), "n_fills": int(len(f5)),
                              "fill_ts_sample": [str(x) for x in fts.head(3)], "signal_day_sample": list(ind["fills"]["signal_day"].head(3)) if not ind["fills"].empty else []}
        # 成交价 = 该根开盘价（零成本时）
        o_map = mc.ex.set_index("ts")["open"]
        out["fill_timing"]["fill_price_equals_next_open"] = bool(np.allclose(f5["price"].to_numpy(float), o_map.reindex(fts).to_numpy(float) * np.where(f5["side"] == "BUY", 1 + slip_bps / 1e4, 1 - slip_bps / 1e4), rtol=1e-9))
    # (3) 账本约束与恒等式
    e = r6.equity
    out["ledger"] = {"cash_min": float(e["cash"].min()), "qty_min": float(e[f"qty_{mc.symbol}"].min()), "exposure_max": float(e["exposure"].max()),
                     "equity_identity_max_err": float(np.max(np.abs(e["cash"] + e[f"qty_{mc.symbol}"] * mc.ex.set_index("ts")["close"].reindex(pd.to_datetime(e["ts"], utc=True)).to_numpy(float) - e["equity"])))}
    # (4) 手续费核对：Σ 成交额 × bps
    if not r6.fills.empty:
        notional = (r6.fills["price"] * r6.fills["qty"]).sum()
        out["fees"] = {"fees_paid": r6.final["fees_paid"], "expected": float(notional * fee_bps / 1e4), "ok": bool(abs(r6.final["fees_paid"] - notional * fee_bps / 1e4) < 1e-6)}
    # (5) 持仓期间回撤
    hd = hold_drawdowns(f5, mc.ex.set_index("ts")["close"])
    if hd:
        h = pd.DataFrame(hd)
        out["hold_segments"] = {"n": int(len(h)), "worst_max_dd_in_hold": float(h["max_dd_in_hold"].min()), "median_max_dd_in_hold": float(h["max_dd_in_hold"].median()), "worst_mae_vs_entry": float(h["mae_vs_entry"].min()), "share_segments_negative": float((h["ret"] < 0).mean()), "avg_days": float(h["days"].mean()), "segments": hd}
    # (6) 无未来数据：截断末尾 120 根日线不改变此前任何信号；用“当日”收盘（shift=1）会改变结果
    full_sig = independent_sma_sim(d1, mc.ex, start, end, initial, sma)["fills"]
    cut = len(d1) - 120; d1c = d1.iloc[:cut]; end_c = pd.Timestamp(d1c["ts"].iloc[-1], tz=None)
    exc = mc.ex[pd.to_datetime(mc.ex["ts"], utc=True) < pd.Timestamp(d1c["ts"].iloc[-1]).tz_convert("UTC") + pd.Timedelta(days=1)]
    trunc = independent_sma_sim(d1c, exc, start, None, initial, sma)["fills"]
    nf = len(trunc)
    same = bool(nf <= len(full_sig) and (full_sig.head(nf)[["ts", "side"]].reset_index(drop=True).astype(str) == trunc[["ts", "side"]].reset_index(drop=True).astype(str)).all().all()) if nf else True
    la = independent_sma_sim(d1, mc.ex, start, end, initial, sma, shift=1)
    out["lookahead"] = {"truncation_identical": same, "n_fills_truncated": nf, "if_used_unclosed_daily_bar_total_return": float(la["equity"]["equity"].iloc[-1] / initial - 1), "cash_buffer_uninvested": 0.001}
    out["equity_phase5"] = eq5; out["fills_phase5"] = f5; out["trendsys_result"] = r6
    return out
