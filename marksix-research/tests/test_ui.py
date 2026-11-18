"""界面冒烟测试：每个页面在合成数据与空真实库上都能渲染且无异常。"""
import importlib
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
import sys  # noqa: E402

sys.path.insert(0, str(APP))  # streamlit run 会自动加入主脚本目录；AppTest 不会
PAGES = ["首页.py"] + sorted(p.name for p in (APP / "pages").glob("*.py"))


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    import os

    d = tmp_path_factory.mktemp("uidata")
    os.environ["MARKSIX_DATA_DIR"] = str(d)
    from marksix import config

    importlib.reload(config)
    config.DATA_DIR = d
    from marksix import db, ingest, synthetic, zodiac
    from marksix.backtest import freeze, run_dev, run_final_test

    con = db.connect("synthetic")
    zodiac.install_builtin(con)
    ingest.import_bytes(con, synthetic.to_csv_bytes(synthetic.make_draws(400, seed=2)), source=None)
    s = config.load_settings()
    s["models"]["M3"]["enabled"] = False
    run_dev(con, s, grid=[("M0", {}), ("M1", {"window": 50, "alpha": 5.0}), ("M2", {"rule": "N01", "kappa": 20.0})])
    freeze(con, s)
    run_final_test(con, s, confirm=True)
    yield d
    os.environ.pop("MARKSIX_DATA_DIR", None)


@pytest.mark.parametrize("kind", ["synthetic", "real"])
@pytest.mark.parametrize("page", PAGES)
def test_page_renders(data_dir, page, kind):
    from streamlit.testing.v1 import AppTest

    path = APP / page if page == "首页.py" else APP / "pages" / page
    at = AppTest.from_file(str(path), default_timeout=60)
    at.session_state["kind"] = kind
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert at.title[0].value


def test_pattern_page_button(data_dir):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP / "pages" / "3_历史形态.py"), default_timeout=120)
    at.session_state["kind"] = "synthetic"
    at.run()
    at.number_input[0].set_value(50)
    at.button[0].click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("maxT" in str(c) or "形态" in str(c.value) for c in at.info)
    assert len(at.dataframe) >= 2
