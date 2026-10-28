"""备用真实行情源：GitHub 公开仓库 finom/static-klines 中提交的 Binance 现货 K 线缓存（.klines-cache）。

* 路径：{raw_base}/{SYMBOL}/{tf}/{window_start}.json，每个文件是 Binance 原始 12 列 K 线数组。
* 1d 窗口：自 2016-01-01 起每 24 个月一个文件；4h 窗口：自 2017-07-01 起每季度一个文件；由 GitHub Actions 每日更新。
* 这是“Binance 现货”而非“Bitget 现货”，所有输出标注 source=binance_spot_via_static_klines；
  主流币两所价差通常在万分之几量级，但系统仍把它视为备用源，Bitget 可达时以 Bitget 为准。
* 本地缓存：历史窗口文件只下载一次；当前窗口每天刷新。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from ..config import cfg, data_dir
from .schema import empty_bars, normalize_rows

SOURCE = "binance_spot_via_static_klines"


def _add_months(d: date, months: int) -> date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def window_starts(tf: str, today: date | None = None) -> list[date]:
    c = cfg()["data"]["static_klines"]
    anchor = date.fromisoformat(c["window_anchor"][tf])
    months = int(c["window_months"][tf])
    today = today or datetime.now(timezone.utc).date()
    out, d = [], anchor
    while d <= today:
        out.append(d)
        d = _add_months(d, months)
    return out


class StaticKlinesClient:
    def __init__(self, cache_root: Path | None = None, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        c = cfg()["data"]["static_klines"]
        self.raw_base = c["raw_base"].rstrip("/")
        self.cache_root = cache_root or (data_dir() / "raw_cache" / "static_klines")
        self.client = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "spot90/0.1"})
        self.requests = 0
        self.errors: list[str] = []

    def _fetch_window(self, symbol: str, tf: str, start: date, refresh: bool) -> list | None:
        p = self.cache_root / symbol / tf / f"{start.isoformat()}.json"
        if p.exists() and not refresh:
            try:
                return json.loads(p.read_text())
            except json.JSONDecodeError:
                p.unlink(missing_ok=True)
        url = f"{self.raw_base}/{symbol}/{tf}/{start.isoformat()}.json"
        for attempt in range(4):
            try:
                self.requests += 1
                r = self.client.get(url)
            except httpx.HTTPError as e:
                if attempt == 3:
                    self.errors.append(f"{url}: {type(e).__name__}")
                    return json.loads(p.read_text()) if p.exists() else None
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                time.sleep(1.0 * (attempt + 1))
                continue
            try:
                rows = r.json()
            except ValueError:
                self.errors.append(f"{url}: 非 JSON")
                return None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rows))
            return rows
        self.errors.append(f"{url}: HTTP {r.status_code}")
        return json.loads(p.read_text()) if p.exists() else None

    def probe(self) -> dict:
        try:
            r = self.client.get(f"{self.raw_base}/BTCUSDT/1d/2016-01-01.json")
            return {"ok": r.status_code == 200, "detail": f"raw.githubusercontent.com HTTP {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": f"raw.githubusercontent.com 不可达：{type(e).__name__}"}

    def fetch(self, symbol: str, tf: str, refresh_current: bool = True) -> pd.DataFrame:
        starts = window_starts(tf)
        rows: list = []
        for i, s in enumerate(starts):
            is_current = i == len(starts) - 1
            w = self._fetch_window(symbol, tf, s, refresh=refresh_current and is_current)
            if w:
                rows += [[r[0], r[1], r[2], r[3], r[4], r[5], r[7] if len(r) > 7 else None] for r in w]
        if not rows:
            return empty_bars()
        return normalize_rows(rows, symbol, tf, SOURCE)
