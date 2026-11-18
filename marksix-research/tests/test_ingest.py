"""数据层：重复导入、更正版本、无效行、真实/合成隔离。"""
import pytest

from marksix import db, ingest
from tests.conftest import load_synthetic

HEAD = "期号,开奖日期,正码1,正码2,正码3,正码4,正码5,正码6,特别号码\n"


def _csv(rows):
    return (HEAD + "\n".join(rows) + "\n").encode("utf-8")


def test_reimport_same_snapshot_is_noop(syn_con):
    r1, df = load_synthetic(syn_con, n=100)
    assert len(r1.inserted) == 100
    r2, _ = load_synthetic(syn_con, n=100)
    assert r2.duplicate_snapshot and not r2.inserted
    assert syn_con.execute("SELECT COUNT(*) FROM draws").fetchone()[0] == 100
    assert syn_con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0] == 1


def test_same_rows_different_file_identical(real_con):
    a = _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"])
    b = _csv(["2024/001,2024-01-02,6,5,4,3,2,1,7"])  # 正码顺序不同，内容一致
    ingest.import_bytes(real_con, a, source="测试来源")
    r = ingest.import_bytes(real_con, b, source="测试来源")
    assert r.identical == ["2024/001"] and not r.inserted and not r.conflicts


def test_conflict_rejected_without_reason_and_versioned_with_reason(real_con):
    ingest.import_bytes(real_con, _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"]), source="源A")
    r = ingest.import_bytes(real_con, _csv(["24/001,2024-01-02,1,2,3,4,5,6,8"]), source="源A")
    assert r.conflicts and not r.corrected
    cur = db.current_draws(real_con)
    assert cur["special"].tolist() == [7]
    r = ingest.import_bytes(real_con, _csv(["24/001,2024-01-02,1,2,3,4,5,6,9"]), source="源A",
                            correction_reason="官方更正公告")
    assert r.corrected == ["2024/001"]
    allv = real_con.execute("SELECT version, special, is_current FROM draws ORDER BY version").fetchall()
    assert allv == [(1, 7, 0), (2, 9, 1)]          # 旧版本保留，未被覆盖
    assert db.current_draws(real_con)["special"].tolist() == [9]
    assert real_con.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1


def test_invalid_rows(real_con):
    rows = ["24/001,2024-01-02,1,2,3,4,5,6,50",      # 越界
            "24/002,2024-01-04,1,2,3,4,5,6,6",       # 七球重复
            "23/003,2024-01-06,1,2,3,4,5,6,7",       # 年份不符
            "xx,2024-01-06,1,2,3,4,5,6,7",           # 期号无效
            "24/005,2024-01-09,1,2,3,4,5,6,7"]
    r = ingest.import_bytes(real_con, _csv(rows), source="源")
    assert len(r.invalid) == 4 and r.inserted == ["2024/005"]


def test_within_file_conflict(real_con):
    r = ingest.import_bytes(real_con, _csv(["24/001,2024-01-02,1,2,3,4,5,6,7", "24/001,2024-01-02,1,2,3,4,5,6,8"]),
                            source="源")
    assert r.conflicts and not r.inserted


def test_source_required(real_con):
    with pytest.raises(ValueError):
        ingest.import_bytes(real_con, _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"]), source=None)


def test_real_synthetic_isolation(tmp_path):
    con = db.connect("synthetic", tmp_path / "x.sqlite")
    con.close()
    with pytest.raises(db.KindMismatch):
        db.connect("real", tmp_path / "x.sqlite")
    real = db.connect("real", tmp_path / "r.sqlite")
    with pytest.raises(ValueError):
        ingest.import_bytes(real, _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"]), source="SYNTHETIC-IID")
    syn = db.connect("synthetic", tmp_path / "s.sqlite")
    with pytest.raises(ValueError):
        ingest.import_bytes(syn, _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"]), source="HKJC")


def test_snapshot_hash_and_raw_copy(real_con, tmp_path):
    content = _csv(["24/001,2024-01-02,1,2,3,4,5,6,7"])
    import hashlib

    ingest.import_bytes(real_con, content, source="源", filename="a.csv")
    sha, blob = real_con.execute("SELECT sha256, content FROM raw_snapshots").fetchone()
    assert sha == hashlib.sha256(content).hexdigest() and blob == content
    assert list((tmp_path / "real" / "raw").glob("*a.csv"))
