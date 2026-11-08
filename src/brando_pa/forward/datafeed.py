"""真实行情服务（第八阶段）。

数据源优先级（全部为交易所官方公开现货 K 线接口，无密钥）：Binance → OKX → Bybit → Coinbase Exchange；最后回退到 Binance 现货 K 线的 GitHub 镜像
（finom/static-klines，有数小时延迟，标注 source=static_klines_mirror）。非交易所参考价（CoinGecko）只用于新鲜度/异常对照，绝不用于成交。

规则：只保留已收盘 K 线（开盘时刻 + 周期 ≤ now）；UTC；去重、排序、缺口与乱序检测；1h→1d 聚合一致性校验；失败重试 + 指数退避；
每次更新写 forward_data_status.json（来源、最后成功时间、各周期最后收盘、滞后、缺口、一致性、错误）并追加 data_events.jsonl（哈希链）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg, own_data_dir
from ..data.sources import StaticKlinesAdapter, utcnow
from ..data.store import BarStore
from ..schema import tf_seconds
from ..trendsys.paper import _append

COLS = ["ts", "open", "high", "low", "close", "volume"]
PAIR = {"BTCUSDT": {"binance": "BTCUSDT", "okx": "BTC-USDT", "bybit": "BTCUSDT", "coinbase": "BTC-USDT"}, "ETHUSDT": {"binance": "ETHUSDT", "okx": "ETH-USDT", "bybit": "ETHUSDT", "coinbase": "ETH-USDT"}}


def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for c in COLS[1:]:
        df[c] = df[c].astype(float)
    return df


class ExchangeSource:
    name = "base"; host = ""

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        raise NotImplementedError


class BinanceSpot(ExchangeSource):
    name = "binance_spot"; host = "api.binance.com"
    IV = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w", "15m": "15m"}

    def fetch(self, symbol, tf, start, end):
        rows = []; cur = int(start.timestamp() * 1000); end_ms = int(end.timestamp() * 1000)
        while cur < end_ms:
            r = self.client.get(f"https://{self.host}/api/v3/klines", params={"symbol": PAIR[symbol]["binance"], "interval": self.IV[tf], "startTime": cur, "endTime": end_ms, "limit": 1000}); r.raise_for_status()
            data = r.json()
            if not data:
                break
            rows += [[int(k[0]), k[1], k[2], k[3], k[4], k[5]] for k in data]
            nxt = int(data[-1][0]) + tf_seconds(tf) * 1000
            if nxt <= cur or len(data) < 1000:
                break
            cur = nxt
        return _df([[pd.Timestamp(r_[0], unit="ms", tz="UTC"), *r_[1:]] for r_ in rows])


class OKXSpot(ExchangeSource):
    name = "okx_spot"; host = "www.okx.com"
    IV = {"1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc", "15m": "15m"}

    def fetch(self, symbol, tf, start, end):
        rows = []; after = int(end.timestamp() * 1000) + 1; start_ms = int(start.timestamp() * 1000)
        for _ in range(200):
            r = self.client.get(f"https://{self.host}/api/v5/market/history-candles", params={"instId": PAIR[symbol]["okx"], "bar": self.IV[tf], "after": after, "limit": 100}); r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                break
            rows += [[int(k[0]), k[1], k[2], k[3], k[4], k[5]] for k in data if k[8] == "1"]      # confirm == 1 → 已收盘
            after = int(data[-1][0])
            if after <= start_ms:
                break
        return _df([[pd.Timestamp(r_[0], unit="ms", tz="UTC"), *r_[1:]] for r_ in rows if r_[0] >= start_ms])


class BybitSpot(ExchangeSource):
    name = "bybit_spot"; host = "api.bybit.com"
    IV = {"1h": "60", "4h": "240", "1d": "D", "1w": "W", "15m": "15"}

    def fetch(self, symbol, tf, start, end):
        rows = []; end_ms = int(end.timestamp() * 1000); start_ms = int(start.timestamp() * 1000)
        for _ in range(200):
            r = self.client.get(f"https://{self.host}/v5/market/kline", params={"category": "spot", "symbol": PAIR[symbol]["bybit"], "interval": self.IV[tf], "start": start_ms, "end": end_ms, "limit": 1000}); r.raise_for_status()
            data = r.json().get("result", {}).get("list", [])
            if not data:
                break
            rows += [[int(k[0]), k[1], k[2], k[3], k[4], k[5]] for k in data]
            oldest = min(int(k[0]) for k in data)
            if oldest <= start_ms or len(data) < 1000:
                break
            end_ms = oldest - 1
        return _df([[pd.Timestamp(r_[0], unit="ms", tz="UTC"), *r_[1:]] for r_ in rows])


class CoinbaseExchange(ExchangeSource):
    name = "coinbase_exchange"; host = "api.exchange.coinbase.com"
    IV = {"1h": 3600, "4h": 21600, "1d": 86400, "15m": 900}    # 注意：Coinbase 无 4h 粒度（6h 代替，不用于 4h）

    def fetch(self, symbol, tf, start, end):
        if tf == "4h":
            return _df([])
        rows = []; g = self.IV[tf]; cur = start
        while cur < end:
            nxt = min(cur + pd.Timedelta(seconds=g * 300), end)
            r = self.client.get(f"https://{self.host}/products/{PAIR[symbol]['coinbase']}/candles", params={"granularity": g, "start": cur.isoformat(), "end": nxt.isoformat()}); r.raise_for_status()
            rows += [[pd.Timestamp(k[0], unit="s", tz="UTC"), k[3], k[2], k[1], k[4], k[5]] for k in r.json()]
            cur = nxt
        return _df(rows)


class MirrorSource:
    """GitHub 镜像（Binance 现货 K 线缓存，数小时延迟）。"""
    name = "static_klines_mirror"; host = "raw.githubusercontent.com"

    def __init__(self, client=None):
        self.a = StaticKlinesAdapter()

    def fetch(self, symbol, tf, start, end):
        df = self.a.fetch_klines(symbol, tf, start=start)
        return df[COLS] if not df.empty else _df([])


def normalize(df: pd.DataFrame, tf: str, now: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """UTC、去重、排序、只保留已收盘；返回质量报告（重复、乱序、未收盘、缺口）。"""
    rep = {"n_raw": int(len(df)), "duplicates": 0, "out_of_order": 0, "unclosed_dropped": 0, "gaps": []}
    if df.empty:
        return df, rep
    d = df.copy(); d["ts"] = pd.to_datetime(d["ts"], utc=True)
    rep["out_of_order"] = int((d["ts"].diff().dt.total_seconds() < 0).sum())
    rep["duplicates"] = int(d["ts"].duplicated().sum())
    d = d.drop_duplicates("ts", keep="last").sort_values("ts")
    closed = (d["ts"] + pd.Timedelta(seconds=tf_seconds(tf))) <= now
    rep["unclosed_dropped"] = int((~closed).sum()); d = d[closed].reset_index(drop=True)
    sec = tf_seconds(tf); diffs = d["ts"].diff().dt.total_seconds()
    for i in np.flatnonzero((diffs > sec * 1.5).to_numpy()):
        rep["gaps"].append({"after": str(d["ts"].iloc[i - 1]), "before": str(d["ts"].iloc[i]), "missing_bars": int(round(diffs.iloc[i] / sec)) - 1})
    return d, rep


def consistency_1h_vs_1d(h1: pd.DataFrame, d1: pd.DataFrame, tol: float = 0.005, days: int = 10) -> dict:
    """最近 N 个完整 UTC 日：24 根 1h 聚合的 O/H/L/C 与日线对比。"""
    if h1.empty or d1.empty:
        return {"checked_days": 0, "ok": None}
    h = h1.set_index("ts"); agg = h.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}); cnt = h["close"].resample("1D").count()
    full = agg[cnt == 24].tail(days); d = d1.set_index("ts").reindex(full.index)
    if full.empty or d["close"].isna().all():
        return {"checked_days": 0, "ok": None}
    err = ((full[["open", "high", "low", "close"]] - d[["open", "high", "low", "close"]]).abs() / d[["open", "high", "low", "close"]]).max().max()
    return {"checked_days": int(len(full)), "max_rel_err": float(err), "ok": bool(err <= tol)}


@dataclass
class FeedStatus:
    source: str | None = None
    ok: bool = False
    last_success_utc: str | None = None
    per_tf: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    consistency: dict = field(default_factory=dict)
    reference_check: dict = field(default_factory=dict)
    stale: bool = True
    quality_fault: bool = False
    quality_note: str = ""
    source_switch: dict = field(default_factory=dict)


class MarketDataService:
    SOURCES = [BinanceSpot, OKXSpot, BybitSpot, CoinbaseExchange, MirrorSource]

    def __init__(self, c: dict | None = None, store: BarStore | None = None, log_dir: Path | None = None, sources: list | None = None, timeout: float = 15.0, max_retries: int = 3, seed_from: BarStore | None = None):
        """store 默认为独立的前瞻行情仓 data_store/brando_pa/forward_bars（首次使用时从历史仓复制作为指标预热，之后只由实时来源追加；每根 K 线带 source）。"""
        self.c = c or cfg(); self.dir = Path(log_dir or (own_data_dir(self.c))); self.dir.mkdir(parents=True, exist_ok=True)
        self.store = store or BarStore(root=own_data_dir(self.c) / "forward_bars", fallback_root=None); self.seed_from = seed_from if seed_from is not None else BarStore()
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "brando-pa-research/0.8"})
        by_name = {S.name: S for S in self.SOURCES}
        order = list(self.c.get("forward", {}).get("source_priority", [S.name for S in self.SOURCES]))
        self.sources = sources if sources is not None else [by_name[n](self.client) for n in order if n in by_name]
        self.max_retries = max_retries; self.mult = float(self.c["data"].get("max_bar_age_multiplier", 3))
        self.status_path = self.dir / "forward_data_status.json"; self.events_path = self.dir / "data_events.jsonl"

    def _with_retries(self, fn, what: str, errors: list):
        for k in range(self.max_retries):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{what}: {type(e).__name__}: {str(e)[:120]}")
                if k < self.max_retries - 1:
                    time.sleep(min(2 ** k, 8))
        return None

    def update(self, symbols: list[str], tfs: list[str], now: pd.Timestamp | None = None, lookback_days: int = 10) -> FeedStatus:
        now = now or utcnow(); st = FeedStatus()
        for src in self.sources:
            got = {}; errs = []
            for s in symbols:
                for tf in tfs:
                    self._seed(s, tf)
                    have = self.store.load(s, tf, allow_fallback=False)
                    start = (have["ts"].iloc[-1] - pd.Timedelta(seconds=tf_seconds(tf) * 3)) if not have.empty else now - pd.Timedelta(days=lookback_days)
                    df = self._with_retries(lambda: src.fetch(s, tf, start, now), f"{src.name} {s} {tf}", errs)
                    if df is None:
                        got = None; break
                    got[(s, tf)] = df
                if got is None:
                    break
            st.errors += errs
            if got is None:
                self._event("source_failed", source=src.name, errors=errs[-3:]); continue
            # 全部拉取成功 → 规范化、入库、质量报告
            any_new = False; xsrc = {}
            for (s, tf), df in got.items():
                d, rep = normalize(df, tf, now)
                have0 = self.store.load(s, tf, allow_fallback=False)
                if not d.empty:
                    # 跨来源一致性：与已存的、来源不同的同时刻 K 线比较收盘价（只报告，不改写既有 K 线）
                    if not have0.empty and "source" in have0:
                        ov = have0.merge(d[["ts", "close"]], on="ts", suffixes=("_old", "_new")); ov = ov[ov["source"] != src.name]
                        if len(ov):
                            xsrc[f"{s}_{tf}"] = {"overlap_bars": int(len(ov)), "other_source": str(ov["source"].iloc[-1]), "max_rel_close_diff": float((ov["close_new"] / ov["close_old"] - 1).abs().max())}
                        d = d[~d["ts"].isin(have0["ts"])]           # 既有 K 线不被新来源覆盖（历史不可改写）
                    if not d.empty:
                        d = d.assign(symbol=s, tf=tf, quote_volume=np.nan, source=src.name, ingested_at=now)
                        res = self.store.upsert(d, prefer_new=True); any_new |= bool(res.get("added", 0))
                        rep["added"] = int(res.get("added", 0))
                have = self.store.load(s, tf, allow_fallback=False)
                last_close = have["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf)) if not have.empty else None
                lag_h = (now - last_close).total_seconds() / 3600 if last_close is not None else None
                st.per_tf[f"{s}_{tf}"] = {"n_fetched": rep["n_raw"], "quality": rep, "last_closed_bar_close_utc": str(last_close), "lag_hours": lag_h, "stale": bool(lag_h is None or lag_h > self.mult * tf_seconds(tf) / 3600), "n_stored": int(len(have))}
            if "1h" in tfs and "1d" in tfs:
                st.consistency = {s: consistency_1h_vs_1d(self.store.load(s, "1h", allow_fallback=False), self.store.load(s, "1d", allow_fallback=False)) for s in symbols}
            st.source = src.name; st.ok = True; st.last_success_utc = str(now); st.stale = any(v["stale"] for v in st.per_tf.values()); st.consistency["cross_source"] = xsrc
            prev_src = self._prev_source()
            if prev_src and prev_src != src.name:
                st.source_switch = {"from": prev_src, "to": src.name, "at": str(now), "overlap": xsrc}; self._event("source_switch", **st.source_switch)
            thr = float(self.c.get("forward", {}).get("cross_source_max_rel_diff", 0.005))
            bad = {k: v for k, v in xsrc.items() if v.get("max_rel_close_diff", 0) > thr}
            if bad:
                st.quality_fault = True; st.quality_note = f"跨来源重叠 K 线收盘价偏差超过 {thr:.2%}：{bad}"; self._event("data_quality_fault", note=st.quality_note)
            bad_c = {k: v for k, v in st.consistency.items() if k != "cross_source" and isinstance(v, dict) and v.get("ok") is False}
            if bad_c:
                st.quality_fault = True; st.quality_note += f" 1h→1d 聚合不一致：{bad_c}"; self._event("data_quality_fault", note=st.quality_note)
            self._event("update_ok", source=src.name, stale=st.stale, per_tf={k: {"lag_hours": v["lag_hours"], "n_fetched": v["n_fetched"]} for k, v in st.per_tf.items()}, consistency=st.consistency, any_new=any_new)
            break
        st.reference_check = self.reference_check(symbols)
        self._write_status(st, now)
        return st

    def _prev_source(self) -> str | None:
        try:
            import json
            return json.loads(self.status_path.read_text()).get("last_success_source") if self.status_path.exists() else None
        except Exception:  # noqa: BLE001
            return None

    def _seed(self, s: str, tf: str) -> None:
        """前瞻行情仓为空时，从历史仓复制一次作为指标预热（标注 source 原值，通常为 Binance 镜像）。"""
        if self.seed_from is None or not self.store.load(s, tf, allow_fallback=False).empty:
            return
        hist = self.seed_from.load(s, tf)
        if not hist.empty:
            hist = hist.copy()
            if "source" not in hist or hist["source"].isna().all():
                hist["source"] = "binance_spot_history"
            for col, val in (("symbol", s), ("tf", tf), ("quote_volume", np.nan), ("ingested_at", utcnow())):
                if col not in hist:
                    hist[col] = val
            self.store.upsert(hist, prefer_new=False); self._event("seeded_from_history", symbol=s, tf=tf, n=int(len(hist)), last=str(hist["ts"].iloc[-1]))

    def reference_check(self, symbols: list[str]) -> dict:
        """非交易所参考价（CoinGecko simple price）：只用于对照最后收盘价是否明显偏离（> 3%）；不用于成交。"""
        ids = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}; out = {}
        try:
            r = self.client.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(ids[s] for s in symbols), "vs_currencies": "usd", "include_last_updated_at": "true"}); r.raise_for_status(); j = r.json()
            for s in symbols:
                have = self.store.load(s, "1h", allow_fallback=False); last = float(have["close"].iloc[-1]) if not have.empty else None
                ref = j.get(ids[s], {}).get("usd"); out[s] = {"reference_usd": ref, "reference_updated_at": j.get(ids[s], {}).get("last_updated_at"), "last_1h_close": last, "rel_diff": (last / ref - 1) if (ref and last) else None, "flag_gt_3pct": bool(ref and last and abs(last / ref - 1) > 0.03), "note": "non-exchange reference, alerts only"}
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out

    def _event(self, kind: str, **kw) -> None:
        _append(self.events_path, {"kind": kind, **kw})

    def _write_status(self, st: FeedStatus, now) -> None:
        prev = {}
        if self.status_path.exists():
            try:
                import json; prev = json.loads(self.status_path.read_text())
            except Exception:  # noqa: BLE001
                prev = {}
        d = {"checked_at_utc": str(now), "ok": st.ok, "source": st.source, "last_success_utc": st.last_success_utc or prev.get("last_success_utc"), "last_success_source": st.source or prev.get("last_success_source"), "stale": st.stale, "quality_fault": st.quality_fault, "quality_note": st.quality_note, "source_switch": st.source_switch or prev.get("source_switch"), "per_tf": st.per_tf, "consistency": st.consistency, "reference_check": st.reference_check, "errors": st.errors[-20:], "note": "只保留已收盘 K 线；UTC；来源为交易所官方公开现货接口或其镜像（见 source）"}
        atomic_write_json(self.status_path, d, indent=1, default=str)

    def run_loop(self, symbols, tfs, interval_s: int = 300, max_iter: int | None = None):
        k = 0
        while max_iter is None or k < max_iter:
            try:
                st = self.update(symbols, tfs); print(f"[datafeed] {utcnow()} ok={st.ok} source={st.source} stale={st.stale} errors={len(st.errors)}", flush=True)
            except Exception as e:  # noqa: BLE001
                self._event("loop_error", error=f"{type(e).__name__}: {e}")
            k += 1
            if max_iter is None or k < max_iter:
                time.sleep(interval_s)


def cross_source_audit(svc: "MarketDataService", symbols: list[str], tf: str = "1h", hours: int = 48, now: pd.Timestamp | None = None) -> dict:
    """实时跨来源审计：对每个可达来源直接拉取最近 N 小时 K 线，检查交易对、时间戳对齐、闭合、重复、缺口，并两两比较收盘价偏差。只读，不入库。"""
    now = now or utcnow(); start = now - pd.Timedelta(hours=hours); out = {"checked_at_utc": str(now), "tf": tf, "hours": hours, "sources": {}, "pairwise_close_diff": {}}
    data = {}
    for src in svc.sources:
        for s in symbols:
            try:
                raw = src.fetch(s, tf, start, now); d, rep = normalize(raw, tf, now)
                bad_ts = int((d["ts"].dt.minute != 0).sum()) if not d.empty else 0
                ok_ohlc = bool(((d["high"] >= d[["open", "close"]].max(axis=1)) & (d["low"] <= d[["open", "close"]].min(axis=1))).all()) if not d.empty else None
                out["sources"].setdefault(src.name, {})[s] = {"ok": True, "n_closed": int(len(d)), "first": str(d["ts"].iloc[0]) if len(d) else None, "last_close": str(d["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf))) if len(d) else None, "lag_hours": float((now - (d["ts"].iloc[-1] + pd.Timedelta(seconds=tf_seconds(tf)))).total_seconds() / 3600) if len(d) else None,
                                                             "duplicates": rep["duplicates"], "unclosed_dropped": rep["unclosed_dropped"], "gaps": rep["gaps"], "non_hour_aligned": bad_ts, "ohlc_consistent": ok_ohlc, "pair": PAIR[s].get(src.name.split("_")[0], s)}
                data[(src.name, s)] = d.set_index("ts")["close"]
            except Exception as e:  # noqa: BLE001
                out["sources"].setdefault(src.name, {})[s] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}"}
    names = sorted({k[0] for k in data})
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for s in symbols:
                if (a, s) in data and (b, s) in data:
                    j = pd.concat([data[(a, s)].rename("a"), data[(b, s)].rename("b")], axis=1).dropna()
                    if len(j):
                        r = (j["a"] / j["b"] - 1).abs(); out["pairwise_close_diff"][f"{a} vs {b} {s}"] = {"overlap_bars": int(len(j)), "max_rel_diff": float(r.max()), "mean_rel_diff": float(r.mean()), "exceeds_gate": bool(r.max() > float(svc.c.get("forward", {}).get("cross_source_max_rel_diff", 0.005)))}
    return out
