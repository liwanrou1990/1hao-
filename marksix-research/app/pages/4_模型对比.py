import common  # noqa: F401
import json

import pandas as pd
import streamlit as st

from common import setup
from marksix.backtest import final_test_status, freeze, latest_frozen, run_dev, run_final_test
from marksix.report import research_md, write_all

con, settings, kind = setup("模型对比")
st.caption("主要指标（预先固定）：log loss，越小越好；均匀基准 ln49 ≈ 3.8918。差值 = 模型 − M0，负值代表优于均匀分布。"
           "置信区间用 Newey–West HAC 标准误（考虑序列相关）。")


def metrics_frame(phase):
    rows = pd.read_sql_query("SELECT exp_id, created_at, model, params, metrics, notes FROM experiments"
                             " WHERE phase=? AND status='ok' ORDER BY exp_id", con, params=(phase,))
    out = []
    for r in rows.itertuples():
        m = json.loads(r.metrics)
        d = m["diff_vs_M0"]
        out.append({"实验": r.exp_id, "模型": r.model, "参数": r.params, "期数": m["n"], "logloss": m["logloss"],
                    "差值vsM0": d["mean"], "95%下限": d["lo"], "95%上限": d["hi"],
                    **({"Bonf下限": m["diff_vs_M0_bonf"]["lo"], "Bonf上限": m["diff_vs_M0_bonf"]["hi"],
                        "独立": "是" if m.get("independent") else "否"} if "diff_vs_M0_bonf" in m else {}),
                    "Brier": m["brier"], "Top1": m["top1"], "Top5": m["top5"], "Top10": m["top10"],
                    "生肖logloss": m.get("zodiac_logloss"), "生肖基准logloss": m.get("zodiac_logloss_base"),
                    "生肖Brier": m.get("zodiac_brier"), "生肖基准Brier": m.get("zodiac_brier_base"), "ECE": m["ece"]})
    return pd.DataFrame(out)


tab_dev, tab_test, tab_pro = st.tabs(["开发期", "封存测试（一次性）", "前瞻"])
with tab_dev:
    st.markdown("开发段内滚动预测：每期先用已知结果预测下一期，再读取结果。**调参只在这里进行**。"
                "在同一开发段上挑最优配置存在选择偏差，开发期结果不能作为结论。")
    if st.button("运行开发期回测（全部配置）"):
        bar = st.progress(0.0)
        try:
            run_dev(con, settings, progress=lambda f, m: bar.progress(min(f, 1.0), m))
            st.success("完成")
        except Exception as e:
            st.error(str(e))
    d = metrics_frame("dev")
    if d.empty:
        st.write("尚无开发期结果。")
    else:
        st.dataframe(d.sort_values("logloss"), hide_index=True)
        best = d.sort_values("logloss").groupby("模型").head(1).set_index("模型")
        st.markdown("**各模型族开发期最优配置的差值 vs M0**")
        st.bar_chart(best["差值vsM0"])
    fz = latest_frozen(con)
    if fz:
        st.markdown(f"**已冻结候选**（{fz['created_at']}）")
        st.dataframe(pd.DataFrame(fz["candidates"]), hide_index=True)
    if not d.empty and st.button("冻结每个模型族的开发期最优配置"):
        st.json(freeze(con, settings))

with tab_test:
    fts = final_test_status(con)
    st.markdown("封存测试集为按时间排列的最后 20%，开发期间不可查看。冻结候选后只运行一次。"
                "第一次之后的任何运行都会被标记为**非独立**。")
    if fts["n_runs"]:
        st.warning(f"封存测试已运行 {fts['n_runs']} 次。只有第一次是独立检验。")
    ok = st.checkbox("我确认候选已冻结，之后不会再用测试结果调参")
    if st.button("运行封存测试", disabled=not ok):
        try:
            res = run_final_test(con, settings, confirm=True)
            st.info(res["conclusion"])
        except Exception as e:
            st.error(str(e))
    t = metrics_frame("final_test")
    if t.empty:
        st.write("封存测试尚未运行。")
    else:
        st.dataframe(t, hide_index=True)
        first = t[t["独立"] == "是"]
        if not first.empty:
            win = first[(first["模型"] != "M0") & (first["Bonf上限"] < 0)]
            if win.empty:
                st.success("结论：未发现可靠预测优势（所有候选在 Bonferroni 校正后均未显著优于 M0）。")
            else:
                st.warning("以下候选 Bonferroni 区间上限 < 0：" + "、".join(win["模型"]) +
                           "。这只是一次样本外检验，仍需前瞻复核，不构成投注建议。")

with tab_pro:
    pro = pd.read_sql_query("SELECT * FROM prospective ORDER BY id DESC", con)
    st.markdown("前瞻记录：在开奖前生成并记录时间的预测。只有生成时间早于开奖的记录才算有效前瞻。")
    st.write("尚无前瞻结果。" if pro.empty else "")
    if not pro.empty:
        st.dataframe(pro.drop(columns=["probs"]), hide_index=True)

st.divider()
if st.button("生成研究报告"):
    files = write_all(con, settings, None)
    st.success({k: str(v) for k, v in files.items()})
st.download_button("下载研究报告（Markdown）", research_md(con, settings).encode("utf-8"), f"研究报告_{kind}.md")
