"""实时真实成交与盘口采集（REST 轮询；云端代理不支持 WebSocket，Mac 上可用同一实现）。
- OKX：/market/trades（主动方向 = side）；成交编号连续，出现缺口用 /market/history-trades 按编号向前回补，回补不完整记“未补齐缺口”。
- Coinbase Exchange：/products/{p}/trades（side 为挂单方方向 → 主动方向取反）；用游标向前翻页直到衔接上次最后一笔。
- 盘口：OKX books-full（5000 档）每 5 分钟、Coinbase 完整 L2 每 15 分钟；只保存分档深度摘要与“净挂单变化（新增 − 撤单，经成交校正）”。挂单是可撤销的意向，不计为买盘。
- 原始成交按 UTC 小时写 parquet；数据质量事件写哈希链；状态与资源占用写 status.json。不下单，没有任何交易接口。"""
from __future__ import annotations

import json
import resource
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json

from ..trendsys.paper import _append
from . import CB_PRODUCT, LARGE_USDT, SYMS

OKX = "https://www.okx.com"; CB = "https://api.exchange.coinbase.com"
BANDS = (0.005, 0.01, 0.02)


class Net:
    """记录下载字节数与请求次数（资源预算核对）；遵守限速（每个主机最小请求间隔）。"""
    def __init__(self, client: httpx.Client | None = None, min_gap_s: float = 0.15):
        self.c = client or httpx.Client(timeout=15, headers={"User-Agent": "brando-pa-research/0.22"}); self.bytes = 0; self.requests = 0; self.errors = 0; self.min_gap = min_gap_s; self._last = {}

    def get(self, url: str, params: dict | None = None):
        host = url.split("/")[2]; w = self._last.get(host, 0) + self.min_gap - time.time()
        if w > 0:
            time.sleep(w)
        self._last[host] = time.time(); r = self.c.get(url, params=params); self.requests += 1; self.bytes += len(r.content)
        if r.status_code != 200:
            self.errors += 1; raise RuntimeError(f"{url} HTTP {r.status_code}: {r.text[:120]}")
        return r


def okx_norm(t: dict, recv_ms: float) -> dict:
    return {"ex": "okx", "sym": t["instId"], "tid": int(t["tradeId"]), "ts_ms": int(t["ts"]), "price": float(t["px"]), "size": float(t["sz"]), "taker": t["side"].lower(), "recv_ms": recv_ms}


