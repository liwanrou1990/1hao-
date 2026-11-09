"""宏观事件表与过滤（可选模块）。
FOMC：已知会议结束/决议日（美联储公布的年度日历；2018–2026）。CPI / 非农：按 BLS 常规发布规则生成（非农 = 每月首个周五；CPI = 每月 10–15 日的首个工作日），
**标注 approx=True、未逐条核验首次公布时间戳**，仅供日粒度过滤研究；不含任何修订值。宏观事件不产生买入信号。
日粒度语义：pre_event = 事件日与前一日不新开仓；post_event = 事件日不新开仓（事件后第一个收盘重新评估才允许）；none = 不过滤。
本阶段数据频率为日线（1h 仅 2022 起），15/30/60 分钟确认窗口不可支持，报告中明示。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

FOMC_DECISION_DATES = [
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _first_friday(y: int, m: int) -> pd.Timestamp:
    d = pd.Timestamp(year=y, month=m, day=1)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def _cpi_day(y: int, m: int) -> pd.Timestamp:
    d = pd.Timestamp(year=y, month=m, day=10)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def build_events(start: str = "2018-01-01", end: str = "2026-12-31") -> pd.DataFrame:
    rows = [{"date": pd.Timestamp(x), "event": "FOMC", "source": "Federal Reserve FOMC calendar (meeting decision day; 2020-03-03 与 2020-03-15 为紧急会议)", "approx": False, "time_et": "14:00"} for x in FOMC_DECISION_DATES]
    for y in range(2018, 2027):
        for m in range(1, 13):
            rows.append({"date": _first_friday(y, m), "event": "NFP", "source": "rule: first Friday 08:30 ET (BLS Employment Situation); 未逐条核验例外日期", "approx": True, "time_et": "08:30"})
            rows.append({"date": _cpi_day(y, m), "event": "CPI", "source": "rule: first weekday on/after the 10th 08:30 ET (BLS CPI); 未逐条核验", "approx": True, "time_et": "08:30"})
    ev = pd.DataFrame(rows); ev = ev[(ev["date"] >= pd.Timestamp(start)) & (ev["date"] <= pd.Timestamp(end))].sort_values("date").reset_index(drop=True)
    ev["date"] = ev["date"].dt.tz_localize("UTC"); return ev


def save_events(path: Path) -> pd.DataFrame:
    ev = build_events(); path.parent.mkdir(parents=True, exist_ok=True); ev.assign(date=ev["date"].dt.strftime("%Y-%m-%d")).to_csv(path, index=False); return ev


class MacroEventFilter:
    def __init__(self, events: pd.DataFrame | None, mode: str = "none", kinds: tuple = ("FOMC", "CPI", "NFP")):
        self.mode = mode; self.blocked: set = set()
        if events is not None and mode != "none":
            days = set(pd.to_datetime(events[events["event"].isin(kinds)]["date"]).dt.normalize())
            for d in days:
                self.blocked.add(d)
                if mode == "pre_event":
                    self.blocked.add(d - pd.Timedelta(days=1))

    def allows_new_entry(self, ts: pd.Timestamp) -> tuple[bool, str]:
        if self.mode == "none":
            return True, ""
        t = pd.Timestamp(ts).normalize()
        if t in self.blocked:
            return False, f"宏观事件过滤（{self.mode}）：{t.date()} 不新开仓"
        return True, ""
