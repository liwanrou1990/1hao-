"""生肖映射。

说明（必须阅读）：
* 生肖只是号码的一种分类方式，本系统不假设其为香港赛马会的官方独立投注项目。
* 本模块内置的映射采用民间通行口径：号码 1 对应该农历年的生肖，号码递增时生肖按
  “鼠牛虎兔龙蛇马羊猴鸡狗猪”逆序排列，即每 12 个号码循环一次。49 个号码中，
  本命生肖有 5 个号码（1、13、25、37、49），其余 11 个生肖各 4 个号码。
  该口径并非官方资料，来源标注为“民间通行口径”，状态为“待与可信来源核对”。
  用户可通过 CSV 导入其他已核验的映射版本，替换内置版本。
* 跨年切换：以农历正月初一（含当日）为新生肖年起点。下表为公历的春节日期。
  历史每期使用开奖当日所在农历年的映射，不套用当年生肖表。
* 开奖日期不在任何映射有效期内时返回缺失，生肖分析停止，号码分析不受影响。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

ZODIACS = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]

# 农历正月初一的公历日期（春节）。
CHINESE_NEW_YEAR = {
    2001: date(2001, 1, 24), 2002: date(2002, 2, 12), 2003: date(2003, 2, 1), 2004: date(2004, 1, 22),
    2005: date(2005, 2, 9), 2006: date(2006, 1, 29), 2007: date(2007, 2, 18), 2008: date(2008, 2, 7),
    2009: date(2009, 1, 26), 2010: date(2010, 2, 14), 2011: date(2011, 2, 3), 2012: date(2012, 1, 23),
    2013: date(2013, 2, 10), 2014: date(2014, 1, 31), 2015: date(2015, 2, 19), 2016: date(2016, 2, 8),
    2017: date(2017, 1, 28), 2018: date(2018, 2, 16), 2019: date(2019, 2, 5), 2020: date(2020, 1, 25),
    2021: date(2021, 2, 12), 2022: date(2022, 2, 1), 2023: date(2023, 1, 22), 2024: date(2024, 2, 10),
    2025: date(2025, 1, 29), 2026: date(2026, 2, 17), 2027: date(2027, 2, 6),
}

BUILTIN_VERSION = "convention_cny_v1"
BUILTIN_SOURCE = "民间通行口径（号码1=当年生肖，逆序循环；春节切换），非官方，待与可信来源核对"


def year_zodiac_index(lunar_year: int) -> int:
    return (lunar_year - 4) % 12  # 2008 鼠年 → 0


def number_zodiac_index(number: int, lunar_year: int) -> int:
    return (year_zodiac_index(lunar_year) - (number - 1)) % 12


def builtin_mapping_rows() -> list[dict]:
    rows = []
    years = sorted(CHINESE_NEW_YEAR)
    for y, nxt in zip(years[:-1], years[1:]):
        start, end = CHINESE_NEW_YEAR[y], CHINESE_NEW_YEAR[nxt] - timedelta(days=1)
        for n in range(1, 50):
            rows.append({"map_version": BUILTIN_VERSION, "number": n, "zodiac": ZODIACS[number_zodiac_index(n, y)],
                         "valid_from": start.isoformat(), "valid_to": end.isoformat(), "source": BUILTIN_SOURCE,
                         "note": f"农历{y}年（{ZODIACS[year_zodiac_index(y)]}年）"})
    return rows


def install_builtin(con: sqlite3.Connection) -> int:
    rows = builtin_mapping_rows()
    con.executemany(
        "INSERT OR IGNORE INTO zodiac_map(map_version, number, zodiac, valid_from, valid_to, source, note)"
        " VALUES (:map_version, :number, :zodiac, :valid_from, :valid_to, :source, :note)", rows)
    con.commit()
    return len(rows)


def import_mapping_csv(con: sqlite3.Connection, path, map_version: str, source: str) -> int:
    """导入外部映射：列 number, zodiac, valid_from, valid_to。会校验每个有效期覆盖 1-49 且无重叠。"""
    df = pd.read_csv(path, dtype=str)
    df["number"] = df["number"].astype(int)
    errs = check_mapping_frame(df)
    if errs:
        raise ValueError("映射校验失败: " + "; ".join(errs[:10]))
    df["map_version"], df["source"], df["note"] = map_version, source, df.get("note", "")
    con.executemany(
        "INSERT INTO zodiac_map(map_version, number, zodiac, valid_from, valid_to, source, note)"
        " VALUES (:map_version, :number, :zodiac, :valid_from, :valid_to, :source, :note)",
        df.to_dict("records"))
    con.commit()
    return len(df)


def check_mapping_frame(df: pd.DataFrame) -> list[str]:
    errs = []
    for (vf, vt), g in df.groupby(["valid_from", "valid_to"]):
        if sorted(g["number"].tolist()) != list(range(1, 50)):
            errs.append(f"{vf}~{vt} 未完整覆盖 1-49")
        bad = set(g["zodiac"]) - set(ZODIACS)
        if bad:
            errs.append(f"{vf}~{vt} 存在未知生肖 {bad}")
    periods = sorted(set(zip(df["valid_from"], df["valid_to"])))
    for (a1, b1), (a2, b2) in zip(periods[:-1], periods[1:]):
        if a2 <= b1:
            errs.append(f"有效期重叠: {a1}~{b1} 与 {a2}~{b2}")
    return errs


class ZodiacMap:
    """按日期查询映射。lookup(date) → 长度 49 的生肖索引数组（号码 n 对应下标 n-1），或 None。"""

    def __init__(self, df: pd.DataFrame, version: str):
        self.version = version
        self.source = df["source"].iloc[0] if not df.empty else ""
        self.periods = []
        for (vf, vt), g in df.groupby(["valid_from", "valid_to"]):
            arr = np.full(49, -1, dtype=int)
            for n, z in zip(g["number"], g["zodiac"]):
                arr[int(n) - 1] = ZODIACS.index(z)
            self.periods.append((pd.Timestamp(vf), pd.Timestamp(vt), arr))
        self.periods.sort(key=lambda x: x[0])

    @classmethod
    def from_db(cls, con: sqlite3.Connection, version: str | None = None) -> "ZodiacMap | None":
        if version is None:
            v = con.execute("SELECT map_version FROM zodiac_map ORDER BY map_version LIMIT 1").fetchone()
            if v is None:
                return None
            version = v[0]
        df = pd.read_sql_query("SELECT * FROM zodiac_map WHERE map_version=?", con, params=(version,))
        return cls(df, version) if not df.empty else None

    @classmethod
    def builtin(cls) -> "ZodiacMap":
        return cls(pd.DataFrame(builtin_mapping_rows()), BUILTIN_VERSION)

    @property
    def coverage(self):
        return (self.periods[0][0], self.periods[-1][1]) if self.periods else (None, None)

    def lookup(self, d) -> np.ndarray | None:
        d = pd.Timestamp(d).normalize()
        for vf, vt, arr in self.periods:
            if vf <= d <= vt:
                return arr
        return None

    def matrix(self, dates) -> tuple[np.ndarray, np.ndarray]:
        """返回 (映射矩阵 n×49，缺失时为 -1；是否有映射 bool 数组)。"""
        out = np.full((len(dates), 49), -1, dtype=int)
        ok = np.zeros(len(dates), dtype=bool)
        for i, d in enumerate(dates):
            arr = self.lookup(d)
            if arr is not None:
                out[i], ok[i] = arr, True
        return out, ok


def zodiac_counts(arr49: np.ndarray) -> np.ndarray:
    """某期映射下每个生肖对应的号码数量（长度 12）。基准概率 = 数量/49。"""
    return np.bincount(arr49, minlength=12)
