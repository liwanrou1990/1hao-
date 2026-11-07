"""K 线存储：本引擎自有 parquet（data_store/brando_pa/bars）+ 只读回退 spot90 数据仓（1d/4h）。原子写入复用 spot90.atomic。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_to_parquet

from ..config import own_data_dir, spot90_data_dir
from ..schema import BAR_COLUMNS, empty_bars


class BarStore:
    def __init__(self, root: Path | None = None, fallback_root: Path | None = None):
        self.root = root or own_data_dir()
        (self.root / "bars").mkdir(parents=True, exist_ok=True)
        self.fallback_root = fallback_root if fallback_root is not None else spot90_data_dir()

    def path(self, symbol: str, tf: str) -> Path:
        return self.root / "bars" / f"{symbol}_{tf}.parquet"

    def _read(self, p: Path) -> pd.DataFrame:
        if not p.exists():
            return empty_bars()
        df = pd.read_parquet(p)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        for c in ("open", "high", "low", "close", "volume", "quote_volume"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        return df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)

    def load(self, symbol: str, tf: str, allow_fallback: bool = True) -> pd.DataFrame:
        df = self._read(self.path(symbol, tf))
        if df.empty and allow_fallback and self.fallback_root is not None:
            fb = self.fallback_root / "bars" / f"{symbol}_{tf}.parquet"
            df = self._read(fb)
            if not df.empty:
                df.attrs["from_fallback"] = str(fb)
        return df

    def upsert(self, bars: pd.DataFrame, prefer_new: bool = True) -> dict[str, int]:
        if bars is None or bars.empty:
            return {"added": 0, "replaced": 0, "total": 0}
        symbol, tf = bars["symbol"].iloc[0], bars["tf"].iloc[0]
        old = self._read(self.path(symbol, tf))
        new = bars[BAR_COLUMNS].copy()
        if old.empty:
            merged, added, replaced = new, len(new), 0
        else:
            both = pd.concat([old, new], ignore_index=True)
            both["ingested_at"] = pd.to_datetime(both["ingested_at"], utc=True)
            both = both.sort_values(["ts", "ingested_at"]).drop_duplicates("ts", keep="last" if prefer_new else "first")
            added = int((~new["ts"].isin(old["ts"])).sum()); replaced = len(new) - added
            merged = both
        merged = merged.sort_values("ts").reset_index(drop=True)
        atomic_to_parquet(merged, self.path(symbol, tf))
        return {"added": added, "replaced": replaced, "total": len(merged)}
