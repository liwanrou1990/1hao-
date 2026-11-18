import common  # noqa: F401
import pandas as pd
import streamlit as st

from common import setup
from marksix import db, ingest
from marksix.backtest import final_test_status, get_or_create_seal, load_study
from marksix.report import data_quality_md
from marksix.validate import quality_report
from marksix.zodiac import ZodiacMap

con, settings, kind = setup("数据质量")

with st.expander("导入 CSV", expanded=db.current_draws(con).empty):
    st.markdown("列：`期号, 开奖日期, 正码1..正码6, 特别号码`（也接受 draw_id/draw_date/main_1..6/special，"
                "或“正码”一列以逗号分隔）。可选列：来源、采集时间。**真实数据必须注明来源**，建议为官方结果页。")
    up = st.file_uploader("选择 CSV 文件", type=["csv"])
    src = st.text_input("来源（CSV 无来源列时必填）", value="" if kind == "real" else "SYNTHETIC-UPLOAD")
    fetched = st.text_input("采集时间（可选）")
    reason = st.text_input("更正原因（仅当本文件用于更正已有记录时填写；否则内容不同的记录会被拒绝）")
    if up is not None and st.button("导入"):
        try:
            r = ingest.import_bytes(con, up.getvalue(), source=src or None, filename=up.name,
                                    fetched_at=fetched or None, correction_reason=reason or None)
            st.success(r.summary())
            if r.conflicts:
                st.warning(pd.DataFrame(r.conflicts, columns=["期号", "说明"]))
            if r.invalid:
                st.error(pd.DataFrame(r.invalid, columns=["行号", "说明"]))
        except Exception as e:
            st.error(f"导入失败：{e}")

df_all = db.current_draws(con)
rep = quality_report(con, df_all, settings, ZodiacMap.from_db(con))
st.subheader(f"检查结果：{rep.get('status')}")
if not df_all.empty:
    c = st.columns(4)
    c[0].metric("重复有效版本", len(rep["duplicate_current_versions"]))
    c[1].metric("缺失期号", rep["n_missing_draw_ids"])
    c[2].metric("越界 / 七球重复", f"{len(rep['out_of_range'])} / {len(rep['seven_ball_duplicates'])}")
    c[3].metric("更正记录", rep["n_corrections"])
    c = st.columns(4)
    c[0].metric("时间顺序异常", len(rep["order_violations"]))
    c[1].metric("同日多期", len(rep["duplicate_dates"]))
    c[2].metric("间隔 >14 天", len(rep["long_gaps"]))
    c[3].metric("无生肖映射的期数", len(rep["draws_without_mapping"]))

    st.subheader("49 号码制度边界")
    r = rep["regime"]
    st.write({"采用起点": r.get("start"), "依据": r.get("basis"), "已核验": r.get("verified"),
              "数据中首次出现≥46号": r.get("first_ball_ge46"), "首次出现≥48号": r.get("first_ball_ge48"),
              "起点前排除期数": r.get("n_excluded_before")})
    if not r.get("verified"):
        st.warning("制度起点尚未用官方资料核验。核验后请在 config/settings.json 的 regime_49 中填写 start_date 与 source。")

    if rep["missing_draw_ids"]:
        st.subheader("缺失期号")
        st.dataframe(pd.DataFrame([{"年份": g["year"], "缺失": ", ".join(g["missing"])} for g in rep["missing_draw_ids"]]))
    if rep["long_gaps"]:
        st.subheader("长间隔（可能为休市或数据缺口）")
        st.dataframe(pd.DataFrame(rep["long_gaps"]))
    if rep["corrections"]:
        st.subheader("更正记录（旧版本保留）")
        st.dataframe(pd.DataFrame(rep["corrections"]))

    st.subheader("开奖记录")
    df, _, _ = load_study(con, settings)
    sp = get_or_create_seal(con, df, settings, create=False)
    show = df_all
    if sp is not None and not final_test_status(con)["n_runs"]:
        cutoff = df["date"].iloc[sp.n_dev - 1]
        show = df_all[df_all["date"] <= cutoff]
        st.caption(f"封存测试段（{sp.n_test} 期，自 {sp.first_test_draw}）及之后的开奖号码在封存测试运行前不显示。")
    st.dataframe(show.drop(columns=["date"]).iloc[::-1], hide_index=True)

st.subheader("导入日志")
st.dataframe(pd.DataFrame(rep.get("import_log", [])), hide_index=True)
st.download_button("下载数据质量报告（Markdown）", data_quality_md(con, settings).encode("utf-8"),
                   file_name=f"数据质量报告_{kind}.md")