def cb_norm(t: dict, sym: str, recv_ms: float) -> dict:
    taker = {"buy": "sell", "sell": "buy"}[t["side"]]                      # Coinbase 的 side 是挂单方（maker）方向
    return {"ex": "coinbase", "sym": sym, "tid": int(t["trade_id"]), "ts_ms": int(pd.Timestamp(t["time"]).value // 10 ** 6), "price": float(t["price"]), "size": float(t["size"]), "taker": taker, "recv_ms": recv_ms}


class Stream:
    def __init__(self, ex: str, sym: str):
        self.ex, self.sym = ex, sym; self.last_tid = None; self.buf: list[dict] = []; self.n_today = 0; self.gaps_backfilled = 0; self.gaps_unresolved = 0; self.missing_ids = 0; self.last_trade_ms = None; self.lat = []; self.recent: list[dict] = []; self.last_new = 500

    def poll(self, net: Net, max_pages: int = 200) -> list[dict]:
        recv = time.time() * 1000
        if self.ex == "okx":                                                              # 只取预计的新成交数量（实测 OKX 的 before 参数返回最新的 N 笔，不能直接用）
            lim = 500 if self.last_tid is None else int(min(500, max(30, 1.5 * self.last_new + 20)))
            batch = [okx_norm(t, recv) for t in net.get(f"{OKX}/api/v5/market/trades", {"instId": self.sym, "limit": lim}).json()["data"]]
        else:                                                                             # Coinbase：before = 上次最后一笔，只返回更新的成交（实测）
            prm = {"limit": 1000} | ({"before": self.last_tid} if self.last_tid is not None else {})
            r = net.get(f"{CB}/products/{CB_PRODUCT[self.sym]}/trades", prm); batch = [cb_norm(t, self.sym, recv) for t in r.json()]; cursor = r.headers.get("cb-after")
        if not batch:
            self.last_new = 0; return []
        if self.last_tid is not None:
            pages = 0
            while min(x["tid"] for x in batch) > self.last_tid + 1 and pages < max_pages:             # 回补：向更早翻页直到衔接
                lo = min(x["tid"] for x in batch); pages += 1
                if self.ex == "okx":
                    more = [okx_norm(t, recv) for t in net.get(f"{OKX}/api/v5/market/history-trades", {"instId": self.sym, "type": "1", "after": str(lo), "limit": 100}).json()["data"]]
                else:
                    r = net.get(f"{CB}/products/{CB_PRODUCT[self.sym]}/trades", {"limit": 1000, "after": cursor}); more = [cb_norm(t, self.sym, recv) for t in r.json()]; cursor = r.headers.get("cb-after")
                if not more:
                    break
                batch += more
            if pages:
                self.gaps_backfilled += 1
            new = sorted({x["tid"]: x for x in batch if x["tid"] > self.last_tid}.values(), key=lambda x: x["tid"])
            if new and new[0]["tid"] != self.last_tid + 1:
                self.gaps_unresolved += 1; self.missing_ids += new[0]["tid"] - self.last_tid - 1
        else:
            new = sorted({x["tid"]: x for x in batch}.values(), key=lambda x: x["tid"])
        tids = np.array([x["tid"] for x in new]) if new else np.array([])
        if len(tids) > 1:
            self.missing_ids += int((np.diff(tids) - 1).clip(min=0).sum())
        self.last_new = len(new)
        if new:
            self.last_tid = new[-1]["tid"]; self.last_trade_ms = new[-1]["ts_ms"]; self.buf += new; self.n_today += len(new); self.lat += [x["recv_ms"] - x["ts_ms"] for x in new[-200:]]
            cut = new[-1]["ts_ms"] - 30 * 60 * 1000; self.recent = [x for x in self.recent if x["ts_ms"] >= cut] + new; self.lat = self.lat[-5000:]
        return new


def band_depth(bids, asks) -> dict:
    """bids/asks: [[价, 量], ...]。中间价 ±band 内挂单名义金额（USDT 或 USD）。"""
    b = np.array([[float(x[0]), float(x[1])] for x in bids]); a = np.array([[float(x[0]), float(x[1])] for x in asks]); mid = (b[0, 0] + a[0, 0]) / 2; out = {"mid": mid, "spread_bps": (a[0, 0] - b[0, 0]) / mid * 1e4,
                                                                                                                                                  "book_bid_range": 1 - b[-1, 0] / mid, "book_ask_range": a[-1, 0] / mid - 1}
    for bd in BANDS:
        bb = b[b[:, 0] >= mid * (1 - bd)]; aa = a[a[:, 0] <= mid * (1 + bd)]; out[f"bid_{bd}"] = float((bb[:, 0] * bb[:, 1]).sum()); out[f"ask_{bd}"] = float((aa[:, 0] * aa[:, 1]).sum())
        out[f"covered_{bd}"] = bool(out["book_bid_range"] >= bd and out["book_ask_range"] >= bd)
    return out


def book(net: Net, ex: str, sym: str) -> dict:
    t = time.time()
    if ex == "okx":
        d = net.get(f"{OKX}/api/v5/market/books-full", {"instId": sym, "sz": 5000}).json()["data"][0]; ts = int(d["ts"])
    else:
        d = net.get(f"{CB}/products/{CB_PRODUCT[sym]}/book", {"level": 2}).json(); ts = int(pd.Timestamp(d["time"]).value // 10 ** 6)
    return {"ex": ex, "sym": sym, "ts_ms": ts, "recv_ms": t * 1000, **band_depth(d["bids"], d["asks"])}


def hourly_from_trades(tr: pd.DataFrame) -> pd.DataFrame:
    q = tr["price"] * tr["size"]; buy = tr["taker"] == "buy"; h = pd.to_datetime(tr["ts_ms"], unit="ms", utc=True).dt.floor("h")
    x = pd.DataFrame({"hour": h, "tb": np.where(buy, q, 0.0), "ts": np.where(~buy, q, 0.0), "bb": np.where(buy, tr["size"], 0.0), "bs": np.where(~buy, tr["size"], 0.0), "nb": buy.astype(int), "ns": (~buy).astype(int),
                      "ltb": np.where(buy & (q >= LARGE_USDT), q, 0.0), "lts": np.where(~buy & (q >= LARGE_USDT), q, 0.0), "price": tr["price"], "tid": tr["tid"]})
    g = x.groupby("hour", sort=True); out = g[["tb", "ts", "bb", "bs", "nb", "ns", "ltb", "lts"]].sum()
    out["open"] = g["price"].first(); out["high"] = g["price"].max(); out["low"] = g["price"].min(); out["close"] = g["price"].last(); out["first_tid"] = g["tid"].min(); out["last_tid"] = g["tid"].max(); out["n"] = g["tid"].count()
    return out.reset_index()


class Collector:
    def __init__(self, data_dir: Path, out_dir: Path, net: Net | None = None, syms=SYMS, exchanges=("okx", "coinbase"), book_every_s: dict | None = None, flush_s: int = 600):
        self.dd = Path(data_dir) / "live"; self.od = Path(out_dir); self.dd.mkdir(parents=True, exist_ok=True); self.od.mkdir(parents=True, exist_ok=True); self.net = net or Net()
        self.streams = [Stream(e, s) for e in exchanges for s in syms]; self.book_every = book_every_s or {"okx": 300, "coinbase": 900}; self.flush_s = flush_s; self._last_book = {}; self._last_flush = time.time(); self._prev_book = {}
        self.t0 = time.time(); self.cpu0 = time.process_time(); self.started = pd.Timestamp.now(tz="UTC")
        st = self._read_status()
        for s in self.streams:                                                    # 重启后从上次最后一笔接着采，缺口回补
            s.last_tid = (st.get("streams", {}).get(f"{s.ex}:{s.sym}", {}) or {}).get("last_tid")

    def _read_status(self) -> dict:
        p = self.od / "collector_status.json"; return json.loads(p.read_text()) if p.exists() else {}

    def _q(self, kind: str, **kw):
        _append(self.od / "quality.jsonl", {"kind": kind, **kw})

    def flush(self):
        for s in self.streams:
            if not s.buf:
                continue
            tr = pd.DataFrame(s.buf); s.buf = []
            for (day, hh), g in tr.groupby([pd.to_datetime(tr["ts_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d"), pd.to_datetime(tr["ts_ms"], unit="ms", utc=True).dt.strftime("%H")]):
                p = self.dd / "raw" / s.ex / s.sym / day; p.mkdir(parents=True, exist_ok=True); g.to_parquet(p / f"{hh}_{int(g['tid'].min())}.parquet", index=False)
            h = hourly_from_trades(tr); h.insert(0, "symbol", s.sym); hp = self.dd / f"{s.ex}_{s.sym}_hourly.parquet"
            (pd.concat([pd.read_parquet(hp), h], ignore_index=True) if hp.exists() else h).to_parquet(hp, index=False)
        self._last_flush = time.time()

    def step(self):
        now = time.time()
        for s in self.streams:
            try:
                g0, u0 = s.gaps_backfilled, s.gaps_unresolved; new = s.poll(self.net)
                if s.gaps_backfilled > g0:
                    self._q("gap_backfilled", stream=f"{s.ex}:{s.sym}", last_tid=s.last_tid)
                if s.gaps_unresolved > u0:
                    self._q("gap_unresolved", stream=f"{s.ex}:{s.sym}", missing_ids_total=s.missing_ids)
            except Exception as e:  # noqa: BLE001
                self._q("poll_error", stream=f"{s.ex}:{s.sym}", error=str(e)[:200])
        for ex in self.book_every:
            if now - self._last_book.get(ex, 0) >= self.book_every[ex]:
                self._last_book[ex] = now
                for sym in {s.sym for s in self.streams if s.ex == ex}:
                    try:
                        b = book(self.net, ex, sym); key = f"{ex}:{sym}"; prev = self._prev_book.get(key)
                        if prev:
                            tr = [x for st_ in self.streams if st_.ex == ex and st_.sym == sym for x in st_.recent if prev["ts_ms"] < x["ts_ms"] <= b["ts_ms"]]
                            sold = sum(x["price"] * x["size"] for x in tr if x["taker"] == "sell" and x["price"] >= prev["mid"] * 0.99)
                            bought = sum(x["price"] * x["size"] for x in tr if x["taker"] == "buy" and x["price"] <= prev["mid"] * 1.01)
                            b["net_bid_placement_1pct"] = b["bid_0.01"] - prev["bid_0.01"] + sold; b["net_ask_placement_1pct"] = b["ask_0.01"] - prev["ask_0.01"] + bought
                            b["interval_s"] = (b["ts_ms"] - prev["ts_ms"]) / 1000; b["trades_attributed"] = len(tr)
                        self._prev_book[key] = b
                        with open(self.dd / f"book_{ex}_{sym}.jsonl", "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(b, default=float) + "\n")
                    except Exception as e:  # noqa: BLE001
                        self._q("book_error", stream=f"{ex}:{sym}", error=str(e)[:200])
        if now - self._last_flush >= self.flush_s:
            self.flush()
        self.write_status()

    def write_status(self):
        el = max(time.time() - self.t0, 1e-9); rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        st = {"updated_utc": str(pd.Timestamp.now(tz="UTC")), "started_utc": str(self.started), "streams": {}, "resources": {"elapsed_s": round(el), "cpu_s": round(time.process_time() - self.cpu0, 2), "cpu_share_one_core": round((time.process_time() - self.cpu0) / el, 4),
                                                                                                                       "max_rss_mb": round(rss / (1024 * 1024) if rss > 10 ** 7 else rss / 1024, 1), "download_mb": round(self.net.bytes / 1e6, 2), "download_mb_per_day": round(self.net.bytes / 1e6 / el * 86400, 1), "requests": self.net.requests, "http_errors": self.net.errors},
              "auto_trading": False, "real_trading": "disabled (no implementation)"}
        for s in self.streams:
            L = sorted(s.lat[-2000:]); st["streams"][f"{s.ex}:{s.sym}"] = {"last_tid": s.last_tid, "last_trade_utc": str(pd.Timestamp(s.last_trade_ms, unit="ms", tz="UTC")) if s.last_trade_ms else None, "trades_collected": s.n_today,
                                                                          "gaps_backfilled": s.gaps_backfilled, "gaps_unresolved": s.gaps_unresolved, "missing_ids": s.missing_ids, "latency_p50_ms": L[len(L) // 2] if L else None, "latency_p95_ms": L[int(len(L) * .95)] if L else None}
        atomic_write_json(self.od / "collector_status.json", st, indent=1, default=str)

    def run(self, duration_s: float | None = None, interval_s: float = 10.0):
        end = None if duration_s is None else time.time() + duration_s
        try:
            while end is None or time.time() < end:
                t = time.time(); self.step(); time.sleep(max(0.0, interval_s - (time.time() - t)))
        finally:
            self.flush(); self.write_status()


def load_live_hourly(data_dir: Path, ex: str, sym: str) -> pd.DataFrame:
    p = Path(data_dir) / "live" / f"{ex}_{sym}_hourly.parquet"
    if not p.exists():
        return pd.DataFrame()
    h = pd.read_parquet(p).sort_values(["hour", "first_tid"]); g = h.groupby("hour", sort=True); out = g[["tb", "ts", "bb", "bs", "nb", "ns", "ltb", "lts", "n"]].sum()
    out["open"] = g["open"].first(); out["high"] = g["high"].max(); out["low"] = g["low"].min(); out["close"] = g["close"].last(); out["first_tid"] = g["first_tid"].min(); out["last_tid"] = g["last_tid"].max(); return out.reset_index()


if __name__ == "__main__":
    import sys
    from ..config import own_data_dir
    from . import DATA_SUBDIR, OUT_DIR
    Collector(own_data_dir() / DATA_SUBDIR, OUT_DIR).run(float(sys.argv[1]) if len(sys.argv) > 1 else None, float(sys.argv[2]) if len(sys.argv) > 2 else 10.0)
