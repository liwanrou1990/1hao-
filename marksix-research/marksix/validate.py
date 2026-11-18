"""数据校验与 49 号码制度边界。"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from .ingest import MAIN

BALLS = MAIN + ["special"]


def regime_info(df: pd.DataFrame, settings: dict) -> dict:
    """确定 49 号码制度起点。

    已配置并写明来源 → 使用配置（标注“已配置”，是否核验以 source 是否填写为准）；
    未配置 → 以数据中首次出现 48 或 49 号（任意球）的开奖日为保守推断起点。
    由于制度首期未必出现 48/49 号，推断起点可能晚于真实起点数期（只会少用数据，不会混入旧制度数据）。
    """
    cfg = settings.get("regime_49", {})
    info = {"configured_start": cfg.get("start_date"), "configured_source": cfg.get("source")}
    if df.empty:
        return {**info, "start": None, "basis": "无数据", "verified": False}
    hi = df[BALLS].max(axis=1)
    first46 = df.loc[hi >= 46, "date"].min() if (hi >= 46).any() else None
    first48 = df.loc[hi >= 48, "date"].min() if (hi >= 48).any() else None
    info.update({"first_ball_ge46": None if first46 is None else first46.date().isoformat(),
                 "first_ball_ge48": None if first48 is None else first48.date().isoformat()})
    if cfg.get("start_date"):
        start = pd.Timestamp(cfg["start_date"])
        info.update(start=start, basis="已配置", verified=bool(cfg.get("source")))
        before = df[df["date"] < start]
        info["n_excluded_before"] = int(len(before))
        info["conflict_ball_ge48_before_start"] = int((before[BALLS].max(axis=1) >= 48).sum())
    else:
        info.update(start=first48, basis="数据推断（首次出现48/49号），未经官方核验", verified=False)
        info["n_excluded_before"] = int((df["date"] < first48).sum()) if first48 is not None else len(df)
        info["conflict_ball_ge48_before_start"] = 0
    return info


def study_frame(df: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict]:
    info = regime_info(df, settings)
    if info["start"] is None:
        return df.iloc[0:0].copy(), info
    out = df[df["date"] >= info["start"]].reset_index(drop=True)
    return out, info


def quality_report(con: sqlite3.Connection, df: pd.DataFrame, settings: dict, zmap=None) -> dict:
    rep: dict = {"n_current": int(len(df))}
    if df.empty:
        rep["status"] = "无数据"
        return rep
    rep["date_range"] = (df["date"].min().date().isoformat(), df["date"].max().date().isoformat())
    rep["sources"] = df["source"].value_counts().to_dict()

    multi = pd.read_sql_query(
        "SELECT draw_id, COUNT(*) c FROM draws WHERE is_current=1 GROUP BY draw_id HAVING c>1", con)
    rep["duplicate_current_versions"] = multi["draw_id"].tolist()
    rep["duplicate_dates"] = df.loc[df["date"].duplicated(keep=False), "draw_id"].tolist()

    balls = df[BALLS].to_numpy()
    rep["out_of_range"] = df.loc[((balls < 1) | (balls > 49)).any(axis=1), "draw_id"].tolist()
    rep["seven_ball_duplicates"] = df.loc[[len(set(r)) != 7 for r in balls], "draw_id"].tolist()

    # 期号连续性：同一年内序号应从 001 起连续
    yr = df["draw_id"].str.slice(0, 4).astype(int)
    seq = df["draw_id"].str.slice(5).astype(int)
    gaps = []
    for y in sorted(yr.unique()):
        s = sorted(seq[yr == y].tolist())
        full = set(range(1, max(s) + 1))
        miss = sorted(full - set(s))
        if miss:
            gaps.append({"year": int(y), "missing": [f"{y}/{m:03d}" for m in miss]})
    rep["missing_draw_ids"] = gaps
    rep["n_missing_draw_ids"] = int(sum(len(g["missing"]) for g in gaps))

    # 时间顺序：按日期排序后期号应单调递增
    key = yr * 1000 + seq
    order_bad = df.loc[key.diff() <= 0, "draw_id"].tolist()
    rep["order_violations"] = order_bad
    gap_days = df["date"].diff().dt.days
    rep["long_gaps"] = [
        {"after": df.loc[i - 1, "draw_id"], "before": df.loc[i, "draw_id"], "days": int(gap_days[i])}
        for i in df.index[gap_days > 14]
    ]
    rep["n_corrections"] = con.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    rep["corrections"] = pd.read_sql_query(
        "SELECT draw_id, old_version, new_version, reason, diff, created_at FROM corrections", con
    ).to_dict("records")
    rep["n_snapshots"] = con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]
    rep["import_log"] = pd.read_sql_query(
        "SELECT created_at, source, n_rows, n_inserted, n_identical, n_conflicts, n_corrected, n_invalid"
        " FROM import_log ORDER BY id DESC LIMIT 50", con).to_dict("records")
    rep["regime"] = {k: (v.date().isoformat() if isinstance(v, pd.Timestamp) else v)
                     for k, v in regime_info(df, settings).items()}
    if zmap is not None:
        _, ok = zmap.matrix(df["date"])
        rep["zodiac_mapping_version"] = zmap.version
        rep["draws_without_mapping"] = df.loc[~ok, "draw_id"].tolist()
    else:
        rep["zodiac_mapping_version"] = None
        rep["draws_without_mapping"] = df["draw_id"].tolist()
    hard = rep["duplicate_current_versions"] or rep["out_of_range"] or rep["seven_ball_duplicates"] or rep["order_violations"]
    rep["status"] = "存在严重问题" if hard else ("有警告" if (rep["n_missing_draw_ids"] or rep["duplicate_dates"]) else "通过")
    return rep
