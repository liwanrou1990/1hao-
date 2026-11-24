"""第二十六阶段：B 只比 A 少一个条件（收盘 > 支撑区上沿）→ A 触发的日子 B 一定触发且失效价相同；B 的检测只用 ≤ i 的数据；
无成交量时 B 不通过；不影响第二十一阶段冻结、无下单接口。（合成数据只用于检验代码。）"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.pa11.signals import PullbackConfirmation
from brando_pa.pa11.structure import DailyStructureDetector
from brando_pa.pa11.zones import SupportResistanceZones
from brando_pa.pa21 import acceptance21, freeze21
from brando_pa.pa26 import ab26 as M

T = lambda s: pd.Timestamp(s, tz="UTC")


@pytest.fixture(scope="module")
def btc():
    if not (M.DATA_DIR / "okx_BTCUSDT_1d.csv").exists():
        pytest.skip("需要先运行 ab26.fetch()")
    d = M.load()["BTCUSDT"]; d = d[d.ts <= T("2021-12-31")].reset_index(drop=True)
    det = DailyStructureDetector(d); z = SupportResistanceZones(det.sw, None, None, det.atr); return d, det, z


def test_relaxed_is_superset_with_same_invalidation(btc):
    d, det, z = btc; A = PullbackConfirmation(); B = M.PullbackConfirmationRelaxed(); n_a = n_b = 0
    for i in range(250, len(d)):
        zs = z.zones_at(i, det.close[i]); ra = A.check(det, z, i, zs); rb = B.check(det, z, i, zs)
        n_a += ra["signal"]; n_b += rb["signal"]
        if ra["signal"]:
            assert rb["signal"] and rb["invalidation"] == pytest.approx(ra["invalidation"])
        if rb["signal"] and not ra["signal"]:
            assert det.close[i] <= rb["zone"].high                                         # 多出来的只能是“未站回区域上沿”的日子
    assert n_b >= n_a > 0


def test_relaxed_no_future_data(btc):
    d, det, z = btc; B = M.PullbackConfirmationRelaxed()
    for cut in (900, 1100, 1300):
        dd = d.iloc[:cut + 1].reset_index(drop=True); det2 = DailyStructureDetector(dd); z2 = SupportResistanceZones(det2.sw, None, None, det2.atr)
        for i in range(cut - 60, cut + 1):
            r1 = B.check(det, z, i, z.zones_at(i, det.close[i])); r2 = B.check(det2, z2, i, z2.zones_at(i, det2.close[i]))
            assert r1["signal"] == r2["signal"], (cut, i)


def test_missing_volume_fails(btc):
    d, det, z = btc; B = M.PullbackConfirmationRelaxed()
    hits = [i for i in range(250, len(d)) if B.check(det, z, i, z.zones_at(i, det.close[i]))["signal"]]
    assert hits
    d0 = d.copy(); d0.loc[hits, "volume"] = 0.0; det0 = DailyStructureDetector(d0); z0 = SupportResistanceZones(det0.sw, None, None, det0.atr)
    for i in hits:
        assert not B.check(det0, z0, i, z0.zones_at(i, det0.close[i]))["signal"]


def test_judge_rules():
    def row(v, sc, w, net, dd, n):
        return {"variant": v, "scope": sc, "window": w, "net_usdt": net, "max_drawdown": dd, "n_round_trips": n}
    rows = [row(v, sc, w, 100.0 if v == "B" else 50.0, -0.05, 20) for v in "AB" for sc in ("BTC", "ETH", "BTC+ETH") for w in ("样本内", "样本外")]
    cmp_ = {"样本外": {"n_common": 8, "n_B_only": 4}}
    assert M.judge(rows, cmp_)["verdict"].startswith("B 优于")
    rows2 = [r | {"n_round_trips": 3} for r in rows]
    assert M.judge(rows2, cmp_)["verdict"] == "保留 A，继续观察"


def test_no_order_api_and_freeze_untouched():
    assert acceptance21.static_no_order_api(Path(M.__file__).parent)["ok"] and freeze21.verify()["ok"]


def test_handoff_subset_and_only_when_above_confirmed_high(btc):
    from brando_pa.pa11.structure import structure_state
    from brando_pa.pa26 import handoff26 as H
    d, det, z = btc; A = PullbackConfirmation(); B = H.PullbackWithHandoff(); n_h = 0
    for i in range(250, len(d)):
        zs = z.zones_at(i, det.close[i]); ra = A.check(det, z, i, zs); rb = B.check(det, z, i, zs)
        if rb["signal"]:
            assert ra["signal"] and rb["invalidation"] == pytest.approx(ra["invalidation"])
        if rb.get("handoff"):
            st = structure_state(det.sw, i); n_h += 1
            assert st["last_high"] == pytest.approx(rb["H"]) and det.close[st["last_high_idx"] + 1:i + 1].max() > rb["H"]
            assert all(c <= i for c in det.sw[det.sw.idx == st["last_high_idx"]].confirmed_at)          # 只用当时已确认的前高
    assert n_h > 0
