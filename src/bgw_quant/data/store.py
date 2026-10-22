"""DuckDB 本地存储：K线、成交、复盘结果、训练记录。"""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..config import get_settings
from .schema import BAR_COLUMNS

DDL = """
CREATE TABLE IF NOT EXISTS bars (
  symbol VARCHAR, venue VARCHAR, source VARCHAR, chain VARCHAR, contract VARCHAR,
  ts TIMESTAMPTZ, ts_ny VARCHAR,
  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE, txn DOUBLE,
  quality INTEGER, is_complete BOOLEAN, ingested_at TIMESTAMPTZ, tf VARCHAR DEFAULT '1m'
);
CREATE TABLE IF NOT EXISTS token_trades (
  symbol VARCHAR, chain VARCHAR, contract VARCHAR, ts TIMESTAMPTZ, ts_ny VARCHAR, side VARCHAR, price DOUBLE, amount DOUBLE, value DOUBLE,
  tx_hash VARCHAR, source VARCHAR, ingested_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS asset_registry (
  ticker VARCHAR, chain VARCHAR, contract VARCHAR, symbol VARCHAR, data_source VARCHAR, status VARCHAR,
  verified BOOLEAN, verified_at TIMESTAMPTZ, note VARCHAR
);
CREATE TABLE IF NOT EXISTS replay_results (
  run_id VARCHAR, symbol VARCHAR, day DATE, mode VARCHAR, payload JSON, created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS backtest_runs (
  run_id VARCHAR, name VARCHAR, payload JSON, created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS training_sessions (
  session_id VARCHAR, trader VARCHAR, symbol VARCHAR, day DATE, state JSON, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
);
"""


class Store:
    def __init__(self, path: str | Path | None = None):
        s = get_settings()
        p = Path(path) if path else s.resolve_path(s.db_path)
        if str(p) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        try:
            self.con = duckdb.connect(self.path)
        except duckdb.IOException as e:
            raise RuntimeError(
                f"DuckDB 文件被另一进程占用（通常是正在运行的 bgwq serve）：{self.path}。"
                "请改用工作台/接口（POST /api/report 等）、停止服务，或用 BGWQ_DB_PATH 指定另一个文件。"
            ) from e
        self._lock = threading.RLock()   # DuckDB 连接非线程安全：API 线程池并发访问需加锁
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                self.con.execute(stmt)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info('bars')").fetchall()}
        if "tf" not in cols:                                   # 旧库升级
            self.con.execute("ALTER TABLE bars ADD COLUMN tf VARCHAR DEFAULT '1m'")
            self.con.execute("UPDATE bars SET tf = '1m' WHERE tf IS NULL")

    # ----- bars -----
    def upsert_bars(self, bars: pd.DataFrame) -> int:
        if bars is None or bars.empty:
            return 0
        df = bars.copy()
        if "tf" not in df:
            df["tf"] = "1m"
        df = df[BAR_COLUMNS]
        with self._lock:
            self.con.register("new_bars", df)
            self.con.execute("""
                DELETE FROM bars USING new_bars
                WHERE bars.symbol = new_bars.symbol AND bars.venue = new_bars.venue AND bars.ts = new_bars.ts
                  AND COALESCE(bars.tf, '1m') = new_bars.tf
            """)
            self.con.execute(f"INSERT INTO bars ({', '.join(BAR_COLUMNS)}) SELECT {', '.join(BAR_COLUMNS)} FROM new_bars")
            self.con.unregister("new_bars")
        return len(df)

    def load_bars(self, symbol: str, start: date | None = None, end: date | None = None,
                  venue: str = "native", complete_only: bool = True, tf: str = "1m") -> pd.DataFrame:
        q = "SELECT * FROM bars WHERE symbol = ? AND venue = ? AND COALESCE(tf, '1m') = ?"
        params: list[Any] = [symbol, venue, tf]
        if start:
            q += " AND CAST(ts_ny AS DATE) >= ?"
            params.append(start)
        if end:
            q += " AND CAST(ts_ny AS DATE) <= ?"
            params.append(end)
        if complete_only:
            q += " AND is_complete"
        q += " ORDER BY ts"
        with self._lock:
            df = self.con.execute(q, params).df()
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df

    def available_days(self, symbol: str, venue: str = "native", tf: str = "1m") -> list[date]:
        with self._lock:
            rows = self.con.execute(
                "SELECT DISTINCT CAST(ts_ny AS DATE) d FROM bars WHERE symbol=? AND venue=? AND COALESCE(tf,'1m')=? ORDER BY d",
                [symbol, venue, tf]).fetchall()
        return [r[0] for r in rows]

    def symbols(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self.con.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol").fetchall()]

    def sources_summary(self) -> pd.DataFrame:
        with self._lock:
            return self.con.execute("""
                SELECT symbol, venue, source, COALESCE(tf,'1m') tf, COUNT(*) bars, MIN(ts_ny) first_ts, MAX(ts_ny) last_ts,
                       BOOL_OR(quality & 128 > 0) simulated
                FROM bars GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""").df()

    # ----- generic json -----
    def save_json(self, table: str, **cols: Any) -> None:
        keys = list(cols.keys())
        vals = [json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v for v in cols.values()]
        with self._lock:
            self.con.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})", vals)

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self.con.execute(sql, params or []).df()

    def close(self) -> None:
        self.con.close()
