"""封存测试集隔离与一次性使用。"""
import numpy as np
import pytest

from marksix import db, ingest, synthetic
from marksix.analysis import patterns_dev
from marksix.backtest import (SealError, dev_ctx, final_test_status, freeze, get_or_create_seal, load_study,
                              run_dev, run_final_test)
from tests.conftest import load_synthetic

SMALL_GRID = [("M0", {}), ("M1", {"window": 50, "alpha": 5.0}), ("M2", {"rule": "N01", "kappa": 20.0}),
              ("M2b", {"family": "三期方向", "kappa": 500.0})]


def test_split_is_last_20_percent(syn_con, settings):
    load_synthetic(syn_con, n=500)
    df, _, zmap = load_study(syn_con, settings)
    sp = get_or_create_seal(syn_con, df, settings)
    assert sp.n_test == int(np.ceil(0.2 * len(df))) and sp.n_dev + sp.n_test == len(df)
    assert dev_ctx(df, zmap, sp).L == sp.n_dev       # 开发上下文物理上不含测试段
    assert df["date"].iloc[sp.n_dev - 1] <= df["date"].iloc[sp.n_dev]


def test_dev_results_independent_of_test_data(tmp_path, settings):
    """只改变封存测试段的特别号码，开发期形态统计与模型结果必须完全相同。"""
    base = synthetic.make_draws(500, seed=4)
    alt = base.copy()
    n_dev = 500 - 100
    # 测试段改成另一组合法开奖
    other = synthetic.make_draws(500, seed=99)
    for c in ["main_1", "main_2", "main_3", "main_4", "main_5", "main_6", "special"]:
        alt.loc[n_dev:, c] = other.loc[n_dev:, c].values
    outs = []
    for i, d in enumerate([base, alt]):
        con = db.connect("synthetic", tmp_path / f"{i}.sqlite")
        ingest.import_bytes(con, synthetic.to_csv_bytes(d), source=None)
        pat = patterns_dev(con, settings, n_sims=0, record=False)["table"]
        dev = run_dev(con, settings, grid=SMALL_GRID)
        outs.append((pat, [r["logloss"] for r in dev["results"]], dev["split"].n_dev))
    assert outs[0][2] == outs[1][2] == n_dev
    assert outs[0][0].fillna(-1).equals(outs[1][0].fillna(-1))
    assert outs[0][1] == outs[1][1]


def test_final_test_requires_freeze_and_confirm_and_runs_once(syn_con, settings):
    load_synthetic(syn_con, n=500)
    with pytest.raises(SealError):
        run_final_test(syn_con, settings, confirm=True)          # 未冻结
    run_dev(syn_con, settings, grid=SMALL_GRID)
    freeze(syn_con, settings)
    with pytest.raises(SealError):
        run_final_test(syn_con, settings, confirm=False)         # 未确认
    r1 = run_final_test(syn_con, settings, confirm=True)
    assert r1["independent"]
    r2 = run_final_test(syn_con, settings, confirm=True)
    assert not r2["independent"] and "不是独立检验" in r2["conclusion"]
    st = final_test_status(syn_con)
    assert st["n_runs"] == 2 and st["independent_done"]
    notes = syn_con.execute("SELECT notes FROM experiments WHERE phase='final_test' ORDER BY exp_id DESC LIMIT 1").fetchone()[0]
    assert "非独立" in notes


def test_seal_fixed_when_new_draws_arrive(syn_con, settings):
    _, df_all = load_synthetic(syn_con, n=600)
    con = syn_con
    df, _, _ = load_study(con, settings)
    # 用前 500 期创建封存，再以 600 期（追加 100 期新开奖）读取：边界不变，新增部分归入前瞻期
    df500 = df.iloc[:500].reset_index(drop=True)
    sp500 = get_or_create_seal(con, df500, settings)
    sp600 = get_or_create_seal(con, df, settings)
    assert sp600.last_dev_draw == sp500.last_dev_draw and sp600.n_test == sp500.n_test
    assert sp600.n_post == len(df) - 500 and sp600.test_hash_ok


def test_frozen_must_match_dev_data(syn_con, settings):
    load_synthetic(syn_con, n=500)
    run_dev(syn_con, settings, grid=SMALL_GRID)
    freeze(syn_con, settings)
    # 更正一条开发段记录 → 冻结失效
    did = db.current_draws(syn_con)["draw_id"].iloc[100]
    row = db.current_draws(syn_con).iloc[100]
    sp = (int(row["special"]) % 49) + 1
    mains = [int(row[f"main_{i}"]) for i in range(1, 7)]
    if sp in mains:
        sp = next(x for x in range(1, 50) if x not in mains and x != int(row["special"]))
    csv = ("draw_id,draw_date,main_1,main_2,main_3,main_4,main_5,main_6,special,source\n"
           f"{did},{row['draw_date']},{','.join(map(str, mains))},{sp},SYNTHETIC-FIX\n").encode()
    r = ingest.import_bytes(syn_con, csv, source=None, correction_reason="测试更正")
    assert r.corrected == [did]
    with pytest.raises(SealError):
        run_final_test(syn_con, settings, confirm=True)
