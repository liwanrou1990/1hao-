"""美国股市交易日历（America/New_York）。周末与休市日不得生成原生股票K线。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


@lru_cache(maxsize=1)
def _xnys():
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def is_trading_day(d: date) -> bool:
    return bool(_xnys().is_session(pd.Timestamp(d)))


def trading_days(start: date, end: date) -> list[date]:
    cal = _xnys()
    sessions = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [s.date() for s in sessions]


def previous_trading_day(d: date) -> date:
    cal = _xnys()
    ts = pd.Timestamp(d)
    if cal.is_session(ts):
        return cal.previous_session(ts).date()
    return cal.date_to_session(ts, direction="previous").date()


def session_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """返回该交易日常规时段 [09:30, 16:00) 的 UTC 边界。含早收盘日处理。"""
    cal = _xnys()
    ts = pd.Timestamp(d)
    if not cal.is_session(ts):
        raise ValueError(f"{d} 不是美股交易日，不得生成原生股票K线")
    open_ = cal.session_open(ts).to_pydatetime()
    close_ = cal.session_close(ts).to_pydatetime()
    return open_.astimezone(UTC), close_.astimezone(UTC)


def regular_minute_index(d: date) -> pd.DatetimeIndex:
    """该交易日所有已完成分钟K线的起始时间（UTC），最后一根为收盘前一分钟。"""
    o, c = session_bounds_utc(d)
    return pd.date_range(o, c - timedelta(minutes=1), freq="1min", tz="UTC")


def to_ny(ts: pd.Series | pd.DatetimeIndex) -> pd.Series | pd.DatetimeIndex:
    return ts.tz_convert(NY)


def ny_date_of(ts_utc: pd.Timestamp) -> date:
    return ts_utc.tz_convert(NY).date()
