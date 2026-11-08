"""连续 72 小时真实时间可用性验收（第九阶段部署）：只统计真实时间推进中的记录（心跳、轮询、数据故障、错过信号、重启），
不做任何模拟；时间覆盖不足 72 小时时结论固定为“未完成”。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text


def _jsonl(p: Path) -> pd.DataFrame:
    return pd.read_json(p, lines=True) if p.exists() and p.stat().st_size else pd.DataFrame()


def acceptance_status(log_dir: Path, interval_s: int = 600, required_hours: float = 72.0, now: pd.Timestamp | None = None, start: str | None = None) -> dict:
    log_dir = Path(log_dir); now = now or pd.Timestamp.now(tz="UTC")
    re_ = _jsonl(log_dir / "runner_events.jsonl"); al = _jsonl(log_dir / "alerts.jsonl")
    checks = re_[(re_["kind"] == "data_check") & (re_.get("mode", "forward") != "replay")].copy() if len(re_) else pd.DataFrame()
    out = {"checked_at_utc": str(now), "required_hours": required_hours, "category": "真实时间前瞻运行（非模拟）", "status": "未开始"}
    if checks.empty:
        atomic_write_json(log_dir / "acceptance_status.json", out, indent=1, default=str); return out
    checks["t"] = pd.to_datetime(checks["now"], utc=True)
    t0 = pd.Timestamp(start, tz="UTC") if start else checks["t"].min(); win = checks[checks["t"] >= t0]
    elapsed_h = (now - t0).total_seconds() / 3600; expected = max(int(elapsed_h * 3600 / interval_s), 1)
    gaps = win["t"].diff().dt.total_seconds().dropna(); n_gaps = int((gaps > interval_s * 3).sum()); longest_gap_h = float(gaps.max() / 3600) if len(gaps) else 0.0
    per_day = {}
    for d, g in win.groupby(win["t"].dt.date):
        per_day[str(d)] = {"cycles": int(len(g)), "data_ok_share": float(g["ok"].astype(bool).mean()), "issues": sorted({x for xs in g["issues"] for x in (xs or [])})[:5]}
    missed = {}; expired = {}; restarts = 0
    for acc in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev = _jsonl(acc / "events.jsonl")
        if len(ev) and "kind" in ev:
            ev = ev[pd.to_datetime(ev.get("recorded_at"), utc=True, errors="coerce") >= t0] if "recorded_at" in ev else ev
            missed[acc.name] = int((ev["kind"] == "missed_signal").sum()); expired[acc.name] = int((ev["kind"] == "order_expired").sum())
    sc = log_dir / "startup_check.json"
    if sc.exists():
        restarts = int(len([l for l in (log_dir / "service.log").read_text().splitlines() if "service start" in l])) if (log_dir / "service.log").exists() else 1
    alerts = al[pd.to_datetime(al["recorded_at"], utc=True) >= t0] if len(al) else al
    out |= {"window_start_utc": str(t0), "elapsed_hours": round(elapsed_h, 2), "cycles_observed": int(len(win)), "cycles_expected": expected, "coverage": round(min(len(win) / expected, 1.0), 3), "cycle_gaps_gt_3x": n_gaps, "longest_gap_hours": round(longest_gap_h, 2),
            "data_ok_share": float(win["ok"].astype(bool).mean()), "per_day": per_day, "missed_signals": missed, "expired_orders": expired, "service_starts": restarts, "alerts_by_kind": alerts["kind"].value_counts().to_dict() if len(alerts) else {},
            "status": ("进行中" if elapsed_h < required_hours else ("通过" if (min(len(win) / expected, 1.0) >= 0.95 and longest_gap_h <= 2.0) else "未通过")), "verdict_note": "elapsed < 72h → 不得宣称验收成功" if elapsed_h < required_hours else "覆盖率 ≥ 95% 且最长中断 ≤ 2 h 为通过"}
    atomic_write_json(log_dir / "acceptance_status.json", out, indent=1, default=str)
    L = [f"# 72 小时真实时间可用性验收 · 状态：**{out['status']}**（{out['category']}）", "", f"- 窗口起点 {out['window_start_utc']}，已运行 {out['elapsed_hours']} h / 要求 {required_hours} h", f"- 轮询覆盖率 {out['coverage']:.1%}（观察 {out['cycles_observed']} / 预期 {out['cycles_expected']}），中断 > 3×间隔 {out['cycle_gaps_gt_3x']} 次，最长中断 {out['longest_gap_hours']} h", f"- 行情检查正常占比 {out['data_ok_share']:.1%}；错过信号 {missed}；过期订单 {expired}；服务启动次数 {restarts}；告警 {out['alerts_by_kind']}", "", "| 日期 (UTC) | 轮询 | 行情正常占比 | 问题 |", "|---|---|---|---|"]
    L += [f"| {d} | {v['cycles']} | {v['data_ok_share']:.0%} | {'; '.join(v['issues'])[:120]} |" for d, v in per_day.items()]
    L += ["", out["verdict_note"]]
    atomic_write_text(log_dir / "acceptance_status.md", "\n".join(L)); return out
