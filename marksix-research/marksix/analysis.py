"""开发期形态分析入口（只使用开发段），以及实验记录。"""
from __future__ import annotations

import json

import pandas as pd

from .backtest import _record, _rows_hash, dev_ctx, get_or_create_seal, load_study
from .patterns import registry_hash
from .stats import maxT_adjust, rule_stats


def patterns_dev(con, settings: dict, n_sims: int | None = None, progress=None, record: bool = True) -> dict:
    df, regime, zmap = load_study(con, settings)
    split = get_or_create_seal(con, df, settings)
    if split is None:
        raise RuntimeError("研究样本不足 50 期")
    ctx = dev_ctx(df, zmap, split)
    lo, hi = 3, split.n_dev
    tab = rule_stats(ctx, lo, hi, settings["small_sample"])
    sim = settings["simulation"]
    n_sims = sim["n_sims"] if n_sims is None else n_sims
    if n_sims > 0:
        adj, mx = maxT_adjust(ctx, lo, hi, n_sims, sim["seed"], progress=progress)
        tab["maxT校正p"] = tab["规则编号"].map(adj)
    else:
        mx = None
    if record:
        _record(con, "patterns", "registered_rules", {"registry_hash": registry_hash(), "n_sims": n_sims,
                                                       "target_range": [str(ctx.draw_ids[lo]), str(ctx.draw_ids[hi - 1])]},
                settings, _rows_hash(df.iloc[:split.n_dev]), "ok",
                {"table": json.loads(tab.to_json(orient="records", force_ascii=False))})
    return {"table": tab, "ctx": ctx, "split": split, "regime": regime, "range": (lo, hi), "max_dist": mx,
            "zmap": zmap}


def experiments_frame(con) -> pd.DataFrame:
    return pd.read_sql_query("SELECT exp_id, created_at, phase, model, params, status, code_version, settings_hash,"
                             " data_hash, notes FROM experiments ORDER BY exp_id DESC", con)


def pattern_verdict(tab: pd.DataFrame) -> str:
    if "maxT校正p" not in tab:
        return "未运行族错误率校正，不作结论。"
    sig = tab[(tab["maxT校正p"] < 0.05) & (tab["状态"] == "可计算")]
    if sig.empty:
        return "开发期内没有任何登记形态在族错误率（maxT）校正后显著偏离随机基准：未发现可靠形态信号。"
    return ("开发期内以下登记形态在 maxT 校正后 p<0.05：" + "、".join(sig["规则编号"]) +
            "。这只是开发期候选信号，必须由封存测试集独立确认，不构成推荐。")
