"""界面入口：streamlit run app/main.py（入口用英文文件名，兼容 Windows 命令行编码）。"""
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
pages = [st.Page(str(HERE / "首页.py"), title="首页", default=True)] + [
    st.Page(str(p), title=p.stem.split("_", 1)[1]) for p in sorted((HERE / "pages").glob("*.py"))]
st.navigation(pages).run()
