"""第二十五阶段：关机币价公式与截图口径一致；主流机型滞后规则；7 天平滑只用当日及以前；事件 180 天合并；收益 / 成本 / 减半到期；不影响第二十一阶段冻结。
（合成数据只用于检验代码。）"""
import numpy as np
import pandas as pd
import pytest

from brando_pa.pa21 import acceptance21, freeze21
from brando_pa.pa25 import shutdown25 as S

T = lambda s: pd.Timestamp(s, tz="UTC")


def test_formula_matches_app_screenshot_units():
    implied = 13.5 * 24 / 1000 * 0.50 / 346183.47                     # 截图：S21 XP 13.5 W/T、0.50 元/度 → ¥346,183
    assert S.shutdown_price(implied, 13.5, 0.50) == pytest.approx(346183.47)
    assert S.shutdown_price(4.5e-7, 17.5, 0.07) == pytest.approx(17.5 * 24 / 1000 * 0.07 / 4.5e-7)


def test_mainstream_lag_rule():
    assert S.mainstream(T("2014-10-31"), 12) is None and S.mainstream(T("2014-11-01"), 12)[0] == "Antminer S1"
    assert S.mainstream(T("2018-12-01"), 12)[0] == "Antminer S9" and S.mainstream(T("2020-04-30"), 12)[0] == "Antminer S17"
    assert S.mainstream(T("2026-11-15"), 12)[0] == "Antminer S21 XP" and S.mainstream(T("2026-11-15"), 24)[0] == "Antminer S21"


def _fake(tmp_path, n=900, crash_at=400):
    idx = pd.date_range(T("2019-01-01"), periods=n, freq="D"); px = np.full(n, 10_000.0); px[crash_at:crash_at + 10] = 3_000.0; px[crash_at + 10:] = np.linspace(6_000, 20_000, n - crash_at - 10)
    import json
    for c, y in (("market-price", px), ("miners-revenue", px * 1800), ("hash-rate", np.full(n, 1e8))):
        (tmp_path / f"{c}.json").write_text(json.dumps({"values": [{"x": int(t.timestamp()), "y": float(v)} for t, v in zip(idx, y)]}))
    return S.load(tmp_path)


def test_smoothing_uses_only_past(tmp_path):
    d = _fake(tmp_path); cut = d.iloc[:520].copy()
    full = d["btc_per_th_day"].iloc[:520]; part = (cut["rev_btc"].rolling(7, min_periods=4).mean() / cut["hash-rate"].rolling(7, min_periods=4).mean())
    pd.testing.assert_series_equal(full, part, check_names=False)


def test_events_merge_and_outcomes(tmp_path):
    d = _fake(tmp_path); x = S.series(d, 12, 0.05); x["shutdown"] = 5_000.0; x["touch"] = x["price"] <= x["shutdown"]
    ev = S.events(x, d["price"]); assert len(ev) == 1 and ev[0]["touch_days_in_episode"] == 10            # 10 天连续触及只算一次
    e = ev[0]; ent = T(e["entry_day"]); p = d["price"]
    assert e["ret_365"] == pytest.approx(p[ent + pd.Timedelta(days=365)] * (1 - S.SIDE) / (p[ent] * (1 + S.SIDE)) - 1)
    assert e["next_halving"] == "2020-05-11" and e["ret_halving"] == pytest.approx(p[T("2020-05-11")] * (1 - S.SIDE) / (p[ent] * (1 + S.SIDE)) - 1)


def test_judge_rules():
    ev = [{"ret_365": 0.5, "ret_halving": 0.2}, {"ret_365": 0.1, "ret_halving": -0.1}]
    assert S.judge(ev, {"win_365": 1.0}, {"win_365": 0.7})["verdict"].startswith("证据不足")
    ev3 = ev + [{"ret_365": 0.3, "ret_halving": 0.4}]
    assert S.judge(ev3, {"win_365": 0.9}, {"win_365": 0.7})["verdict"].startswith("支持") and S.judge(ev3, {"win_365": 0.6}, {"win_365": 0.7})["verdict"] == "不支持"


def test_no_order_api_and_freeze_untouched():
    from pathlib import Path
    assert acceptance21.static_no_order_api(Path(S.__file__).parent)["ok"] and freeze21.verify()["ok"]
