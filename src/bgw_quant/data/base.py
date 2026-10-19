"""数据源可插拔接口。live 与 simulated 实现同一协议，调用方不可能把模拟数据当真。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass
class ProbeResult:
    source: str
    ok: bool
    detail: str
    has_credentials: bool = False
    endpoints: dict[str, str] = field(default_factory=dict)   # endpoint -> status 文本
    history_span: str | None = None
    rate_limit: str | None = None
    cost_note: str | None = None
    blocked: bool = False      # True 表示因权限/网络而阻塞，不得伪装完成


@runtime_checkable
class MarketDataSource(Protocol):
    name: str
    is_simulated: bool

    def probe(self) -> ProbeResult: ...

    def fetch_minute_bars(self, symbol: str, day: date) -> pd.DataFrame:
        """返回该交易日的分钟K线（标准列），不可包含未完成K线。"""
        ...
