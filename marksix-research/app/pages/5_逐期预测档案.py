import common  # noqa: F401
import json

import numpy as np
import pandas as pd
import streamlit as st

from common import setup
from marksix.backtest import latest_frozen, predict_next, score_prospective

con, settings, kind = setup("逐期预测档案")
st.info("每个概率旁均显示随机基准 1/49 ≈ 0.0204。没有经过封存测试确认的优势时，概率较高的号码**不是推荐号码**。")

exps = pd.read_sql_query(
    "SELECT DISTINCT e.exp_id, e.phase, e.model, e.params FROM experiments e JOIN predictions p ON e.exp_id=p.exp_id"
    " ORDER BY e.exp_id", con)
if exps.empty:
    st.write("尚无逐期预测档案（冻结候选或运行封存测试后生成）。")
else:
    label = {"dev": "开发期", "final_test": "封存测试"}
    eid = st.selectbox("选择实验", exps["exp_id"].tolist(),
                       format_func=lambda i: (lambda r: f"#{i} {label.get(r.phase, r.phase)} {r.model} {r.params}")(
                           exps.set_index("exp_id").loc[i]))
    pr = pd.read_sql_query("SELECT draw_id, draw_date, probs, actual, logloss FROM predictions WHERE exp_id=? ORDER BY draw_date",
                           con, params=(eid,))
    P = np.array([json.loads(p) for p in pr["probs"]])
    pa = P[np.arange(len(P)), pr["actual"].to_numpy() - 1]
    g = (P > pa[:, None] + 1e-12).sum(1)
    m = (abs(P - pa[:, None]) <= 1e-12).sum(1)
    rank = [str(a + 1) if b == 1 else f"{a + 1}–{a + b}（并列）" for a, b in zip(g, m)]
    view = pd.DataFrame({"期号": pr["draw_id"], "日期": pr["draw_date"], "实际特别号码": pr["actual"],
                         "实际号码的预测概率": pa.round(5), "随机基准": round(1 / 49, 5), "概率排名(1=最高)": rank,
                         "logloss": pr["logloss"].round(4), "均匀logloss": round(np.log(49), 4)})
    st.dataframe(view.iloc[::-1], hide_index=True)
    st.download_button("下载 CSV", view.to_csv(index=False).encode("utf-8-sig"), f"逐期预测_{eid}.csv")
    i = st.selectbox("查看某期 49 号概率", range(len(view)), format_func=lambda k: view["期号"].iloc[k],
                     index=len(view) - 1)
    st.bar_chart(pd.DataFrame({"预测概率": P[i], "随机基准": 1 / 49}, index=range(1, 50)))

st.subheader("前瞻预测")
fz = latest_frozen(con)
if fz is None:
    st.write("冻结候选后才能生成前瞻预测。")
else:
    if st.button("为下一期生成前瞻预测（记录生成时间）"):
        for r in predict_next(con, settings):
            st.write(f"{r['model']}：最高概率 {r['probs'].max():.5f}，随机基准 {1/49:.5f}")
    if st.button("为已开奖的前瞻预测评分"):
        st.write(f"已评分 {score_prospective(con, settings)} 条")
    pro = pd.read_sql_query("SELECT id, created_at, after_draw_id, model, params, target_draw_id, actual, logloss"
                            " FROM prospective ORDER BY id DESC", con)
    st.dataframe(pro, hide_index=True)
