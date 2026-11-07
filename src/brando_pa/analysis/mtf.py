"""跨周期对齐：决策时刻 = 执行周期 K 线收盘时刻；高周期只允许使用“收盘时刻 ≤ 决策时刻”的 K 线。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..schema import tf_seconds


def close_times_ns(ts: pd.Series, tf: str) -> np.ndarray:
    ct = pd.to_datetime(ts, utc=True) + pd.Timedelta(seconds=tf_seconds(tf))
    return ct.dt.tz_convert(None).to_numpy().astype("datetime64[ns]").astype("int64")


def align_last_closed(exec_ts: pd.Series, exec_tf: str, htf_ts: pd.Series, htf_tf: str) -> np.ndarray:
    """对每根执行 K 线（以其收盘时刻为决策点）返回高周期中最后一根已收盘 K 线的索引；无则 -1。"""
    dec = close_times_ns(exec_ts, exec_tf)
    hclose = close_times_ns(htf_ts, htf_tf)
    idx = np.searchsorted(hclose, dec, side="right") - 1
    return idx.astype(int)


def assert_no_lookahead(exec_ts: pd.Series, exec_tf: str, htf_ts: pd.Series, htf_tf: str, idx: np.ndarray) -> None:
    """自检：任一对齐索引对应的高周期收盘时刻必须 ≤ 决策时刻。"""
    dec = close_times_ns(exec_ts, exec_tf); hclose = close_times_ns(htf_ts, htf_tf)
    ok = idx < 0
    ok |= hclose[np.clip(idx, 0, len(hclose) - 1)] <= dec
    if not bool(np.all(ok)):
        raise AssertionError("跨周期对齐出现未来数据")
