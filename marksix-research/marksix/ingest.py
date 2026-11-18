"""数据采集落库：CSV 导入、原始快照、哈希、更正版本。

规则：
* 每个导入文件的原始字节存入 raw_snapshots（含 SHA-256），并在 data/<kind>/raw/ 留副本。
* 同一快照重复导入不会产生任何新记录（幂等）。
* 已有期号内容相同 → 视为一致，不写入；内容不同 → 默认作为冲突拒绝，
  只有显式提供更正原因时才追加新版本，旧版本保留（禁止静默覆盖）。
* 真实库拒绝来源标记为 SYNTHETIC 的数据；合成库只接受 SYNTHETIC 来源。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config, db

HEADER_ALIASES = {
    "draw_id": ["draw_id", "期号", "期數", "期数", "draw_no", "drawno"],
    "draw_date": ["draw_date", "开奖日期", "開獎日期", "开奖时间", "開獎時間", "date"],
    "special": ["special", "特别号码", "特別號碼", "特码", "特別號", "extra"],
    "source": ["source", "来源", "來源"],
    "fetched_at": ["fetched_at", "采集时间", "採集時間"],
    "main_numbers": ["main_numbers", "正码", "正碼", "winning_numbers"],
}
for i in range(1, 7):
    HEADER_ALIASES[f"main_{i}"] = [f"main_{i}", f"n{i}", f"正码{i}", f"正碼{i}", f"no{i}", f"ball{i}"]

MAIN = [f"main_{i}" for i in range(1, 7)]


@dataclass
class ImportResult:
    snapshot_id: int | None
    duplicate_snapshot: bool = False
    n_rows: int = 0
    inserted: list = field(default_factory=list)
    identical: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)   # [(draw_id, 说明)]
    corrected: list = field(default_factory=list)
    invalid: list = field(default_factory=list)     # [(行号, 说明)]

    def summary(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id, "重复快照": self.duplicate_snapshot, "行数": self.n_rows,
            "新增": len(self.inserted), "一致未变": len(self.identical), "冲突未应用": len(self.conflicts),
            "已更正": len(self.corrected), "无效行": len(self.invalid),
        }


def normalize_draw_id(raw) -> str | None:
    s = str(raw).strip()
    m = re.fullmatch(r"(\d{2}|\d{4})\s*[/\-]\s*(\d{1,3})", s)
    if not m:
        m = re.fullmatch(r"(\d{2}|\d{4})(\d{3})", s)
    if not m:
        return None
    y, n = m.group(1), int(m.group(2))
    if len(y) == 2:
        yy = int(y)
        y = str(2000 + yy if yy < 70 else 1900 + yy)
    if n < 1:
        return None
    return f"{y}/{n:03d}"


def _rename_headers(df: pd.DataFrame) -> pd.DataFrame:
    lower = {c: str(c).strip().lower() for c in df.columns}
    mapping = {}
    for canon, aliases in HEADER_ALIASES.items():
        al = [a.lower() for a in aliases]
        for c, lc in lower.items():
            if lc in al and c not in mapping:
                mapping[c] = canon
                break
    return df.rename(columns=mapping)


def parse_csv_bytes(content: bytes, default_source: str | None) -> tuple[pd.DataFrame, list]:
    """解析 CSV，返回 (规范化数据, 无效行列表)。不做任何补数。"""
    text = content.decode("utf-8-sig")
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df = _rename_headers(raw)
    if "main_numbers" in df.columns and not all(c in df.columns for c in MAIN):
        parts = df["main_numbers"].str.split(r"[,\s;+]+", regex=True)
        for i, c in enumerate(MAIN):
            df[c] = parts.map(lambda p, i=i: p[i] if isinstance(p, list) and len(p) > i else "")
    missing = [c for c in ["draw_id", "draw_date", *MAIN, "special"] if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少必需列: {missing}；可用列: {list(raw.columns)}")
    if "source" not in df.columns:
        if not default_source:
            raise ValueError("CSV 无来源列，必须通过 --source 指明数据来源")
        df["source"] = default_source
    df.loc[df["source"].str.strip() == "", "source"] = default_source or ""
    if "fetched_at" not in df.columns:
        df["fetched_at"] = ""

    rows, invalid = [], []
    for idx, r in df.iterrows():
        line = idx + 2
        did = normalize_draw_id(r["draw_id"])
        if did is None:
            invalid.append((line, f"期号格式无效: {r['draw_id']!r}"))
            continue
        try:
            d = pd.to_datetime(str(r["draw_date"]).strip())
        except Exception:
            invalid.append((line, f"开奖日期无效: {r['draw_date']!r}"))
            continue
        date_s = d.strftime("%Y-%m-%d %H:%M") if (d.hour or d.minute) else d.strftime("%Y-%m-%d")
        try:
            nums = [int(str(r[c]).strip()) for c in MAIN] + [int(str(r["special"]).strip())]
        except ValueError:
            invalid.append((line, "号码不是整数"))
            continue
        bad = [x for x in nums if not 1 <= x <= 49]
        if bad:
            invalid.append((line, f"号码超出 1-49: {bad}"))
            continue
        if len(set(nums)) != 7:
            invalid.append((line, f"七个球存在重复: {nums}"))
            continue
        if int(did[:4]) != d.year:
            invalid.append((line, f"期号年份 {did[:4]} 与开奖日期 {date_s} 不一致"))
            continue
        src = str(r["source"]).strip()
        if not src:
            invalid.append((line, "来源为空"))
            continue
        rows.append({"draw_id": did, "draw_date": date_s, **{c: nums[i] for i, c in enumerate(MAIN)},
                     "special": nums[6], "source": src, "fetched_at": str(r["fetched_at"]).strip() or None,
                     "_line": line})
    return pd.DataFrame(rows), invalid


def _same(a: dict, b: dict) -> bool:
    return (a["draw_date"][:10] == b["draw_date"][:10] and a["special"] == b["special"]
            and sorted(a[c] for c in MAIN) == sorted(b[c] for c in MAIN))


def _check_kind_source(kind: str, source: str):
    is_syn = source.upper().startswith("SYNTHETIC")
    if kind == "real" and is_syn:
        raise ValueError("真实数据库拒绝合成来源（SYNTHETIC）的数据")
    if kind == "synthetic" and not is_syn:
        raise ValueError("合成数据库只接受来源以 SYNTHETIC 开头的数据")


def import_bytes(con: sqlite3.Connection, content: bytes, *, source: str | None, filename: str = "",
                 fetched_at: str | None = None, correction_reason: str | None = None,
                 note: str | None = None) -> ImportResult:
    kind = db.dataset_kind(con)
    sha = hashlib.sha256(content).hexdigest()
    row = con.execute("SELECT snapshot_id FROM raw_snapshots WHERE sha256=?", (sha,)).fetchone()
    if row is not None:
        res = ImportResult(snapshot_id=row[0], duplicate_snapshot=True)
        _log(con, res, source)
        return res

    df, invalid = parse_csv_bytes(content, source)
    for s in (df["source"].unique() if not df.empty else []):
        _check_kind_source(kind, s)

    cur = con.execute(
        "INSERT INTO raw_snapshots(sha256, source, filename, fetched_at, imported_at, n_bytes, content, note)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (sha, source or (df["source"].iloc[0] if not df.empty else "?"), filename, fetched_at,
         db.now_iso(), len(content), content, note),
    )
    snap_id = cur.lastrowid
    dbfile = con.execute("PRAGMA database_list").fetchone()[2]
    rd = Path(dbfile).parent / "raw" if dbfile else config.raw_dir(kind)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"{sha[:16]}_{Path(filename).name or 'snapshot.csv'}").write_bytes(content)

    res = ImportResult(snapshot_id=snap_id, n_rows=len(df) + len(invalid), invalid=invalid)
    if df.empty:
        con.commit()
        _log(con, res, source)
        return res

    # 文件内部重复期号
    groups = {}
    for rec in df.to_dict("records"):
        groups.setdefault(rec["draw_id"], []).append(rec)
    now = db.now_iso()
    for did, recs in groups.items():
        if len(recs) > 1 and not all(_same(recs[0], x) for x in recs[1:]):
            res.conflicts.append((did, f"同一文件内期号重复且内容不同（行 {[x['_line'] for x in recs]}）"))
            continue
        rec = recs[0]
        old = con.execute(
            f"SELECT version, draw_date, {', '.join(MAIN)}, special FROM draws WHERE draw_id=? AND is_current=1",
            (did,),
        ).fetchone()
        if old is None:
            _insert(con, rec, 1, snap_id, now)
            res.inserted.append(did)
            continue
        old_d = {"draw_date": old[1], **{c: old[2 + i] for i, c in enumerate(MAIN)}, "special": old[8]}
        if _same(old_d, rec):
            res.identical.append(did)
            continue
        diff = {k: [old_d[k], rec[k]] for k in ["draw_date", *MAIN, "special"] if old_d[k] != rec[k]}
        if not correction_reason:
            res.conflicts.append((did, f"与库内版本 {old[0]} 不一致，未应用（需提供更正原因）: {diff}"))
            continue
        new_v = old[0] + 1
        con.execute("UPDATE draws SET is_current=0 WHERE draw_id=? AND version=?", (did, old[0]))
        _insert(con, rec, new_v, snap_id, now)
        con.execute(
            "INSERT INTO corrections(draw_id, old_version, new_version, reason, snapshot_id, diff, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (did, old[0], new_v, correction_reason, snap_id, json.dumps(diff, ensure_ascii=False), now),
        )
        res.corrected.append(did)
    con.commit()
    _log(con, res, source)
    return res


def _insert(con, rec, version, snap_id, now):
    con.execute(
        f"INSERT INTO draws(draw_id, version, draw_date, {', '.join(MAIN)}, special, source, fetched_at,"
        " snapshot_id, is_current, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
        (rec["draw_id"], version, rec["draw_date"], *[int(rec[c]) for c in MAIN], int(rec["special"]),
         rec["source"], rec["fetched_at"], snap_id, now),
    )


def _log(con, res: ImportResult, source):
    con.execute(
        "INSERT INTO import_log(snapshot_id, created_at, source, n_rows, n_inserted, n_identical, n_conflicts,"
        " n_corrected, n_invalid, details) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (res.snapshot_id, db.now_iso(), source, res.n_rows, len(res.inserted), len(res.identical),
         len(res.conflicts), len(res.corrected), len(res.invalid),
         json.dumps({"重复快照": res.duplicate_snapshot, "冲突": res.conflicts[:200], "无效": res.invalid[:200],
                     "更正": res.corrected[:200]}, ensure_ascii=False)),
    )
    con.commit()


def import_csv(con, path: str | Path, *, source: str | None = None, correction_reason: str | None = None,
               fetched_at: str | None = None) -> ImportResult:
    p = Path(path)
    return import_bytes(con, p.read_bytes(), source=source, filename=p.name, fetched_at=fetched_at,
                        correction_reason=correction_reason)
