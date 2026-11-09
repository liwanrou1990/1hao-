"""72 小时真实时间可用性验收：按运行主机/时段分段统计（云容器历史段 vs Mac 本地段 vs 全部），每段独立计算实际运行次数、预期次数、覆盖率、
最长中断、行情质量、异常次数、错过信号、过期订单、服务启动次数。历史缺口原样保留；不伪造周期、不改时间戳；本地段起点 = Mac 实际首次运行。
只统计真实时间推进中的记录，不做模拟；本地段不足 72 小时固定为“进行中”。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text


def _jsonl(p: Path) -> pd.DataFrame:
    return pd.read_json(p, lines=True) if p.exists() and p.stat().st_size else pd.DataFrame()


def _segment_stats(win: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp, interval_s: int, alerts: pd.DataFrame, accounts_events: dict) -> dict:
    elapsed_h = max((t1 - t0).total_seconds() / 3600, 0.0); expected = max(int(elapsed_h * 3600 / interval_s), 1)
    gaps = win["t"].diff().dt.total_seconds().dropna() if len(win) else pd.Series(dtype=float)
    tail_gap = (t1 - win["t"].max()).total_seconds() if len(win) else elapsed_h * 3600       # 最后一轮到段末的空档也算中断
    longest = float(max([gaps.max() if len(gaps) else 0.0, tail_gap]) / 3600)
    per_day = {}
    for d, g in win.groupby(win["t"].dt.date):
        per_day[str(d)] = {"cycles": int(len(g)), "data_ok_share": float(g["ok"].astype(bool).mean()), "issues": sorted({x for xs in g["issues"] for x in (xs or [])})[:5], "anomalies": sorted({x for xs in g.get("anomalies", pd.Series([[]] * len(g))) for x in (xs or [])})[:5]}
    al = alerts[(pd.to_datetime(alerts["recorded_at"], utc=True) >= t0) & (pd.to_datetime(alerts["recorded_at"], utc=True) < t1)] if len(alerts) else alerts
    missed = {}; expired = {}; fills = {}; paused = {}
    for name, ev in accounts_events.items():
        if ev.empty or "kind" not in ev:
            continue
        rt = pd.to_datetime(ev["recorded_at"], utc=True); e = ev[(rt >= t0) & (rt < t1)]
        missed[name] = int((e["kind"] == "missed_signal").sum()); expired[name] = int((e["kind"] == "order_expired").sum()); fills[name] = int((e["kind"] == "fill").sum()); paused[name] = int((e["kind"] == "fill_paused").sum())
    return {"start_utc": str(t0), "end_utc": str(t1), "elapsed_hours": round(elapsed_h, 2), "cycles_observed": int(len(win)), "cycles_expected": expected, "coverage": round(min(len(win) / expected, 1.0), 3) if expected else None,
            "cycle_gaps_gt_3x": int((gaps > interval_s * 3).sum()), "longest_gap_hours": round(longest, 2), "data_ok_share": float(win["ok"].astype(bool).mean()) if len(win) else None,
            "n_data_issues": int(sum(len(x or []) for x in win["issues"])) if len(win) else 0, "n_anomalies": int(sum(len(x or []) for x in win.get("anomalies", pd.Series([[]] * len(win))))) if len(win) else 0,
            "alerts_by_kind": al["kind"].value_counts().to_dict() if len(al) else {}, "missed_signals": missed, "expired_orders": expired, "fills": fills, "fill_paused": paused, "per_day": per_day, "hosts": sorted(win["host"].dropna().unique().tolist()) if "host" in win else []}


def acceptance_status(log_dir: Path, interval_s: int = 600, required_hours: float = 72.0, now: pd.Timestamp | None = None, local_start: str | None = None, cloud_hosts: tuple = ("vm",), cloud_segment_end: str | None = "2026-11-08 06:03:00", write: bool = True) -> dict:
    """分段：cloud_legacy = 云容器历史记录（host 在 cloud_hosts 内，或无 host 字段且时间 ≤ cloud_segment_end）；local = 其余（Mac 本地），
    起点 = local_start（显式给定）或本地段第一条记录的时间；all = 全部。判定只看 local 段。"""
    log_dir = Path(log_dir); now = now or pd.Timestamp.now(tz="UTC")
    re_ = _jsonl(log_dir / "runner_events.jsonl"); al = _jsonl(log_dir / "alerts.jsonl")
    out = {"checked_at_utc": str(now), "required_hours": required_hours, "interval_s": interval_s, "category": "真实时间前瞻运行（非模拟）", "segments": {}, "status": "未开始", "verdict_segment": "local", "local_start": local_start}
    checks = re_[(re_["kind"] == "data_check") & (re_.get("mode", pd.Series(["forward"] * len(re_))) != "replay")].copy() if len(re_) else pd.DataFrame()
    if checks.empty:
        if write:
            atomic_write_json(log_dir / "acceptance_status.json", out, indent=1, default=str)
        return out
    checks["t"] = pd.to_datetime(checks["now"], utc=True)
    if "host" not in checks:
        checks["host"] = None
    cse = pd.Timestamp(cloud_segment_end, tz="UTC") if cloud_segment_end else None
    is_cloud = checks["host"].isin(cloud_hosts) | (checks["host"].isna() & (checks["t"] <= cse if cse is not None else False))
    if local_start:
        ls = pd.Timestamp(local_start, tz="UTC"); is_cloud = is_cloud & (checks["t"] < ls)
    cloud = checks[is_cloud]; local = checks[~is_cloud]
    acc_ev = {d.name: _jsonl(d / "events.jsonl") for d in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists())}
    if len(cloud):
        out["segments"]["cloud_legacy"] = _segment_stats(cloud, cloud["t"].min(), cloud["t"].max(), interval_s, al, acc_ev) | {"note": "云容器内的单轮/手动运行，不构成连续运行；原样保留"}
    t0_local = pd.Timestamp(local_start, tz="UTC") if local_start else (local["t"].min() if len(local) else None)
    if t0_local is not None and len(local):
        seg = _segment_stats(local, t0_local, now, interval_s, al, acc_ev); elapsed = seg["elapsed_hours"]
        seg["status"] = "进行中" if elapsed < required_hours else ("通过" if (seg["coverage"] or 0) >= 0.95 and seg["longest_gap_hours"] <= 2.0 else "未通过")
        seg["verdict_note"] = "elapsed < 72h → 不得宣称验收成功" if elapsed < required_hours else "覆盖率 ≥ 95% 且最长中断 ≤ 2 h 为通过（基于真实时间，不做任何模拟或起点调整）"
        out["segments"]["local"] = seg; out["status"] = seg["status"]
    else:
        out["segments"]["local"] = {"status": "未开始", "note": "没有本地（非云容器）运行记录"}
    out["segments"]["all"] = _segment_stats(checks, checks["t"].min(), now, interval_s, al, acc_ev) | {"note": "云端 + 本地全部记录；历史缺口原样保留"}
    if not write:
        return out                                                   # 只读模式（审计工具）：不写 acceptance_status.*
    atomic_write_json(log_dir / "acceptance_status.json", out, indent=1, default=str)
    L = [f"# 72 小时真实时间可用性验收 · 本地段状态：**{out['status']}**（{out['category']}）", "", "| 分段 | 起点 UTC | 终点 UTC | 时长 h | 实际轮询 | 预期轮询 | 覆盖率 | 最长中断 h | 行情正常占比 | 行情问题数 | 异常数 | 告警 | 错过信号 | 过期订单 | 成交 | 主机 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, sgm in out["segments"].items():
        if "cycles_observed" in sgm:
            L.append(f"| {k} | {sgm['start_utc'][:16]} | {sgm['end_utc'][:16]} | {sgm['elapsed_hours']} | {sgm['cycles_observed']} | {sgm['cycles_expected']} | {sgm['coverage']:.1%} | {sgm['longest_gap_hours']} | {(sgm['data_ok_share'] or 0):.0%} | {sgm['n_data_issues']} | {sgm['n_anomalies']} | {sgm['alerts_by_kind']} | {sgm['missed_signals']} | {sgm['expired_orders']} | {sgm['fills']} | {sgm['hosts']} |")
        else:
            L.append(f"| {k} | — | — | — | — | — | — | — | — | — | — | — | — | — | — | {sgm.get('note')} |")
    loc = out["segments"].get("local", {})
    if "per_day" in loc:
        L += ["", "## 本地段逐日", "", "| 日期 (UTC) | 轮询 | 行情正常占比 | 问题 | 异常 |", "|---|---|---|---|---|"] + [f"| {d} | {v['cycles']} | {v['data_ok_share']:.0%} | {'; '.join(v['issues'])[:100]} | {'; '.join(v.get('anomalies', []))[:60]} |" for d, v in loc["per_day"].items()]
        L += ["", loc.get("verdict_note", "")]
    atomic_write_text(log_dir / "acceptance_status.md", "\n".join(L)); return out
