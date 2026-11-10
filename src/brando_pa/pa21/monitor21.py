"""第二十一阶段上线验收：仓位漂移监测与每日观察记录（预登记补充 A / C）。
只读取冻结账本（daily_ledger.jsonl）与独立行情仓，只追加写 drift.jsonl 与 observations.jsonl（哈希链）。不交易，不改冻结规则。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..data.store import BarStore
from ..trendsys.paper import _append
from . import ACCOUNTS, BUDGET, OUT_DIR, WARN

DRIFT_LEVELS = (0.50, 0.70, 0.85)
LIVE_SOURCES = {"okx_spot", "coinbase_exchange", "binance_spot", "bybit_spot"}
REALTIME_H, DELAYED_H = 6.0, 36.0


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def drop_to_hit(weight: float, dd_now: float, target: float) -> float | None:
    """组合当前回撤 dd_now（≤ 0）、BTC 仓位 weight 时，BTC 再一次性下跌多少会使组合回撤达到 −target（相对历史高水位）。None = BTC 归零也达不到。"""
    if weight <= 0:
        return None
    x = (1 - (1 - target) / (1 + dd_now)) / weight
    return max(0.0, x) if x <= 1 else None


def drift_risk_table(dd_now: float = 0.0) -> list[dict]:
    """25% / 50% / 70% / 85% 仓位下，BTC 再跌多少会触及各回撤线（静态算术，不是预测）。"""
    return [{"btc_weight": w, **{f"btc_drop_to_{int(t * 100)}pct": drop_to_hit(w, dd_now, t) for t in WARN}} for w in (0.25,) + DRIFT_LEVELS]


def drift_events(rows: list[dict]) -> list[dict]:
    ev = []; armed = {lv: True for lv in DRIFT_LEVELS}
    for r in rows:
        a = r["accounts"]["FIX25_Y"]; w = float(a["btc_weight"])
        for lv in DRIFT_LEVELS:
            if w < lv:
                armed[lv] = True
            elif armed[lv]:
                armed[lv] = False
                ev.append({"date": r["date"], "account": "FIX25_Y", "level": lv, "btc_weight": w, "btc_close": r["btc_close"], "drawdown": a["drawdown"],
                           "btc_drop_to": {f"{int(t * 100)}%": drop_to_hit(w, a["drawdown"], t) for t in WARN}, "action": "仅记录仓位漂移；不交易、不增加交易限制"})
    return ev


def classify(close_utc: pd.Timestamp, ingested: pd.Timestamp | None, source: str | None, tick: pd.Timestamp) -> str:
    if ingested is None or source not in LIVE_SOURCES:
        return "历史补记"
    lag = max((ingested - close_utc).total_seconds(), (tick - close_utc).total_seconds()) / 3600
    return "实时观察" if lag <= REALTIME_H else ("延迟记录" if lag <= DELAYED_H else "历史补记")


def update(now: pd.Timestamp, store: BarStore, out_dir: Path | None = None) -> dict:
    out = Path(out_dir or OUT_DIR); rows = [r for r in _read(out / "daily_ledger.jsonl") if r.get("kind") == "daily"]
    P = {"obs": out / "observations.jsonl", "drift": out / "drift.jsonl"}; done = {o["date"] for o in _read(P["obs"])}
    have_d = {(e["date"], e["level"]) for e in _read(P["drift"])}; new_obs = []
    dev = drift_events(rows)
    for e in dev:
        if (e["date"], e["level"]) not in have_d:
            _append(P["drift"], e)
    bars = store.load("BTCUSDT", "1d", allow_fallback=False)
    meta = {}
    if not bars.empty:
        ts = pd.to_datetime(bars["ts"], utc=True).dt.normalize(); src_s = bars["source"] if "source" in bars else pd.Series([None] * len(bars))
        ing_s = pd.to_datetime(bars["ingested_at"], utc=True, errors="coerce") if "ingested_at" in bars else pd.Series([pd.NaT] * len(bars))
        meta = {t: (s_, i_) for t, s_, i_ in zip(ts, src_s, ing_s)}
    events = _read(out / "events.jsonl"); alerts = _read(out / "alerts.jsonl")
    for r in rows:
        if r["date"] in done:
            continue
        t = pd.Timestamp(r["date"], tz="UTC"); close = t + pd.Timedelta(days=1); src, ing = meta.get(t, (None, None)); ing = None if ing is None or pd.isna(ing) else pd.Timestamp(ing)
        day_ev = [{"kind": e["kind"], **({"account": e.get("account")} if e.get("account") else {})} for e in events if e.get("date") == r["date"]]
        day_ev += [{"kind": a["kind"], "account": a["account"], "threshold": a["threshold"]} for a in alerts if a["date"] == r["date"]] + [{"kind": "drift", "level": e["level"]} for e in dev if e["date"] == r["date"]]
        rec = {"date": r["date"], "bar_close_utc": str(close), "bar_ingested_utc": None if ing is None else str(ing), "bar_source": src, "observed_at_tick_utc": str(now), "category": classify(close, ing, src, now),
               "btc_close": r["btc_close"], "accounts": {n: {k: r["accounts"][n][k] for k in ("btc_weight", "btc_qty", "usdt", "equity", "liquidation", "hwm", "drawdown", "max_drawdown", "fees_cum", "slippage_impact_cum")} for n in ACCOUNTS},
               "events": day_ev}
        _append(P["obs"], rec); new_obs.append(r["date"])
    obs = _read(P["obs"]); cats = pd.Series([o["category"] for o in obs]).value_counts().to_dict() if obs else {}
    last_w = rows[-1]["accounts"]["FIX25_Y"]["btc_weight"] if rows else None
    return {"new_observations": new_obs, "categories": cats, "fix25_btc_weight": last_w, "max_fix25_btc_weight": max([r["accounts"]["FIX25_Y"]["btc_weight"] for r in rows], default=None), "n_drift_events": len(dev)}


def drift_history(ctx=None) -> dict:
    """2014–2026 历史（已看过的数据）：FIX25_Y 规则下 BTC 仓位达到 50/70/85% 的天数、首次日期与随后 365 天组合最大回撤。"""
    from ..pa20 import phase20 as P20
    from ..pa20.alloc20 import AllocBacktester
    ctx = ctx or P20.Ctx(); nav = AllocBacktester(ctx.btc, 0.25, "Y", 10.0, 5.0, "2014-01-01", None, 100_000.0, ctx.liq_btc).run().nav.copy()
    nav["ts"] = pd.to_datetime(nav["ts"], utc=True); s = nav.set_index("ts"); w = s["exposure"].astype(float); liq = s["liquidation"].astype(float); dd = liq / liq.cummax() - 1; out = []
    for lv in DRIFT_LEVELS:
        hit = w[w >= lv]; eps = []
        if len(hit):
            starts = [hit.index[0]] + [t for p, t in zip(hit.index[:-1], hit.index[1:]) if (t - p).days > 30]      # 间隔 30 天以内视为同一段
            for t0 in starts:
                eps.append({"first_date": str(t0.date()), "btc_weight": float(w[t0]), "max_dd_next_365d_from_prior_peak": float(dd[t0:t0 + pd.Timedelta(days=365)].min())})
        out.append({"level": lv, "days": int(len(hit)), "episodes": eps})
    return {"category": "历史回测（已看过的数据，非前瞻）", "max_weight": float(w.max()), "max_weight_date": str(w.idxmax().date()), "levels": out}
