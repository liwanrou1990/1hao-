import common  # noqa: F401
import json

import pandas as pd
import streamlit as st

from common import setup
from marksix.analysis import pattern_verdict, patterns_dev
from marksix.backtest import final_test_status
from marksix.patterns import REGISTERED_AT, RULES, registry_hash, rules_table
from marksix.stats import concrete_zodiac_triples, next_number_distribution, next_zodiac_distribution

con, settings, kind = setup("历史形态")
st.markdown(f"**形态规则表**（登记日期 {REGISTERED_AT}，登记哈希 `{registry_hash()}`）：规则在查看结果前登记，"
            "预测对象固定为下一期特别号码或其生肖，观察期限 1 期。")
with st.expander("查看全部 35 条登记规则"):
    st.dataframe(rules_table(), hide_index=True)
    st.download_button("下载规则表 CSV", rules_table().to_csv(index=False).encode("utf-8-sig"), "形态规则表.csv")

st.subheader("开发期检验（只使用开发段，封存测试段不可见）")
n_sims = st.number_input("maxT 蒙特卡洛模拟次数（0 = 只做解析校正）", 0, 5000, 0, step=100)
key = f"pat_{kind}_{n_sims}"
if st.button("运行开发期形态检验") or key in st.session_state:
    if key not in st.session_state:
        bar = st.progress(0.0, "模拟中…")
        try:
            st.session_state[key] = patterns_dev(con, settings, n_sims=int(n_sims),
                                                 progress=lambda f: bar.progress(f, "模拟中…"))
        except Exception as e:
            st.error(str(e))
            st.stop()
        bar.empty()
    res = st.session_state[key]
    tab = res["table"]
    st.info(pattern_verdict(tab))
    st.caption("效应 = 实际率 − 基准率（百分点）；基准率按每次触发时目标期的事件号码数 ÷ 49 计算。"
               "“样本极小/样本不足”的规则不予解读。重叠规则不是相互独立的证据。")
    st.dataframe(tab, hide_index=True)
    st.download_button("下载检验结果 CSV", tab.to_csv(index=False).encode("utf-8-sig"), f"开发期形态检验_{kind}.csv")

    rid = st.selectbox("查看某条规则的下一期分布", tab["规则编号"].tolist(),
                       format_func=lambda r: f"{r} {RULES[r].name}")
    lo, hi = res["range"]
    ctx = res["ctx"]
    rule = RULES[rid]
    st.write(f"条件：{rule.condition}；事件：{rule.event}")
    nd = next_number_distribution(rule, ctx, lo, hi)
    st.markdown("**下一期 49 号分布**（实际次数与随机期望）")
    st.bar_chart(nd.set_index("号码")[["次数", "随机期望"]])
    zd = next_zodiac_distribution(rule, ctx, lo, hi)
    if zd is not None:
        st.markdown("**下一期生肖分布**（随机期望按目标期实际对应号码数量计算）")
        st.dataframe(zd, hide_index=True)
    with st.expander("具体生肖三期序列（描述性，不作为登记检验）"):
        st.dataframe(concrete_zodiac_triples(ctx, lo, hi).head(200), hide_index=True)

st.subheader("封存测试段形态结果")
fts = final_test_status(con)
if not fts["n_runs"]:
    st.write("封存测试尚未运行，测试段形态结果不可查看。")
else:
    first = fts["runs"][0]
    note = json.loads(first["note"])
    st.write(f"首次（独立）封存测试运行于 {first['created_at']}，登记哈希 {note.get('registry_hash')}")
    st.dataframe(pd.DataFrame(note["patterns"]), hide_index=True)
