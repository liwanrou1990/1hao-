import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marksix import config, db, ingest, synthetic, zodiac  # noqa: E402


@pytest.fixture
def settings():
    s = config.load_settings()
    s["simulation"]["n_sims"] = 0
    s["models"]["M3"]["enabled"] = False
    return s


@pytest.fixture
def syn_con(tmp_path):
    con = db.connect("synthetic", tmp_path / "syn" / "s.sqlite")
    zodiac.install_builtin(con)
    yield con
    con.close()


@pytest.fixture
def real_con(tmp_path):
    con = db.connect("real", tmp_path / "real" / "r.sqlite")
    zodiac.install_builtin(con)
    yield con
    con.close()


def load_synthetic(con, n=600, seed=1, **kw):
    df = synthetic.make_draws(n, seed=seed, **kw)
    return ingest.import_bytes(con, synthetic.to_csv_bytes(df), source=None, filename=f"s{seed}.csv"), df
