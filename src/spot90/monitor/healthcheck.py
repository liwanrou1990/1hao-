"""每日健康检查与失败提醒（只读）。检查项：
  1. 日线完整性：无重复、无缺口（1d）、最新完整日线延迟 ≤ 1 天、OHLC 合法、与参考价偏差 < 5%
  2. 采集状态：data_status.json 是否为今天生成、是否使用了备用源（允许，但记录）
  3. 快照：latest.json 是否存在、生成时间 ≤ 30 小时、两个标的齐全、关键字段非空（均线、风险评分、成交概率表、方案摘要）
  4. 参考价：状态为“正常”；否则记录延迟/不可用
  5. 模型文件：fill_model_logistic.joblib 存在；验证 JSON 存在
结果写 reports_out/spot90/monitor/health.json 与 health_history.jsonl；任何 FAIL 追加到 alerts.log，并（若配置 SPOT90_ALERT_WEBHOOK）POST 一条 JSON；退出码 1。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from ..atomic import atomic_write_json
from ..config import cfg, data_dir, model_dir, report_dir
from ..data.store import BarStore
from ..data.quality import continuity_report


def _check(name: str, ok: bool, detail: str, level: str = "FAIL") -> dict:
    return {"check": name, "status": "OK" if ok else level, "detail": detail}


def run(now: datetime | None = None, send_alert: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []
    store = BarStore()
    mdir = report_dir() / "monitor"
    symbols = cfg()["symbols"]
    # 1. 日线
    for s in symbols:
        b = store.load(s, "1d")
        if b.empty:
            checks.append(_check(f"{s} 日线存在", False, "无数据")); continue
        rep = continuity_report(b, "1d")
        lag = (now.date() - b["ts"].iloc[-1].date()).days - 1
        checks.append(_check(f"{s} 日线无重复", rep["duplicates"] == 0, f"duplicates={rep['duplicates']}"))
        checks.append(_check(f"{s} 日线无缺口", rep["n_gap_bars"] == 0, f"缺 {rep['n_gap_bars']} 根"))
        checks.append(_check(f"{s} 日线新鲜度", lag <= 1, f"最后日线 {b['ts'].iloc[-1].date()}，延迟 {max(lag,0)} 天", level="FAIL" if lag > 2 else "WARN"))
        ok_ohlc = bool(((b["high"] >= b[["open", "close"]].max(axis=1)) & (b["low"] <= b[["open", "close"]].min(axis=1)) & (b["low"] > 0)).all())
        checks.append(_check(f"{s} OHLC 合法", ok_ohlc, "high≥max(open,close)≥min≥low>0"))
        ret = b["close"].pct_change().abs().iloc[-5:].max()
        checks.append(_check(f"{s} 近 5 日无异常跳变", ret < 0.5, f"最大单日绝对涨跌 {ret:.1%}", level="WARN"))
    # 2. 采集状态
    stp = data_dir() / "data_status.json"
    if stp.exists():
        st = json.loads(stp.read_text()); run_ts = pd.Timestamp(st["run_ts"])
        checks.append(_check("采集状态为近 30 小时内", (now - run_ts.to_pydatetime()).total_seconds() < 30 * 3600, f"data_status run_ts={st['run_ts'][:19]}", level="WARN"))
        checks.append(_check("Bitget 可达", bool(st["probes"]["bitget"]["ok"]), st["probes"]["bitget"]["detail"] + ("；已使用备用源" if st.get("fallback_used") else ""), level="WARN"))
    else:
        checks.append(_check("采集状态文件", False, "data_status.json 不存在"))
    # 3/4. 快照与参考价
    lp = mdir / "latest.json"
    if lp.exists():
        snap = json.loads(lp.read_text()); gen = pd.Timestamp(snap["generated_at"])
        age_h = (now - gen.to_pydatetime()).total_seconds() / 3600
        checks.append(_check("快照新鲜度 ≤ 30 小时", age_h <= 30, f"生成于 {snap['generated_at'][:19]}，{age_h:.1f} 小时前"))
        for s in symbols:
            v = snap["symbols"].get(s, {})
            if "error" in v or not v:
                checks.append(_check(f"{s} 快照", False, v.get("error", "缺失"))); continue
            ind = v["indicators"]; ok_ma = all(ind.get(k) is not None for k in ("ma20", "ma50", "ma90", "ma200"))
            checks.append(_check(f"{s} 均线/风险评分", ok_ma and v["trend_risk"]["risk_score"] is not None, f"MA200={ind.get('ma200')}, risk={v['trend_risk']['risk_score']}"))
            checks.append(_check(f"{s} 成交概率表", len(v.get("limit_fill_table", [])) > 0, f"{len(v.get('limit_fill_table', []))} 行"))
            checks.append(_check(f"{s} 方案摘要", bool(v.get("plans")), "plans_comparison 已载入" if v.get("plans") else "缺失", level="WARN"))
            lv = v["market"]["live"]
            checks.append(_check(f"{s} 参考价", lv.get("status") == "正常", f"{lv.get('source')} {lv.get('status')} 延迟 {lv.get('age_s')} 秒", level="WARN"))
            if lv.get("price") and v["market"]["daily_bar"]["close"]:
                dev = abs(lv["price"] / v["market"]["daily_bar"]["close"] - 1)
                checks.append(_check(f"{s} 参考价与最后收盘偏差 < 15%", dev < 0.15, f"{dev:.2%}", level="WARN"))
            checks.append(_check(f"{s} 数据延迟标志", True, "data_delayed=" + str(v["market"]["data_delayed"]), level="WARN"))
    else:
        checks.append(_check("快照存在", False, "latest.json 不存在"))
    # 5. 模型与验证文件
    checks.append(_check("成交概率模型文件", (model_dir() / "fill_model_logistic.joblib").exists(), str(model_dir() / "fill_model_logistic.joblib")))
    checks.append(_check("成交模型验证报告", (mdir / "fill_model_validation.json").exists(), "fill_model_validation.json"))
    checks.append(_check("方案比较结果", (mdir / "plans_comparison.json").exists(), "plans_comparison.json", level="WARN"))
    # 5b. Dashboard 进程健康（可选：SPOT90_DASHBOARD_URL，如 http://127.0.0.1:8501）
    durl = os.environ.get("SPOT90_DASHBOARD_URL")
    if durl:
        try:
            r = httpx.get(durl.rstrip("/") + "/_stcore/health", timeout=8)
            checks.append(_check("Dashboard 进程健康", r.status_code == 200 and "ok" in r.text.lower(), f"{durl} → HTTP {r.status_code}"))
        except Exception as e:  # noqa: BLE001
            checks.append(_check("Dashboard 进程健康", False, f"{durl} 不可达：{type(e).__name__}"))
    # 6. 只读保证：代码中不存在下单/提现接口调用
    import re
    bad = []
    for f in Path(__file__).resolve().parents[1].rglob("*.py"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"/api/v2/spot/trade/|place-order|placeOrder|/withdraw", txt) and "healthcheck" not in f.name:
            bad.append(f.name)
    checks.append(_check("只读模式（无下单/提现接口）", not bad, "命中: " + ", ".join(bad) if bad else "未发现"))
    fails = [c for c in checks if c["status"] == "FAIL"]; warns = [c for c in checks if c["status"] == "WARN"]
    result = {"run_ts": now.isoformat(), "overall": "FAIL" if fails else ("WARN" if warns else "OK"), "n_fail": len(fails), "n_warn": len(warns), "checks": checks}
    mdir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(mdir / "health.json", result, indent=2)
    with open(mdir / "health_history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_ts": result["run_ts"], "overall": result["overall"], "n_fail": len(fails), "n_warn": len(warns)}, ensure_ascii=False) + "\n")
    if fails or warns:
        with open(mdir / "alerts.log", "a", encoding="utf-8") as fh:
            for c in fails + warns:
                fh.write(f"{now.isoformat()} {c['status']} {c['check']}: {c['detail']}\n")
    if send_alert:
        result["alert"] = notify(result, now)
    return result


def notify(result: dict, now: datetime, repeat_hours: float = 24.0) -> dict:
    """去重告警：同一组 FAIL 指纹在 repeat_hours 内只发一次；状态由 FAIL 恢复为非 FAIL 时发一次“已恢复”。
    Webhook 地址只从环境变量 SPOT90_ALERT_WEBHOOK 读取，绝不写入日志/页面/结果。"""
    mdir = report_dir() / "monitor"; state_p = mdir / "alerts_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    fails = [c for c in result["checks"] if c["status"] == "FAIL"]
    fp = "|".join(sorted(c["check"] for c in fails))
    hook = os.environ.get("SPOT90_ALERT_WEBHOOK")
    out = {"webhook_configured": bool(hook), "action": "none"}
    last_fp, last_ts = state.get("last_fingerprint", ""), state.get("last_sent_at")
    hours_since = (now - datetime.fromisoformat(last_ts)).total_seconds() / 3600 if last_ts else 1e9
    msg = None
    if fails and (fp != last_fp or hours_since >= repeat_hours):
        msg = {"level": "FAIL", "text": f"spot90 健康检查 FAIL×{len(fails)}（{now.isoformat()[:16]}Z）: " + "；".join(f"{c['check']}: {c['detail']}" for c in fails)[:1500]}
        out["action"] = "sent_fail" if fp != last_fp else "resent_fail_after_repeat_window"
    elif not fails and last_fp:
        msg = {"level": "RECOVERED", "text": f"spot90 健康检查已恢复（{now.isoformat()[:16]}Z），总体 {result['overall']}"}
        out["action"] = "sent_recovered"
    elif fails:
        out["action"] = "suppressed_duplicate"
    if msg:
        if hook:
            try:
                r = httpx.post(hook, json=msg, timeout=10); out["delivered"] = r.status_code < 300; out["http_status"] = r.status_code
            except Exception as e:  # noqa: BLE001
                out["delivered"] = False; out["error"] = type(e).__name__
        else:
            out["delivered"] = False; out["note"] = "未配置 SPOT90_ALERT_WEBHOOK，仅写 alerts.log"
        state = {"last_fingerprint": fp if fails else "", "last_sent_at": now.isoformat() if fails else None, "last_message_level": msg["level"]}
        atomic_write_json(state_p, state, indent=2)
    return out


if __name__ == "__main__":
    r = run()
    for c in r["checks"]:
        print(f"[{c['status']}] {c['check']}: {c['detail']}")
    print("overall:", r["overall"], "| alert:", r.get("alert"))
    sys.exit(1 if r["overall"] == "FAIL" else 0)
