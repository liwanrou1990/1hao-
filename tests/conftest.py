import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402
from datetime import date  # noqa: E402

from bgw_quant.data.simulated import SimulatedSource  # noqa: E402
from bgw_quant.data.quality import apply_quality_flags  # noqa: E402

SAMPLE_DAY = date(2026, 10, 16)  # 周五，美股交易日


@pytest.fixture(scope="session")
def sim_source():
    return SimulatedSource()


@pytest.fixture(scope="session")
def nvda_day(sim_source):
    return apply_quality_flags(sim_source.fetch_minute_bars("NVDA", SAMPLE_DAY))


@pytest.fixture(scope="session")
def nvda_multi(sim_source):
    """约 60 个交易日的 NVDA 模拟分钟线，用于策略/ML 测试。"""
    import pandas as pd
    from bgw_quant.calendar_utils import trading_days
    days = trading_days(date(2026, 7, 20), SAMPLE_DAY)
    frames = [apply_quality_flags(sim_source.fetch_minute_bars("NVDA", d)) for d in days]
    return pd.concat(frames, ignore_index=True)
