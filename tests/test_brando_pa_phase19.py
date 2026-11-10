"""第十九阶段：周线只用完整周线；状态与候选无未来数据（截断历史重算结果一致）；引擎次日开盘成交、每笔买入有完整退出计划且风险收益比 ≥ 1.5、
最多 3 批且间隔 ≥ 10 日、趋势失效全部卖出；事件研究独立样本间隔 ≥ 持有期、Wilson 区间；B2 V7 不能绕过数据故障、熔断后 ≥ 30 天；判定；前瞻日志哈希链；只写输出目录。"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.pa16 import data16
from brando_pa.pa18.variants18 import COOLDOWN, RECOVERY
from brando_pa.pa19 import phase19 as P, states19 as S
from brando_pa.pa19.b2rec19 import V7Breaker
from brando_pa.pa19.engine19 import LongTermBacktester

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (data16.RAW16 / "coinbase_btc_usd_1d.csv").exists(), reason="raw snapshots missing")


@pytest.fixture(scope="module")
def ctx():
    d = S.load_daily(); cand, f, w = S.candidates(d)
    return {"d": d, "cand": cand, "f": f, "w": w}


def test_weekly_only_complete_weeks_and_states_have_no_lookahead(ctx):
    d, w = ctx["d"], ctx["w"]; assert w["week_end"].max() <= d["ts"].max()
    cut = pd.Timestamp("2021-06-15", tz="UTC"); w2 = S.weekly(d[d["ts"] <= cut].reset_index(drop=True))
    m = w.merge(w2[["week_end", "state", "trend_failure"]], on="week_end", suffixes=("", "_t"))
    assert len(m) > 300 and (m["state"] == m["state_t"]).all() and (m["trend_failure"] == m["trend_failure_t"]).all()


def test_candidates_have_no_lookahead(ctx):
    d, cand = ctx["d"], ctx["cand"]
    for cut in ("2018-03-01", "2021-07-01", "2024-05-01"):
        t = pd.Timestamp(cut, tz="UTC"); c2, _, _ = S.candidates(d[d["ts"] <= t].reset_index(drop=True))
        a = cand[cand["ts"] <= t][["ts", "type", "entry_ref", "invalidation"]].reset_index(drop=True); b = c2[["ts", "type", "entry_ref", "invalidation"]].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)


def test_engine_rules(ctx):
    f, cand = ctx["f"], ctx["cand"]
    for e, x in (("A", "X1"), ("A", "X4"), ("ABC", "X2"), ("B", "X3")):
        r = LongTermBacktester(f, cand, e, x, 10.0, 5.0, "2014-01-01", None).run(); assert r.final["identity_ok"]
        o = f.set_index(f["ts"])["open"]; buys = r.fills[r.fills["side"] == "BUY"]
        for b in buys.to_dict("records"):
            assert b["ref_price"] == pytest.approx(float(o.loc[b["ts"]])) and b["plan"]["rr_net"] >= 1.5
            assert pd.Timestamp(b["plan"]["decision_date"], tz="UTC") < b["ts"] and {"invalidation", "target_1", "target_2", "max_loss_pct"} <= set(b["plan"])
        nav = r.nav.set_index("ts"); assert nav["exposure"].max() <= 1.0 + 1e-9
        bi = list(pd.DatetimeIndex(buys["ts"]))
        for a_, b_ in zip(bi, bi[1:]):
            if (b_ - a_).days < 10:
                pytest.fail("两批买入间隔 < 10 日")


def test_trend_failure_exits_everything(ctx):
    f, cand = ctx["f"], ctx["cand"]; r = LongTermBacktester(f, cand, "A", "X1", 10.0, 5.0, "2014-01-01", None).run()
    tf = r.fills[r.fills["reason"].str.startswith("趋势失效")]
    for t in tf["ts"]:
        assert r.nav.set_index("ts").loc[t, "holdings"]["BTCUSDT"] <= 1e-10


def test_event_study_independence_and_wilson(ctx):
    ev = P.event_study(ctx["cand"], ctx["f"])
    for typ in ("A", "B", "C"):
        for H in P.HORIZONS:
            st = ev[typ]["stats"][f"{H}d"]; assert st["n_independent"] <= st["n_matured"] <= st["n_raw"]
            if st["n_independent"] < 10:
                assert st["evidence"].startswith("证据不足")
    lo, hi = P.wilson(6, 10); assert 0 < lo < 0.6 < hi < 1
    assert P.wilson(0, 0) == (None, None)


def test_v7_cannot_bypass_data_fault_and_waits_30_days():
    sm = cfg()["trendsys"]["risk"]["state_machine"]; idx = pd.date_range("2024-01-01", periods=120, freq="D", tz="UTC")
    df = pd.DataFrame({"close": 100.0, "sma200": 90.0, "above_streak": 50, "w_close": np.where(idx.dayofweek == 6, 120.0, np.nan), "w_low": np.where(idx.dayofweek == 6, 95.0 + np.arange(120) * 0.1, np.nan), "sma20w": 90.0, "sma40w": 80.0}, index=idx)
    b = V7Breaker(sm, {"early": "weekly40"}, {"BTCUSDT": df}); b.halt_count = 2; b.evaluate(idx[0], 10000.0); b.evaluate(idx[1], 7900.0); b.evaluate(idx[2], 7900.0); assert b.state == COOLDOWN
    for i in range(3, 31):
        b.evaluate(idx[i], 8000.0); assert b.state == COOLDOWN                                          # 熔断后 < 30 天不提前结束
    for i in range(31, 45):
        b.evaluate(idx[i], 8000.0)
    assert b.state == RECOVERY and "early_V7" in b.transitions[-1]["why"]
    c = V7Breaker(sm, {"early": "weekly40"}, {"BTCUSDT": df}); c.evaluate(idx[0], 10000.0); c.evaluate(idx[1], 7900.0); c.set_data_fault(True)
    for i in range(2, 120):
        assert c.evaluate(idx[i], 9000.0).max_exposure == 0.0


def test_judge_rejects_when_failing():
    base = lambda net, mdd, up=1.0: {"net_profit_usdt": net, "max_drawdown": mdd, "up_capture": up, "regime_pnl": {"bull": 100.0, "bear": -100.0, "sideways": 0.0}, "n_round_trips": 3, "kind": "baseline"}
    R = {}
    for wn in P.WINDOWS19:
        for cc in (15, 25):
            R[("BH_BTC", wn, cc)] = base(1000, -0.8); R[("B1_BTC_SMA200", wn, cc)] = base(500, -0.6, 0.6); R[("X_X1", wn, cc)] = base(100, -0.3, 0.3) | {"kind": "phase19"}
    j = P.judge(R)["X_X1"]; assert j["verdict"] == "不采用" and j["reasons"]


def test_forward_log_hash_chain_and_labels(tmp_path):
    from brando_pa.pa19 import current19
    p = tmp_path / "obs.jsonl"; a = current19.append_observation(online=False, path=p); b = current19.append_observation(online=False, path=p)
    assert a["prev_hash"] == "0" * 64 and b["prev_hash"] == a["hash"] and a["readings"]["validated_strategy_signal"] == "无" and "研究性观察" in a["readings"]["label"]


def test_run_writes_only_to_output_dir(tmp_path):
    def h(dd: Path):
        return {str(p.relative_to(dd)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dd.rglob("*")) if p.is_file()} if dd.exists() else {}
    watch = [data16.RAW16, ROOT / "data_store" / "brando_pa" / "phase10_holding", ROOT / "reports_out" / "brando_pa" / "phase8_forward"]; before = [h(x) for x in watch]
    r = P.run_phase19(cfg(), out_dir=tmp_path, windows={"recheck_2022_2026": ("2022-01-01", None)}, costs=[15], strategies=[("A", "X4")])
    assert (tmp_path / "phase19_results.json").exists() and r["real_trading"].startswith("disabled") and [h(x) for x in watch] == before
