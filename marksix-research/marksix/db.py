"""SQLite 存储层。

隔离原则：每个数据库文件在 meta 表里写入 dataset_kind（real / synthetic），
打开时必须声明类型，类型不符立即报错，从而防止真实数据与合成数据混用。
开奖记录按 (期号, 版本) 存储，更正只追加新版本，旧版本保留并标记为已被取代。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    filename TEXT,
    fetched_at TEXT,
    imported_at TEXT NOT NULL,
    n_bytes INTEGER NOT NULL,
    content BLOB NOT NULL,
    note TEXT
);

-- 正码 main_1..main_6 与特别号码 special 分列保存；分析只取 special 作为样本。
CREATE TABLE IF NOT EXISTS draws (
    draw_id TEXT NOT NULL,          -- 完整期号，如 2024/001
    version INTEGER NOT NULL,       -- 数据版本：1 为首次导入，更正后递增
    draw_date TEXT NOT NULL,        -- 开奖时间（YYYY-MM-DD 或 YYYY-MM-DD HH:MM）
    main_1 INTEGER NOT NULL, main_2 INTEGER NOT NULL, main_3 INTEGER NOT NULL,
    main_4 INTEGER NOT NULL, main_5 INTEGER NOT NULL, main_6 INTEGER NOT NULL,
    special INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT,                -- 采集时间
    snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(snapshot_id),
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (draw_id, version)
);
CREATE INDEX IF NOT EXISTS idx_draws_current ON draws(is_current, draw_date);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draw_id TEXT NOT NULL, old_version INTEGER NOT NULL, new_version INTEGER NOT NULL,
    reason TEXT NOT NULL, snapshot_id INTEGER NOT NULL, diff TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER, created_at TEXT NOT NULL, source TEXT,
    n_rows INTEGER, n_inserted INTEGER, n_identical INTEGER, n_conflicts INTEGER,
    n_corrected INTEGER, n_invalid INTEGER, details TEXT
);

CREATE TABLE IF NOT EXISTS zodiac_map (
    map_version TEXT NOT NULL, number INTEGER NOT NULL, zodiac TEXT NOT NULL,
    valid_from TEXT NOT NULL, valid_to TEXT NOT NULL,
    source TEXT NOT NULL, note TEXT,
    PRIMARY KEY (map_version, number, valid_from)
);

CREATE TABLE IF NOT EXISTS seal (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    created_at TEXT NOT NULL, data_hash TEXT NOT NULL,
    n_total INTEGER NOT NULL, n_dev INTEGER NOT NULL, n_test INTEGER NOT NULL,
    last_dev_draw TEXT NOT NULL, first_test_draw TEXT NOT NULL,
    test_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    exp_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    phase TEXT NOT NULL,            -- dev / final_test / prospective / patterns
    model TEXT NOT NULL,
    params TEXT NOT NULL,
    code_version TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    data_hash TEXT NOT NULL,
    status TEXT NOT NULL,           -- ok / failed
    metrics TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    exp_id INTEGER NOT NULL, draw_id TEXT NOT NULL, draw_date TEXT NOT NULL,
    probs TEXT NOT NULL,            -- 49 个号码概率（JSON）
    actual INTEGER NOT NULL, logloss REAL NOT NULL,
    PRIMARY KEY (exp_id, draw_id)
);

CREATE TABLE IF NOT EXISTS frozen_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    candidates TEXT NOT NULL, dev_exp_ids TEXT NOT NULL, data_hash TEXT NOT NULL, code_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS final_test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    frozen_id INTEGER NOT NULL, independent INTEGER NOT NULL, exp_ids TEXT NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS prospective (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,       -- 预测生成时间（必须早于开奖）
    after_draw_id TEXT NOT NULL,    -- 基于截至此期的数据
    model TEXT NOT NULL, params TEXT NOT NULL, probs TEXT NOT NULL,
    code_version TEXT NOT NULL, data_hash TEXT NOT NULL,
    target_draw_id TEXT, actual INTEGER, logloss REAL, scored_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KindMismatch(RuntimeError):
    pass


def connect(kind: str, path: Path | None = None) -> sqlite3.Connection:
    """打开指定类型数据库；若文件已标记为另一类型则拒绝。"""
    p = Path(path) if path else config.db_path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key='dataset_kind'").fetchone()
    if row is None:
        con.execute("INSERT INTO meta(key, value) VALUES('dataset_kind', ?)", (kind,))
        con.commit()
    elif row[0] != kind:
        con.close()
        raise KindMismatch(f"数据库 {p} 标记为 {row[0]}，不能作为 {kind} 使用（真实/合成隔离）")
    return con


def dataset_kind(con: sqlite3.Connection) -> str:
    return con.execute("SELECT value FROM meta WHERE key='dataset_kind'").fetchone()[0]


DRAW_COLS = ["draw_id", "version", "draw_date", "main_1", "main_2", "main_3", "main_4",
             "main_5", "main_6", "special", "source", "fetched_at", "snapshot_id"]


def current_draws(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        f"SELECT {', '.join(DRAW_COLS)} FROM draws WHERE is_current=1", con
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["draw_date"].str.slice(0, 10))
    df = df.sort_values(["date", "draw_id"]).reset_index(drop=True)
    return df


def data_hash(df: pd.DataFrame) -> str:
    """当前数据版本哈希：只依赖期号、日期、正码、特别号码和版本号。"""
    if df is None or df.empty:
        return "empty"
    cols = ["draw_id", "version", "draw_date", "main_1", "main_2", "main_3", "main_4", "main_5", "main_6", "special"]
    blob = df[cols].sort_values("draw_id").to_csv(index=False).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
