"""合成演示数据生成器（只写入合成数据库，来源固定以 SYNTHETIC 开头）。

用途：流程验证、测试，以及“植入信号”功效检查（确认系统在确有信号时能检测到）。
合成数据的任何结果都不代表真实六合彩。
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd


def make_draws(n: int = 1500, start: str = "2010-01-05", seed: int = 0,
               plant_repeat_prob: float | None = None) -> pd.DataFrame:
    """生成 n 期合成开奖：每周二、四、六开奖；期号按年从 001 起编。

    plant_repeat_prob：若给定，则特别号码以该概率等于上一期特别号码（植入信号）。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n * 3, freq="D")
    dates = dates[dates.dayofweek.isin([1, 3, 5])][:n]
    rows, prev_sp, counter = [], None, {}
    for d in dates:
        y = d.year
        counter[y] = counter.get(y, 0) + 1
        if plant_repeat_prob is not None and prev_sp is not None and rng.random() < plant_repeat_prob:
            sp = prev_sp
            mains = rng.choice([x for x in range(1, 50) if x != sp], 6, replace=False)
        else:
            seven = rng.choice(np.arange(1, 50), 7, replace=False)
            mains, sp = seven[:6], int(seven[6])
        rows.append({"draw_id": f"{y}/{counter[y]:03d}", "draw_date": d.strftime("%Y-%m-%d"),
                     **{f"main_{i+1}": int(v) for i, v in enumerate(sorted(mains))}, "special": int(sp),
                     "source": "SYNTHETIC" + ("-PLANTED" if plant_repeat_prob else "-IID")})
        prev_sp = sp
    return pd.DataFrame(rows)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
