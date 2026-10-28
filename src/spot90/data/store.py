"""Parquet 为真源（按 symbol/tf 分文件）+ DuckDB 查询/记录（预测历史、回测明细）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..config import data_dir
from .schema import BAR_COLUMNS, empty_bars


class BarStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        (self.root / "bars").mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str, tf: str) -> Path:
        return self.root / "bars" / f"{symbol}_{tf}.parquet"

    def load(self, symbol: str, tf: str) -> pd.DataFrame:
        p = self.path(symbol, tf)
        if not p.exists():
            return empty_bars()
        df = pd.read_parquet(p)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df.sort_values("ts").reset_index(drop=True)

    def upsert(self, bars: pd.DataFrame, prefer_new: bool = True) -> dict[str, int]:
        """按 ts 合并：prefer_new=True 时新数据覆盖旧数据（同一 ts）。返回 {added, replaced, total}。"""
        if bars is None or bars.empty:
            return {"added": 0, "replaced": 0, "total": 0}
        symbol, tf = bars["symbol"].iloc[0], bars["tf"].iloc[0]
        old = self.load(symbol, tf)
        new = bars[BAR_COLUMNS].copy()
        if old.empty:
            merged, added, replaced = new, len(new), 0
        else:
            both = pd.concat([old, new], ignore_index=True)
            both = both.sort_values(["ts", "ingested_at"]).drop_duplicates("ts", keep="last" if prefer_new else "first")
            added = int((~new["ts"].isin(old["ts"])).sum())
            replaced = len(new) - added
            merged = both
        merged = merged.sort_values("ts").reset_index(drop=True)
        merged.to_parquet(self.path(symbol, tf), index=False)
        return {"added": added, "replaced": replaced, "total": len(merged)}

    def duck(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(str(self.root / "spot90.duckdb"))
        con.execute("""CREATE TABLE IF NOT EXISTS predictions (
            run_ts TIMESTAMPTZ, as_of DATE, symbol VARCHAR, payload JSON)""")
        con.execute("""CREATE TABLE IF NOT EXISTS data_status (run_ts TIMESTAMPTZ, payload JSON)""")
        con.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (run_ts TIMESTAMPTZ, name VARCHAR, payload JSON)""")
        return con

    def save_json(self, table: str, **cols: Any) -> None:
        con = self.duck()
        try:
            keys = list(cols)
            vals = [json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v for v in cols.values()]
            con.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})", vals)
        finally:
            con.close()

    def prediction_history(self, symbol: str | None = None, limit: int = 2000) -> pd.DataFrame:
        con = self.duck()
        try:
            q = "SELECT run_ts, as_of, symbol, payload FROM predictions"
            params: list[Any] = []
            if symbol:
                q += " WHERE symbol = ?"
                params.append(symbol)
            q += f" ORDER BY as_of DESC, run_ts DESC LIMIT {int(limit)}"
            return con.execute(q, params).df()
        finally:
            con.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
