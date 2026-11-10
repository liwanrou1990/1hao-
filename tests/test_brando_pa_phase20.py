"""第二十阶段：100% BTC 配置与 BTC 买入持有逐位一致；执行模型（数量精度、冲击、无成交量时 25 bps、波动率只用前一日）；
真实重算不是简单 ×10（冲击按平方根随规模增长）；三种维护方式的交易时点；动态回撤与预警；只写独立目录、不触碰 Mac 账户与旧结果。"""
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brando_pa.config import cfg
from brando_pa.pa16 import data16
from brando_pa.pa20 import RESEARCH_INITIAL_USDT, phase20 as P
from brando_pa.pa20.alloc20 import AllocBacktester, metrics
from brando_pa.pa20.execution20 import FALLBACK_BPS, STEP, liquidity_table, make_ledger

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (data16.RAW16 / "coinbase_btc_usd_1d.csv").exists(), reason="raw snapshots missing")


@pytest.fixture(scope="module")
def ctx():
    return P.Ctx()


def test_research_capital_is_100k_and_alloc100_equals_buyhold(ctx):
    assert RESEARCH_INITIAL_USDT == 100_000.0 and ctx.initial == 100_000.0
    a = P.run_one(ctx, "ALLOC_100_N", 15, "2014-01-01", None); b = P.run_one(ctx, "BH_BTC", 15, "2014-01-01", None)
    assert a["final_usdt"] == pytest.approx(b["final_usdt"], rel=1e-12) and a["initial_usdt"] == 100_000.0


def test_execution_model_rounding_fallback_and_sigma_uses_prior_days(ctx):
    liq = ctx.liq_btc["BTCUSDT"]; c = ctx.btc.set_index(pd.to_datetime(ctx.btc["ts"], utc=True))["close"]
    t = pd.Timestamp("2020-06-15", tz="UTC"); lr = np.log(c).diff()
    assert liq.loc[t, "sigma"] == pytest.approx(lr.loc[:t - pd.Timedelta(days=1)].tail(20).std())        # 只用前一日及以前
    L = make_ledger(100_000.0, 10.0, 5.0, ctx.liq_btc); lot = L.buy("BTCUSDT", 1.234567891, 9000.0, t, "x", 0, 0, 0)
    assert abs(lot.qty / STEP["BTCUSDT"] - round(lot.qty / STEP["BTCUSDT"])) < 1e-6 and L.impacts[-1]["impact_bps"] > 0
    L2 = make_ledger(100_000.0, 10.0, 5.0, ctx.liq_btc); L2.buy("BTCUSDT", 1.0, 500.0, pd.Timestamp("2014-06-01", tz="UTC"), "x", 0, 0, 0)
    assert L2.impacts[-1]["impact_bps"] == FALLBACK_BPS and "无成交量" in L2.impacts[-1]["flag"]


def test_real_rerun_is_not_simple_x10(ctx):
    m100 = P.run_one(ctx, "B1_BTC_SMA200", 15, "2016-01-01", "2021-12-31", initial=100_000.0); m10 = P.run_one(ctx, "B1_BTC_SMA200", 15, "2016-01-01", "2021-12-31", initial=10_000.0)
    assert m100["impact_usdt"] > 10 * m10["impact_usdt"] * 1.5                                         # 平方根冲击：规模放大 10 倍，冲击成本远超 10 倍
    assert m100["final_usdt"] < 10 * m10["final_usdt"]


def test_maintenance_rules_trade_only_when_specified(ctx):
    for mode in ("N", "Y", "D"):
        bt = AllocBacktester(ctx.btc, 0.5, mode, 10.0, 5.0, "2019-01-01", "2021-12-31", 100_000.0, ctx.liq_btc); res = bt.run(); f = res.fills
        if mode == "N":
            assert len(f) == 1
        if mode == "Y":
            later = pd.to_datetime(f["ts"].iloc[1:], utc=True)
            assert all(t.dayofyear == 1 for t in later)                                                 # 只在每年第一个交易日
        if mode == "D":
            nav = res.nav.set_index("ts")
            for t in pd.to_datetime(f["ts"].iloc[1:], utc=True):
                prev = nav.index[nav.index.get_loc(t) - 1]; assert abs(nav.loc[prev, "exposure"] - 0.5) > 0.10
    z = AllocBacktester(ctx.btc, 0.0, "N", 10.0, 5.0, "2019-01-01", None, 100_000.0, ctx.liq_btc).run(); assert len(z.fills) == 0 and z.final["liquidation_usdt"] == 100_000.0


def test_dynamic_drawdown_and_warnings(ctx):
    m = P.run_one(ctx, "ALLOC_100_N", 15, "2017-12-01", "2019-06-30")
    assert m["max_drawdown"] < -0.5 and m["warnings"]["35%"]["hit"] and m["warnings"]["50%"]["hit"] and m["within_50pct_budget"] is False
    assert m["warnings"]["35%"]["first_date"] <= m["warnings"]["50%"]["first_date"]


def test_run_writes_only_to_new_dir(tmp_path):
    def h(d: Path):
        return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}
    watch = [ROOT / "reports_out" / "brando_pa" / "phase8_forward", ROOT / "reports_out" / "brando_pa" / "phase19_longterm", ROOT / "docs" / "brando_pa_validation" / "phase19_longterm", data16.RAW16]; before = [h(x) for x in watch]
    r = P.run_phase20(cfg(), out_dir=tmp_path, names=["ALLOC_50_Y", "BH_BTC"], windows={"recheck_2022_2026": ("2022-01-01", None)}, rolling=False)
    assert (tmp_path / "phase20_results.json").exists() and r["research_initial_usdt"] == 100_000.0 and r["mac_accounts_initial_usdt_unchanged"] == 10_000.0
    assert [h(x) for x in watch] == before


def test_dashboards_default_100k_and_label_mac_accounts(ctx, tmp_path):
    from brando_pa.pa20 import dashboard20
    import shutil
    shutil.copy(P.OUT_DIR / "phase20_results.json", tmp_path / "phase20_results.json") if (P.OUT_DIR / "phase20_results.json").exists() else pytest.skip("phase20 results not generated")
    html = dashboard20.build(res_dir=tmp_path, out=tmp_path, ctx=ctx).read_text(encoding="utf-8")
    assert "研究账户 · 100,000 USDT" in html and "Mac 运行账户 · 10,000 USDT × 3" in html and "立即买入" not in html
