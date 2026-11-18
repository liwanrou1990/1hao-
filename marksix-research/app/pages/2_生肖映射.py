import common  # noqa: F401
import pandas as pd
import streamlit as st

from common import setup
from marksix import zodiac

con, settings, kind = setup("生肖映射")
zm = zodiac.ZodiacMap.from_db(con)
st.warning("生肖只是号码分类方式，本系统不假设它是官方独立投注项目。内置映射为**民间通行口径**"
           "（号码 1 = 当年生肖，按 鼠牛虎兔龙蛇马羊猴鸡狗猪 逆序循环），**非官方，待与可信来源核对**。")
if zm is None:
    st.error("映射缺失：生肖分析已停止，号码分析仍可运行。")
    st.stop()
lo, hi = zm.coverage
st.write({"版本": zm.version, "来源": zm.source, "有效范围": f"{lo.date()} ～ {hi.date()}",
          "跨年切换口径": "农历正月初一（含当日）"})
st.caption("超出有效范围的开奖日期没有映射，这些期不参与生肖分析。历史每期使用当期所在农历年的映射，不套用今年生肖表。")

st.subheader("春节日期（切换点）")
st.dataframe(pd.DataFrame([{"农历年": y, "生肖": zodiac.ZODIACS[zodiac.year_zodiac_index(y)], "正月初一": d.isoformat()}
                           for y, d in zodiac.CHINESE_NEW_YEAR.items()]), hide_index=True)

st.subheader("查询某日映射")
d = st.date_input("开奖日期", value=pd.Timestamp("2024-02-10"))
arr = zm.lookup(d)
if arr is None:
    st.error("该日期无映射")
else:
    cnt = zodiac.zodiac_counts(arr)
    rows = [{"生肖": z, "号码": " ".join(str(n) for n in range(1, 50) if arr[n - 1] == i), "号码数量": int(cnt[i]),
             "随机基准概率": f"{cnt[i]}/49 = {cnt[i] / 49:.4f}"} for i, z in enumerate(zodiac.ZODIACS)]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("基准概率按对应号码数量 ÷ 49 计算，本命生肖 5/49，其余 4/49，不是统一的 1/12。")

st.subheader("导入已核验的映射（可选）")
st.markdown("CSV 列：`number, zodiac, valid_from, valid_to`；每个有效期必须完整覆盖 1–49 且互不重叠。")
up = st.file_uploader("映射 CSV", type=["csv"])
ver = st.text_input("映射版本名")
src = st.text_input("映射来源（必填）")
if up is not None and ver and src and st.button("导入映射"):
    try:
        st.success(f"已导入 {zodiac.import_mapping_csv(con, up, ver, src)} 行。注意：分析默认使用按名称排序的第一个版本。")
    except Exception as e:
        st.error(str(e))
