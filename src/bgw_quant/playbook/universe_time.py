"""统一网格的美东时间字符串。"""
from __future__ import annotations

from datetime import date

from ..rotation.universe import day_grid_utc


def grid_ny_strings(day: str | date) -> list[str]:
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return day_grid_utc(d).tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M:%S").tolist()
