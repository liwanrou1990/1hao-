import common  # noqa: F401
import streamlit as st

from common import setup
from marksix.analysis import experiments_frame

con, settings, kind = setup("实验记录")
st.markdown("全部实验均被保存，**包括失败与不理想的结果**。每条记录含参数、代码版本、配置哈希与数据哈希。")
df = experiments_frame(con)
phase = st.multiselect("阶段", sorted(df["phase"].unique()) if not df.empty else [],
                       default=sorted(df["phase"].unique()) if not df.empty else [])
st.dataframe(df[df["phase"].isin(phase)] if phase else df, hide_index=True)
st.download_button("导出实验记录 CSV", df.to_csv(index=False).encode("utf-8-sig"), f"实验记录_{kind}.csv")
if not df.empty:
    eid = st.number_input("查看实验详情（实验编号）", int(df["exp_id"].min()), int(df["exp_id"].max()), int(df["exp_id"].max()))
    row = con.execute("SELECT metrics, notes FROM experiments WHERE exp_id=?", (int(eid),)).fetchone()
    if row:
        st.code(row[0] or "", language="json")
        if row[1]:
            st.text(row[1])
