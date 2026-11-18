"""界面共用：数据集选择、页眉状态栏。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from marksix import config, db, zodiac  # noqa: E402
from marksix.backtest import final_test_status, get_or_create_seal, load_study  # noqa: E402

DISCLAIMER = "本系统只做统计研究，不提供投注建议，不接入任何投注或资金操作。"


def setup(title: str):
    st.set_page_config(page_title=f"{title}｜六合彩形态研究", layout="wide")
    kinds = list(config.DATASET_KINDS)
    if "kind" not in st.session_state:
        q = st.query_params.get("kind")
        st.session_state["kind"] = q if q in kinds else "real"
    st.sidebar.radio("数据集", kinds, key="kind", format_func=lambda k: config.KIND_LABEL[k],
                     help="真实数据与合成演示数据存放在不同的数据库文件中，互不混用。")
    st.sidebar.caption(DISCLAIMER)
    kind = st.session_state["kind"]
    con = db.connect(kind)
    zodiac.install_builtin(con)
    settings = config.load_settings()
    st.title(title)
    header(con, settings, kind)
    return con, settings, kind


def validation_state(con) -> str:
    fts = final_test_status(con)
    if fts["n_runs"] == 0:
        return "开发期（封存测试集未查看）"
    if fts["n_runs"] == 1:
        return "封存测试已运行 1 次（独立）"
    return f"封存测试已运行 {fts['n_runs']} 次（仅首次独立，之后均非独立）"


def header(con, settings, kind):
    df_all = db.current_draws(con)
    df, regime, zmap = load_study(con, settings)
    sp = get_or_create_seal(con, df, settings, create=False) if not df.empty else None
    flag = "🟥 合成演示数据（结果与真实六合彩无关）" if kind == "synthetic" else "🟩 真实数据"
    period = f"{df['date'].min().date()} ～ {df['date'].max().date()}" if len(df) else "无"
    split_s = f"开发 {sp.n_dev} 期 / 封存测试 {sp.n_test} 期 / 封存后 {sp.n_post} 期" if sp else "未封存"
    st.markdown(
        f"| 数据类型 | 研究样本 | 数据时间 | 封存划分 | 验证状态 |\n|---|---|---|---|---|\n"
        f"| {'**合成**' if kind == 'synthetic' else '**真实**'} | {len(df)} 期（全部有效记录 {len(df_all)} 期） | {period} "
        f"| {split_s} | {validation_state(con)} |")
    src = ", ".join(df_all["source"].unique()[:3]) if len(df_all) else "无"
    st.caption(f"{flag}｜来源：{src}｜数据哈希：{db.data_hash(df_all)}｜49 号码制度起点：{regime.get('start') or '未确定'}"
               f"（{regime.get('basis') or '无数据'}）｜生肖映射：{zmap.version if zmap else '缺失'}")
    if kind == "real" and df_all.empty:
        st.warning("真实数据库尚无数据。请在“数据质量”页导入带来源的官方开奖 CSV；系统不会编造任何数据。")
    return df, regime, zmap, sp
