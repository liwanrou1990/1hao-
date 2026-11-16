"""第二十五阶段：关机币价（只计电费，鱼池收益页同口径）与触及事件的持币结果。参数按预登记固定。
数据：blockchain.info 公开图表 miners-revenue（美元，含手续费）、market-price（日均价）、hash-rate（TH/s）。只读，无任何交易接口。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase25_shutdown"
DATA_DIR = PROJECT_ROOT / "data_store" / "brando_pa" / "phase25_shutdown"
CHARTS = ("miners-revenue", "market-price", "hash-rate")
# 比特大陆旗舰：开始供货月份（公开资料近似，±2 个月）、能效 J/TH
MACHINES = [("Antminer S1", "2013-11-01", 2000.0), ("Antminer S3", "2014-07-01", 770.0), ("Antminer S5", "2014-12-01", 510.0), ("Antminer S7", "2015-09-01", 273.0),
            ("Antminer S9", "2016-06-01", 98.0), ("Antminer S17", "2019-04-01", 45.0), ("Antminer S19", "2020-05-01", 34.5), ("Antminer S19 XP", "2022-07-01", 21.5),
            ("Antminer S21", "2024-01-01", 17.5), ("Antminer S21 XP", "2024-12-01", 13.5)]
HALVINGS = [pd.Timestamp(x, tz="UTC") for x in ("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20")]
NEXT_HALVING_EST = pd.Timestamp("2028-04-15", tz="UTC")             # 约数，仅用于标注“未到期”
LAGS = (12, 6, 24); PRICES = (0.05, 0.03, 0.07); MAIN = (12, 0.05)
SIDE = 0.0015; GAP_DAYS = 180; SMOOTH = 7


def fetch(data_dir: Path = DATA_DIR) -> dict:
    import httpx
    data_dir.mkdir(parents=True, exist_ok=True); meta = {}
    for c in CHARTS:
        r = httpx.get(f"https://api.blockchain.info/charts/{c}", params={"timespan": "all", "format": "json", "sampled": "false"}, timeout=60); r.raise_for_status()
        (data_dir / f"{c}.json").write_bytes(r.content); meta[c] = {"bytes": len(r.content), "fetched_at_utc": str(pd.Timestamp.now(tz="UTC"))}
    (data_dir / "fetch_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8"); return meta


def load(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    s = {}
    for c in CHARTS:
        v = json.loads((data_dir / f"{c}.json").read_text())["values"]
        x = pd.Series({pd.Timestamp(e["x"], unit="s", tz="UTC").normalize(): float(e["y"]) for e in v}); s[c] = x[~x.index.duplicated(keep="last")].sort_index()
    idx = pd.date_range(max(x.index.min() for x in s.values()), min(x.index.max() for x in s.values()), freq="D")
    d = pd.DataFrame({k: v.reindex(idx) for k, v in s.items()}); d.index.name = "ts"
    d["price"] = d["market-price"].ffill(); d["rev_btc"] = d["miners-revenue"] / d["price"]
    d = d[d["price"] > 0]
    # 只用当日及以前：过去 7 天（含当日）平均
    d["rev_btc_7d"] = d["rev_btc"].rolling(SMOOTH, min_periods=4).mean(); d["hash_7d"] = d["hash-rate"].rolling(SMOOTH, min_periods=4).mean()
    d["btc_per_th_day"] = d["rev_btc_7d"] / d["hash_7d"]
    return d


def mainstream(ts: pd.Timestamp, lag_months: int) -> tuple | None:
    ok = [m for m in MACHINES if pd.Timestamp(m[1], tz="UTC") + pd.DateOffset(months=lag_months) <= ts]
    return ok[-1] if ok else None


def shutdown_price(btc_per_th_day: float, jt: float, usd_kwh: float) -> float:
    return jt * 24 / 1000 * usd_kwh / btc_per_th_day


def series(d: pd.DataFrame, lag: int, usd_kwh: float) -> pd.DataFrame:
    rows = []
    for ts, r in d.iterrows():
        m = mainstream(ts, lag)
        rows.append({"ts": ts, "machine": m[0] if m else None, "jt": m[2] if m else np.nan})
    x = pd.DataFrame(rows).set_index("ts"); x["price"] = d["price"]; x["btc_per_th_day"] = d["btc_per_th_day"]
    x["shutdown"] = x["jt"] * 24 / 1000 * usd_kwh / x["btc_per_th_day"]; x["touch"] = (x["price"] <= x["shutdown"]) & x["shutdown"].notna()
    return x[x["machine"].notna() & x["btc_per_th_day"].notna()]


def next_halving(ts: pd.Timestamp):
    for h in HALVINGS:
        if h > ts:
            return h
    return None


def outcome(price: pd.Series, entry_day: pd.Timestamp) -> dict:
    p0 = float(price[entry_day]) * (1 + SIDE); last = price.index[-1]; out = {"entry_day": str(entry_day.date()), "entry_price": float(price[entry_day])}
    e365 = entry_day + pd.Timedelta(days=365)
    if e365 <= last:
        w = price[entry_day:e365]; out |= {"ret_365": float(price[e365] * (1 - SIDE) / p0 - 1), "mae_365": float(w.min() / p0 - 1)}
    else:
        w = price[entry_day:]; out |= {"ret_365": None, "mae_365_so_far": float(w.min() / p0 - 1)}
    h = next_halving(entry_day)
    if h is not None and h <= last:
        out |= {"next_halving": str(h.date()), "ret_halving": float(price[h] * (1 - SIDE) / p0 - 1)}
    else:
        out |= {"next_halving": f"约 {NEXT_HALVING_EST.date()}（未到期）", "ret_halving": None}
    after = price[entry_day + pd.Timedelta(days=1):]; be = after[after * (1 - SIDE) >= p0]
    out["days_to_breakeven"] = int((be.index[0] - entry_day).days) if len(be) else None
    return out


def events(x: pd.DataFrame, price: pd.Series) -> list[dict]:
    tdays = x.index[x["touch"].to_numpy()]; ev = []; last = None
    for t in tdays:
        if last is None or (t - last).days > GAP_DAYS:
            ent = t + pd.Timedelta(days=1)
            if ent in price.index:
                ep = tdays[(tdays >= t)]; run_end = t
                for u in ep:
                    if (u - run_end).days > GAP_DAYS:
                        break
                    run_end = u
                ev.append({"touch_day": str(t.date()), "machine": x.loc[t, "machine"], "shutdown_price": float(x.loc[t, "shutdown"]), "touch_price": float(x.loc[t, "price"]),
                           "episode_last_touch": str(run_end.date()), "touch_days_in_episode": int(((tdays >= t) & (tdays <= run_end)).sum())} | outcome(price, ent))
        last = t
    return ev


def base_rates(price: pd.Series, start: pd.Timestamp) -> dict:
    p = price[price.index >= start]; last = price.index[-1]; r365 = []; rh = []
    for t in p.index:
        e = t + pd.Timedelta(days=365)
        if e <= last:
            r365.append(price[e] * (1 - SIDE) / (price[t] * (1 + SIDE)) - 1 > 0)
        h = next_halving(t)
        if h is not None and h <= last:
            rh.append(price[h] * (1 - SIDE) / (price[t] * (1 + SIDE)) - 1 > 0)
    return {"start": str(start.date()), "n_365": len(r365), "win_365": float(np.mean(r365)) if r365 else None, "n_halving": len(rh), "win_halving": float(np.mean(rh)) if rh else None}


def touch_day_stats(x: pd.DataFrame, price: pd.Series) -> dict:
    last = price.index[-1]; r = []
    for t in x.index[x["touch"].to_numpy()]:
        ent = t + pd.Timedelta(days=1); e = ent + pd.Timedelta(days=365)
        if ent in price.index and e <= last:
            r.append(price[e] * (1 - SIDE) / (price[ent] * (1 + SIDE)) - 1)
    return {"n_touch_days": int(x["touch"].sum()), "n_matured_365": len(r), "win_365": float(np.mean(np.array(r) > 0)) if r else None, "median_ret_365": float(np.median(r)) if r else None}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    ph = k / n; den = 1 + z * z / n; c = (ph + z * z / (2 * n)) / den; h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den; return float(c - h), float(c + h)


def judge(ev: list[dict], td: dict, base: dict) -> dict:
    m365 = [e["ret_365"] for e in ev if e["ret_365"] is not None]; mh = [e["ret_halving"] for e in ev if e["ret_halving"] is not None]
    w365 = sum(r > 0 for r in m365); wh = sum(r > 0 for r in mh)
    c1 = len(m365) >= 3; c2 = c1 and len(mh) >= 3 and w365 >= 2 / 3 * len(m365) and wh >= 2 / 3 * len(mh); c3 = td["win_365"] is not None and base["win_365"] is not None and td["win_365"] > base["win_365"]
    v = "证据不足（已到期独立事件少于 3 次）" if not c1 else ("支持，但样本很少、证据有限" if (c2 and c3) else "不支持")
    return {"matured_365": len(m365), "win_365": w365, "wilson_365": wilson(w365, len(m365)), "matured_halving": len(mh), "win_halving": wh, "wilson_halving": wilson(wh, len(mh)),
            "c1_events_ge3": c1, "c2_two_thirds": c2, "c3_beats_base": c3, "verdict": v}


def calibrate(d: pd.DataFrame, app_cny: float = 346183.47, jt: float = 13.5, cny_kwh: float = 0.50) -> dict:
    """截图（13.5 W/T、0.50 元/度 → 关机币价 ¥346,183）隐含的每 TH 每天产出 BTC，与本数据最新值比较；不依赖汇率。"""
    implied = jt * 24 / 1000 * cny_kwh / app_cny; ours = float(d["btc_per_th_day"].dropna().iloc[-1])
    return {"as_of": str(d["btc_per_th_day"].dropna().index[-1].date()), "app_implied_btc_per_th_day": implied, "ours_btc_per_th_day": ours, "diff": ours / implied - 1, "within_10pct": abs(ours / implied - 1) <= 0.10}


def run(out_dir: Path | None = None, data_dir: Path = DATA_DIR, refetch: bool = True) -> dict:
    out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    if refetch or not (data_dir / "hash-rate.json").exists():
        fetch(data_dir)
    d = load(data_dir); price = d["price"]; R = {"calibration": calibrate(d), "specs": {}, "data_last_day": str(d.index[-1].date())}
    for lag in LAGS:
        for pk in PRICES:
            x = series(d, lag, pk); ev = events(x, price); td = touch_day_stats(x, price); base = base_rates(price, x.index[0])
            key = f"满{lag}个月|{pk}美元/度"; R["specs"][key] = {"start": str(x.index[0].date()), "events": ev, "touch_days": td, "base": base, "judgement": judge(ev, td, base),
                                                           "now": {"machine": x["machine"].iloc[-1], "jt": float(x["jt"].iloc[-1]), "shutdown": float(x["shutdown"].iloc[-1]), "price": float(x["price"].iloc[-1])}}
            if (lag, pk) == MAIN:
                x.to_csv(out / "shutdown_series_main.csv")
    last = d["btc_per_th_day"].dropna().iloc[-1]
    R["now_by_efficiency"] = {f"{jt} W/T": {f"{pk}美元/度": shutdown_price(last, jt, pk) for pk in PRICES} for jt in (9.5, 13.5, 17.5, 21.5, 34.5)}
    R["main_spec"] = f"满{MAIN[0]}个月|{MAIN[1]}美元/度"; R["verdict"] = R["specs"][R["main_spec"]]["judgement"]["verdict"]
    R |= {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史研究（价格历史已反复研究过，非盲测）", "real_trading": "disabled (no implementation)"}
    (out / "phase25_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.DataFrame([e | {"spec": k} for k, v in R["specs"].items() for e in v["events"]]).to_csv(out / "events.csv", index=False); return R


if __name__ == "__main__":
    r = run(); print(r["verdict"])
