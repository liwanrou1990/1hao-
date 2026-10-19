"""阶段三：策略库与机器学习。"""
import numpy as np
import pandas as pd
import pytest

from bgw_quant.costs.model import CostModel, load_cost_profile
from bgw_quant.data.simulated import regime_for_day
from bgw_quant.ml.features import build_features
from bgw_quant.ml.labels import build_labels, forward_net_return
from bgw_quant.ml.predict import ai_signals_for_day
from bgw_quant.ml.train import purged_walk_forward_splits, walk_forward_train
from bgw_quant.strategies.rules import RULE_STRATEGIES, all_strategies


def test_nine_rule_strategies_registered():
    assert set(RULE_STRATEGIES) == {"vwap", "ema_trend", "macd", "rsi", "bollinger", "atr_channel", "breakout",
                                    "mean_reversion", "volume_filter"}
    assert len(all_strategies()) == 9


def test_volume_filter_refuses_without_volume(nvda_day):
    df = nvda_day.copy()
    df["volume"] = np.nan
    from bgw_quant.strategies.rules import make_strategy
    assert make_strategy("volume_filter").compute(df).sum() == 0


def test_features_are_causal(nvda_multi):
    df = nvda_multi.iloc[:800].reset_index(drop=True)
    full = build_features(df)
    part = build_features(df.iloc[:500].reset_index(drop=True))
    pd.testing.assert_frame_equal(full.iloc[:500], part, check_exact=False, atol=1e-9)


def test_labels_use_future_and_not_cross_day(nvda_multi):
    cm = CostModel(load_cost_profile("token_dex"))
    df = nvda_multi.iloc[:390 * 2].reset_index(drop=True)
    r = forward_net_return(df, 30, cm, 10_000)
    day = pd.to_datetime(df["ts_ny"]).dt.date
    last_day1 = day[day == day.iloc[0]].index.max()
    assert r.iloc[last_day1 - 10:last_day1 + 1].isna().all()   # 跨日标签缺失
    # 标签与手算一致
    i = 100
    c = cm.proportional_side_cost() + cm.impact_fraction(10_000)
    gas = 2 * cm.p.gas_usd / 10_000
    expect = df["close"].iloc[i + 30] * (1 - c) / (df["open"].iloc[i + 1] * (1 + c)) - 1 - gas
    assert r.iloc[i] == pytest.approx(expect)
    lab = build_labels(df, cm, 10_000)
    assert {"ret_5", "up_5", "ret_60", "up_60"} <= set(lab.columns)


def test_split_refuses_too_short_training():
    days = list(range(60))
    with pytest.raises(ValueError):
        list(purged_walk_forward_splits(days, train_days=1, test_days=1))
    splits = list(purged_walk_forward_splits(days, train_days=20, test_days=5, embargo_days=2))
    for tr, te in splits:
        assert te[0] - tr[-1] == 3        # embargo 2 天 → 间隔 3
        assert max(tr) < min(te)          # 严格按时间向前


def test_walk_forward_oos_only_and_no_trade_when_no_edge(nvda_multi):
    cm = CostModel(load_cost_profile("token_dex"))
    wf = walk_forward_train(nvda_multi, cm, 10_000, train_days=30, test_days=5, horizons=(5, 30), regime_fn=regime_for_day)
    assert wf.folds and wf.data_is_simulated
    for f in wf.folds:
        assert f.test_days[0] > f.train_days[1]            # 测试集在训练集之后
        assert f.regimes_in_test                          # 记录了测试集市场环境
        assert 0 <= f.brier <= 1
    s = wf.summary()
    assert set(s["horizon_min"]) == {5, 30}
    # 阈值为 None 的模型必须输出不交易
    for h, m in wf.final_models.items():
        if m.threshold is None:
            assert m.decide(np.array([0.99]), np.array([0.01]))[0] == 0
    # 任何“has_edge=True”都必须有正的 95% 置信下界
    for _, row in s.iterrows():
        if row["has_edge"]:
            assert row["ci95"][0] > 0
    last_day = pd.to_datetime(nvda_multi["ts_ny"]).dt.date.iloc[-1]
    day_bars = nvda_multi[pd.to_datetime(nvda_multi["ts_ny"]).dt.date == last_day]
    sig = ai_signals_for_day(day_bars, wf.final_models)
    assert len(sig) == len(day_bars) * 2
    assert sig["reason"].str.contains("不交易|做多").all()
    assert ((sig["decision"] == 1) <= sig["p_up"].notna()).all()
