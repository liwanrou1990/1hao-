"""生肖映射：跨年切换、数量基准、缺失时停止。"""
import numpy as np
import pandas as pd

from marksix import zodiac
from marksix.context import build_ctx
from marksix.stats import rule_stats
from marksix.patterns import RULES


def test_cross_year_switch_at_chinese_new_year():
    zm = zodiac.ZodiacMap.builtin()
    before, after = zm.lookup("2024-02-09"), zm.lookup("2024-02-10")
    assert zodiac.ZODIACS[before[0]] == "兔" and zodiac.ZODIACS[after[0]] == "龙"
    # 公历年初、春节前仍属上一农历年
    assert zodiac.ZODIACS[zm.lookup("2026-01-20")[0]] == "蛇"
    assert zodiac.ZODIACS[zm.lookup("2026-02-17")[0]] == "马"


def test_counts_not_uniform_twelfth():
    arr = zodiac.ZodiacMap.builtin().lookup("2024-06-01")
    c = zodiac.zodiac_counts(arr)
    assert c.sum() == 49 and c[zodiac.ZODIACS.index("龙")] == 5 and sorted(c)[:11] == [4] * 11
    assert [n for n in range(1, 50) if zodiac.ZODIACS[arr[n - 1]] == "龙"] == [1, 13, 25, 37, 49]


def test_missing_mapping_returns_none():
    zm = zodiac.ZodiacMap.builtin()
    assert zm.lookup("2030-06-01") is None and zm.lookup("2000-06-01") is None


def test_mapping_check():
    rows = pd.DataFrame(zodiac.builtin_mapping_rows())
    assert zodiac.check_mapping_frame(rows) == []
    bad = rows[rows["number"] != 7]
    assert zodiac.check_mapping_frame(bad)


def _df(dates, specials):
    return pd.DataFrame({"draw_id": [f"{pd.Timestamp(d).year}/{i+1:03d}" for i, d in enumerate(dates)],
                         "date": pd.to_datetime(dates), "special": specials})


def test_zodiac_baseline_uses_target_period_mapping():
    # 末期在龙年前（兔年），目标期在龙年：兔年中“兔”有 5 个号码，龙年中“兔”只有 4 个
    zm = zodiac.ZodiacMap.builtin()
    d = ["2024-02-01", "2024-02-03", "2024-02-06", "2024-02-13"]
    ctx = build_ctx(_df(d, [5, 1, 13, 2]), zm)  # 兔年: 1、13 → 兔；5 → 猪
    cond, E = RULES["Z-ABB-cont"].compute(ctx)
    assert cond[2]
    assert E[2].sum() == 4                 # 目标期（龙年）“兔”= 2,14,26,38
    assert E[2][2 - 1]                     # 号码 2 在龙年为兔 → 命中


def test_zodiac_analysis_stops_without_mapping_numbers_continue():
    rng = np.random.default_rng(0)
    dates = pd.date_range("2031-01-01", periods=200, freq="3D")
    ctx = build_ctx(_df(dates, rng.integers(1, 50, 200)), zodiac.ZodiacMap.builtin())
    assert not ctx.zok.any()
    tab = rule_stats(ctx, 3, 200, {"tiny": 30, "small": 100, "min_expected": 5})
    z = tab[tab["规则编号"].str.startswith("Z-")]
    assert (z["状态"] == "生肖映射缺失，已停止").all()
    n = tab[tab["规则编号"] == "N01"].iloc[0]
    assert n["触发次数n"] > 100
