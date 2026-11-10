"""第二十一阶段独立前瞻观察账本（只读研究记录；不下单；不改动 Mac 账户）。

每次运行：校验冻结清单 → 更新独立行情仓（只追加已收盘日线）→ 从观察起点按冻结规则重放四个账户 → 只追加尚未记录的日期（哈希链）
→ 计算 35/40/45/50% 研究性回撤警报（只记录，不交易）→ 写 state.json。已记录日期若重放结果不同，记录 replay_mismatch，不改写历史。
用法（手动单次运行）：PYTHONPATH=src python3 -m brando_pa.pa21.forward21 [--no-fetch]"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg, own_data_dir
from ..data.sources import utcnow
from ..data.store import BarStore
from ..holding.engine import HoldingBacktester
from ..pa18 import phase18
from ..pa20.alloc20 import AllocBacktester
from ..pa20.execution20 import CAP_BPS, FALLBACK_BPS, Y, attach, liquidity_table
from ..trendsys.paper import _append, verify_chain
from . import ACCOUNTS, BARS_SUBDIR, FEED_SUBDIR, FORWARD_START, OUT_DIR, RESEARCH_INITIAL_USDT, SEED_DIR, WARN
from . import freeze21

FEE, SLIP = 10.0, 5.0
SYMS = ("BTCUSDT", "ETHUSDT")
REL_TOL = 1e-9


def default_store(c=None) -> BarStore:
    return BarStore(root=own_data_dir(c) / BARS_SUBDIR, fallback_root=None)


def load_bars(store: BarStore, now: pd.Timestamp | None = None) -> dict:
    """已收盘日线（ts + 1 天 ≤ now）。"""
    out = {}
    for s in SYMS:
        d = store.load(s, "1d", allow_fallback=False).copy()
        if d.empty:
            return {}
        d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.normalize()
        if now is not None:
            d = d[d["ts"] + pd.Timedelta(days=1) <= now]
        keep = ["ts", "open", "high", "low", "close", "volume"] + (["source"] if "source" in d else [])
        out[s] = d[keep].drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    last = min(out[s]["ts"].max() for s in SYMS)
    return {s: v[v["ts"] <= last].reset_index(drop=True) for s, v in out.items()}


def simulate(bars: dict, c: dict, start: str = FORWARD_START, end: str | None = None, initial: float = RESEARCH_INITIAL_USDT) -> dict | None:
    """四个账户在同一行情、同一起点、同一成本与冲击模型下重放。起点之后没有已收盘日 → None。"""
    btc, eth = bars["BTCUSDT"], bars["ETHUSDT"]
    if btc.empty or btc["ts"].max() < pd.Timestamp(start, tz="UTC"):
        return None
    liq = {"BTCUSDT": liquidity_table(btc), "ETHUSDT": liquidity_table(eth)}; lb = {"BTCUSDT": liq["BTCUSDT"]}; b = end or str(btc["ts"].max().date()); px = {"BTCUSDT": btc[["ts", "open", "high", "low", "close", "volume"]], "ETHUSDT": eth[["ts", "open", "high", "low", "close", "volume"]]}
    out = {}
    bt = AllocBacktester(px["BTCUSDT"], 0.25, "Y", FEE, SLIP, start, b, initial, lb); out["FIX25_Y"] = (bt.run(), bt.L)
    bt, res = phase18.run_variant(px, "V0_original", 15, start, b, c, initial=initial, hook=lambda x: attach(x, liq)); out["B2_original"] = (res, bt.L)
    for name, spec in (("SMA200", {"name": "SMA200", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}), ("BH_BTC", {"name": "BH_BTC", "assets": ["BTCUSDT"], "mode": "buyhold"})):
        bt = attach(HoldingBacktester({"BTCUSDT": px["BTCUSDT"]}, spec, FEE, SLIP, start, b, initial=initial, sma=200), lb); out[name] = (bt.run(), bt.L)
    return {"accounts": out, "liq": liq}


def _imp_bps(tab: pd.DataFrame, t: pd.Timestamp, notional: float) -> float:
    if t in tab.index:
        r = tab.loc[t]
        if np.isfinite(r["quote_vol"]) and np.isfinite(r["sigma"]) and r["quote_vol"] > 0:
            return min(CAP_BPS, 1e4 * Y * float(r["sigma"]) * math.sqrt(notional / float(r["quote_vol"])))
    return FALLBACK_BPS


def daily_rows(sim: dict, bars: dict, initial: float = RESEARCH_INITIAL_USDT) -> list[dict]:
    """每个已收盘日一条：四个账户的仓位、余额、权益、清仓净值（含当日冲击）、高水位、回撤、费用、相对收益、预警等级。"""
    close = {s: bars[s].set_index("ts")["close"].astype(float) for s in SYMS}; src = bars["BTCUSDT"].set_index("ts")["source"] if "source" in bars["BTCUSDT"] else None
    per = {}
    for name, (res, L) in sim["accounts"].items():
        nav = res.nav.copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True); f = res.fills.copy()
        if len(f):
            f["ts"] = pd.to_datetime(f["ts"], utc=True)
        rows = []; hwm = initial; mdd = 0.0
        for _, r in nav.iterrows():
            t = r["ts"]; h = {s: float(q) for s, q in (r["holdings"] or {}).items()}; cl = {s: float(close[s].get(t, np.nan)) for s in h}
            pos_val = sum(h[s] * cl[s] for s in h); eq = float(r["equity"]); cash = eq - pos_val
            liqv = cash + sum(h[s] * cl[s] * (1 - (L.base_slip + _imp_bps(sim["liq"][s], t, h[s] * cl[s])) / 1e4) * (1 - L.fee_bps / 1e4) for s in h if h[s] > 0)
            hwm = max(hwm, liqv); dd = liqv / hwm - 1; mdd = min(mdd, dd)
            ff = f[f["ts"] <= t] if len(f) else f
            rows.append({"date": str(t.date()), "btc_qty": h.get("BTCUSDT", 0.0), "eth_qty": h.get("ETHUSDT", 0.0) if name == "B2_original" else None, "usdt": cash, "equity": eq, "liquidation": liqv,
                         "btc_weight": (h.get("BTCUSDT", 0.0) * cl.get("BTCUSDT", 0.0)) / eq if eq > 0 else 0.0, "hwm": hwm, "drawdown": dd, "max_drawdown": mdd,
                         "fees_cum": float(ff["fee"].sum()) if len(ff) else 0.0, "slippage_impact_cum": float(ff["slippage"].sum()) if len(ff) else 0.0, "n_fills_cum": int(len(ff)),
                         "return": liqv / initial - 1, "warn_level": max([int(w * 100) for w in WARN if dd <= -w], default=0)})
        per[name] = rows
    out = []
    for k in range(len(per["FIX25_Y"])):
        d = per["FIX25_Y"][k]["date"]; t = pd.Timestamp(d, tz="UTC"); acc = {n: per[n][k] for n in ACCOUNTS}
        assert all(acc[n]["date"] == d for n in ACCOUNTS), "账户日期不一致"
        rel = {n: {f"vs_{m}": acc[n]["return"] - acc[m]["return"] for m in ACCOUNTS if m != n} for n in ACCOUNTS}
        out.append({"date": d, "btc_close": float(close["BTCUSDT"][t]), "eth_close": float(close["ETHUSDT"][t]), "data_source": (str(src[t]) if src is not None else None), "accounts": acc, "relative_return": rel})
    return out


def alert_events(rows: list[dict]) -> list[dict]:
    """回撤首次跌破 35/40/45/50% 各一条；回到高水位后重新计数。只记录，不交易。"""
    ev = []
    for n in ACCOUNTS:
        fired = set()
        for r in rows:
            a = r["accounts"][n]
            if a["drawdown"] >= -1e-12:
                fired = set()
            for w in WARN:
                if a["drawdown"] <= -w and w not in fired:
                    fired.add(w); ev.append({"date": r["date"], "account": n, "threshold": w, "drawdown": a["drawdown"], "liquidation": a["liquidation"], "hwm": a["hwm"],
                                             "kind": "drawdown_budget_breach" if w >= 0.50 else "drawdown_warning", "action": "仅记录研究性警报；不触发任何交易"})
    return ev


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def run_once(c: dict | None = None, now: pd.Timestamp | None = None, store: BarStore | None = None, out_dir: Path | None = None, fetch: bool = True, feed_dir: Path | None = None, sources: list | None = None) -> dict:
    c = c or cfg(); now = now or utcnow(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); store = store or default_store(c)
    P = {"daily": out / "daily_ledger.jsonl", "alerts": out / "alerts.jsonl", "events": out / "events.jsonl", "state": out / "state.json"}
    st = {"generated_at_utc": str(now), "forward_start": FORWARD_START, "initial_usdt_virtual": RESEARCH_INITIAL_USDT, "is_real_asset": False, "real_trading": "disabled (no implementation)", "auto_trading": False}
    fz = freeze21.verify(c=c); st["freeze"] = fz
    if not fz["ok"]:
        _append(P["events"], {"kind": "frozen_rule_violation", "detail": fz}); st["status"] = "frozen_rule_violation"; atomic_write_json(P["state"], st, indent=1, default=str); return st
    if fetch:
        from ..forward.datafeed import MarketDataService
        svc = MarketDataService(c, store=store, log_dir=Path(feed_dir or own_data_dir(c) / FEED_SUBDIR), seed_from=BarStore(root=SEED_DIR, fallback_root=None), sources=sources)
        fs = svc.update(list(SYMS), ["1d"], now=now, lookback_days=10)
        st["feed"] = {"ok": fs.ok, "source": fs.source, "stale": fs.stale, "quality_fault": fs.quality_fault, "quality_note": fs.quality_note, "errors": fs.errors[-3:], "per_tf": fs.per_tf}
        if not fs.ok or fs.quality_fault:
            _append(P["events"], {"kind": "data_unavailable" if not fs.ok else "data_quality_fault", "detail": st["feed"]})
    bars = load_bars(store, now)
    if not bars:
        st["status"] = "no_data"; atomic_write_json(P["state"], st, indent=1, default=str); return st
    st["latest_closed_day"] = str(bars["BTCUSDT"]["ts"].max().date())
    if st.get("feed", {}).get("quality_fault"):
        st["status"] = "paused_data_quality"; atomic_write_json(P["state"], st, indent=1, default=str); return st
    sim = simulate(bars, c)
    if sim is None:
        st["status"] = "awaiting_first_close"; st["n_days"] = 0; st["note"] = f"观察起点 {FORWARD_START} 的日线尚未收盘：前瞻观察 0 天"
        st["chains"] = {k: verify_chain(P[k]) for k in ("daily", "alerts", "events")}; atomic_write_json(P["state"], st, indent=1, default=str); return st
    rows = daily_rows(sim, bars); have = {r["date"]: r for r in _read(P["daily"]) if r.get("kind") == "daily"}
    logged_mm = {(e["date"], e["account"]) for e in _read(P["events"]) if e.get("kind") == "replay_mismatch"}; added = []
    for r in rows:
        if r["date"] in have:
            for n in ACCOUNTS:
                a0, a1 = have[r["date"]]["accounts"][n]["liquidation"], r["accounts"][n]["liquidation"]
                if abs(a1 / a0 - 1) > REL_TOL and (r["date"], n) not in logged_mm:
                    _append(P["events"], {"kind": "replay_mismatch", "date": r["date"], "account": n, "logged": a0, "replayed": a1, "note": "已记录的历史不改写"})
            continue
        catch = now > pd.Timestamp(r["date"], tz="UTC") + pd.Timedelta(days=1, hours=36)
        _append(P["daily"], {"kind": "daily", **r, "catch_up": bool(catch)}); added.append(r["date"])
    have_a = {(e["date"], e["account"], e["threshold"]) for e in _read(P["alerts"])}
    for e in alert_events(rows):
        if (e["date"], e["account"], e["threshold"]) not in have_a and e["date"] in added:
            _append(P["alerts"], e)
    last = rows[-1]
    st |= {"status": "observing", "n_days": len(rows), "first_day": rows[0]["date"], "last_day": last["date"], "added_days": added, "latest": last,
           "open_alerts": {n: last["accounts"][n]["warn_level"] for n in ACCOUNTS}, "chains": {k: verify_chain(P[k]) for k in ("daily", "alerts", "events")}}
    atomic_write_json(P["state"], st, indent=1, default=str); return st


if __name__ == "__main__":
    import sys
    s = run_once(fetch="--no-fetch" not in sys.argv)
    print(json.dumps({k: s.get(k) for k in ("status", "latest_closed_day", "n_days", "added_days", "open_alerts", "note")}, ensure_ascii=False, default=str))
