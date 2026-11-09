"""第十三阶段数据与订单审计 + 前瞻绩效（只读影子目录）：
复用 forward.audit（T1–T8）、forward.ledger_audit（费用 / 净值）、forward.acceptance（连续性）；新增：信号延迟、数据缺失 / 停机、每日净值、回撤与持续时间、
相对买入持有差额、30/90/180/365 天窗口（只评价已到期窗口）、行为观察（追高回补 / 无效空仓 / 主升段暴露）、“无未来日期”断言。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..forward.acceptance import acceptance_status
from ..forward.audit import audit_timeline
from ..forward.ledger_audit import ledger_audit

HORIZONS = (30, 90, 180, 365)


def _ev(p: Path) -> pd.DataFrame:
    return pd.read_json(p, lines=True) if p.exists() and p.stat().st_size else pd.DataFrame()


def daily_nav(acc_dir: Path, ledger: str = "cost") -> pd.DataFrame:
    ev = _ev(acc_dir / "events.jsonl")
    if ev.empty or "kind" not in ev:
        return pd.DataFrame(columns=["ts", "equity", "liquidation", "cash", "exposure"])
    nav = ev[ev["kind"] == "nav"].copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True)
    rows = [{"ts": r["ts"], "equity": r["ledgers"][ledger]["equity_usdt"], "liquidation": r["ledgers"][ledger]["liquidation_usdt"], "cash": r["ledgers"][ledger]["cash"], "exposure": r["ledgers"][ledger]["exposure"], "prices": r.get("prices")} for _, r in nav.iterrows()]
    d = pd.DataFrame(rows); d["day"] = d["ts"].dt.normalize(); return d.groupby("day").last().reset_index()


def drawdown(eq: pd.Series) -> dict:
    if eq.empty:
        return {"max_drawdown": None, "max_drawdown_usdt": None, "longest_drawdown_days": None}
    peak = eq.cummax(); dd = eq / peak - 1; longest = 0; cur = None
    for t, v in dd.items():
        if v < -1e-12:
            cur = cur or t
        elif cur is not None:
            longest = max(longest, (t - cur).days); cur = None
    if cur is not None:
        longest = max(longest, (dd.index[-1] - cur).days + 1)
    return {"max_drawdown": float(dd.min()), "max_drawdown_usdt": float((eq - peak).min()), "longest_drawdown_days": int(longest)}


def shadow_audit(log_dir: Path, now: pd.Timestamp | None = None, out_dir: Path | None = None, interval_s: int = 600, write: bool = True) -> dict:
    log_dir = Path(log_dir); now = now or pd.Timestamp.now(tz="UTC"); out_dir = Path(out_dir or (log_dir / "audit"))
    accs = sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()) if log_dir.exists() else []
    out = {"checked_at_utc": str(now), "category": "forward（前瞻模拟；独立影子账户；非历史回测）", "log_dir": str(log_dir), "accounts": {}, "ok": True, "future_dates_found": False}
    if not accs:
        out["status"] = "尚未开始：没有影子账户记录（前瞻数据尚未产生）"; out["ok"] = True
        if write:
            out_dir.mkdir(parents=True, exist_ok=True); atomic_write_json(out_dir / "shadow_audit.json", out, indent=1, default=str); atomic_write_text(out_dir / "shadow_audit.md", f"# 第十三阶段前瞻审计 · {out['status']}")
        return out
    tl = audit_timeline(log_dir, write=False); la = ledger_audit(log_dir, out_dir / "ledger", interval_s, write=False)
    try:
        acc = acceptance_status(log_dir, interval_s, now=now, cloud_hosts=(), cloud_segment_end=None, write=False); seg = acc["segments"].get("local", {})
    except Exception as e_:  # noqa: BLE001
        seg = {"error": str(e_)}
    re_ = _ev(log_dir / "runner_events.jsonl"); checks = re_[re_["kind"] == "data_check"] if len(re_) else pd.DataFrame(); gaps = []
    if len(checks) > 1:
        t = pd.to_datetime(checks["now"], utc=True).sort_values(); g = t.diff().dt.total_seconds(); big = g[g > interval_s * 3]
        gaps = [{"from": str(t.iloc[i - 1]), "to": str(t.iloc[i]), "hours": round(float(g.iloc[i]) / 3600, 2)} for i in big.index]
    navs = {d.name: daily_nav(d) for d in accs}; bh = navs.get("C_BUYHOLD_BTC")
    for d in accs:
        n = d.name; ev = _ev(d / "events.jsonl"); st = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
        nav = navs[n]
        # 未来日期检查：事件自带的运行时钟字段（ts / decision_time / signal_ts / fill_ts / execution_time）不得晚于审计时钟（recorded_at 是墙钟，模拟时钟测试下不可比）
        for col in ("ts", "decision_time", "signal_ts", "fill_ts", "execution_time"):
            if col in ev:
                t_ = pd.to_datetime(ev[col], utc=True, errors="coerce").dropna()
                if len(t_) and t_.max() > now + pd.Timedelta(minutes=5):
                    out["future_dates_found"] = True; out["ok"] = False
        fs = pd.Timestamp(st["forward_start_utc"]) if st.get("forward_start_utc") else None; elapsed_days = (now - fs).total_seconds() / 86400 if fs is not None else 0.0
        sig = ev[ev["kind"].isin(["signal", "missed_signal"])] if "kind" in ev else pd.DataFrame(); fills = ev[ev["kind"] == "fill"] if "kind" in ev else pd.DataFrame()
        lat = None
        if len(sig):
            lat = (pd.to_datetime(sig["decision_time"], utc=True) - pd.to_datetime(sig["signal_ts"], utc=True)).dt.total_seconds() / 60
        fill_lat = None
        if len(fills) and "execution_time" in fills:
            fill_lat = (pd.to_datetime(fills["execution_time"], utc=True) - pd.to_datetime(fills["decision_time"], utc=True)).dt.total_seconds() / 60
        eq = nav.set_index("day")["liquidation"] if len(nav) else pd.Series(dtype=float); dd = drawdown(eq)
        horizons = {}
        for h in HORIZONS:
            if fs is None or elapsed_days < h:
                horizons[f"{h}d"] = {"status": "未到期", "elapsed_days": round(elapsed_days, 1)}; continue
            end = fs + pd.Timedelta(days=h); e = eq[eq.index <= end.normalize()]
            if e.empty:
                horizons[f"{h}d"] = {"status": "无数据"}; continue
            row = {"status": "已到期", "liquidation_usdt_at_horizon": float(e.iloc[-1]), "net_profit_usdt": float(e.iloc[-1]) - float(st.get("initial_usdt", 10000.0)), **drawdown(e)}
            if bh is not None and len(bh):
                b = bh.set_index("day")["liquidation"]; b = b[b.index <= end.normalize()]
                if len(b):
                    row["diff_vs_btc_buyhold_usdt"] = float(e.iloc[-1] - b.iloc[-1])
            horizons[f"{h}d"] = row
        # 行为观察（仅已有数据；窗口不足则 None）
        beh = {}
        if len(nav) and nav["prices"].notna().any():
            px = pd.Series({r["day"]: (r["prices"] or {}).get("BTCUSDT") for _, r in nav.iterrows()}).dropna().astype(float); ex = nav.set_index("day")["exposure"]
            if len(px) >= 31:
                r30 = px / px.shift(30) - 1; up = r30 >= 0.20; down = r30 <= -0.20
                beh["avg_exposure_in_btc_30d_up_ge20pct"] = float(ex[up[up].index].mean()) if up.any() else None; beh["avg_exposure_in_btc_30d_down_ge20pct"] = float(ex[down[down].index].mean()) if down.any() else None
            else:
                beh["note"] = "前瞻不足 31 日：主升 / 主跌段观察未到期"
            flat = (ex < 0.01).astype(int); run = 0; longest_flat = 0
            for v in flat:
                run = run + 1 if v else 0; longest_flat = max(longest_flat, run)
            beh["longest_flat_days"] = int(longest_flat)
        chase = 0
        if len(fills) >= 2:
            f = fills.sort_values("recorded_at")
            for sym, g in f.groupby("symbol"):
                g = g.reset_index(drop=True)
                for i in range(1, len(g)):
                    if g.loc[i - 1, "side"] == "SELL" and g.loc[i, "side"] == "BUY" and (pd.to_datetime(g.loc[i, "fill_ts"], utc=True) - pd.to_datetime(g.loc[i - 1, "fill_ts"], utc=True)).days <= 10 and g.loc[i, "fill_price"] > g.loc[i - 1, "fill_price"]:
                        chase += 1
        beh["sell_then_rebuy_higher_within_10d"] = chase
        out["accounts"][n] = {"label": st.get("account"), "forward_start_utc": str(fs) if fs is not None else None, "elapsed_days": round(elapsed_days, 2), "n_runs": st.get("n_runs"), "last_daily_ts": st.get("last_daily_ts"),
                              "n_signals": int(len(sig)), "n_missed": int((sig["kind"] == "missed_signal").sum()) if len(sig) else 0, "n_fills": int(len(fills)), "n_fills_estimated": int(fills["estimated"].fillna(False).astype(bool).sum()) if len(fills) and "estimated" in fills else 0, "n_fills_late": int(fills["late"].fillna(False).astype(bool).sum()) if len(fills) and "late" in fills else 0,
                              "signal_latency_min": {"median": float(lat.median()), "max": float(lat.max())} if lat is not None and len(lat) else None, "fill_latency_min": {"median": float(fill_lat.median()), "max": float(fill_lat.max())} if fill_lat is not None and len(fill_lat) else None,
                              "latest": {"day": str(eq.index[-1].date()) if len(eq) else None, "equity_usdt": float(nav["equity"].iloc[-1]) if len(nav) else None, "liquidation_usdt": float(eq.iloc[-1]) if len(eq) else None, "cash_usdt": float(nav["cash"].iloc[-1]) if len(nav) else None, "exposure": float(nav["exposure"].iloc[-1]) if len(nav) else None},
                              "drawdown": dd, "diff_vs_btc_buyhold_usdt": (float(eq.iloc[-1] - bh.set_index("day")["liquidation"].iloc[-1]) if (bh is not None and len(bh) and len(eq)) else None), "horizons": horizons, "behaviour": beh,
                              "timeline_ok": tl["accounts"].get(n, {}).get("ok"), "ledger_ok": la["accounts"].get(n, {}).get("ok"), "chain_ok": tl["accounts"].get(n, {}).get("chain", {}).get("ok")}
        out["ok"] &= bool(tl["accounts"].get(n, {}).get("ok", True)) and bool(la["accounts"].get(n, {}).get("ok", True))
    out["service"] = {"continuity": {k: seg.get(k) for k in ("start_utc", "end_utc", "elapsed_hours", "cycles_observed", "cycles_expected", "coverage", "longest_gap_hours", "data_ok_share", "n_data_issues", "hosts", "status")}, "gaps_gt_3_intervals": gaps, "runner_chain": tl.get("ok")}
    out["evaluation_rules"] = "docs/brando_pa_validation/phase13_forward/evaluation_rules.md"
    if write:
        out_dir.mkdir(parents=True, exist_ok=True); atomic_write_json(out_dir / "shadow_audit.json", out, indent=1, default=str); atomic_write_text(out_dir / "shadow_audit.md", render_audit_md(out))
    return out


def render_audit_md(a: dict) -> str:
    L = [f"# 第十三阶段前瞻审计 · {a['checked_at_utc'][:19]} UTC · {a['category']} · 总体 {'正常' if a['ok'] else '发现问题'}", ""]
    if "status" in a:
        return "\n".join(L + [a["status"]])
    L += ["| 影子账户 | 起点 UTC | 已运行(日) | 信号/错过 | 成交(估算/迟) | 最新清仓净值 | 现金 | 暴露 | 最大回撤 | 最长回撤(日) | vs BTC 持有 | 时间审计 | 账本 | 链 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, x in a["accounts"].items():
        lt = x["latest"]; d = x["drawdown"]
        L.append(f"| {n} | {str(x['forward_start_utc'])[:16]} | {x['elapsed_days']} | {x['n_signals']}/{x['n_missed']} | {x['n_fills']}({x['n_fills_estimated']}/{x['n_fills_late']}) | {lt['liquidation_usdt'] if lt['liquidation_usdt'] is None else f'{lt['liquidation_usdt']:,.2f}'} | {lt['cash_usdt'] if lt['cash_usdt'] is None else f'{lt['cash_usdt']:,.0f}'} | {lt['exposure'] if lt['exposure'] is None else f'{lt['exposure']:.0%}'} | {d['max_drawdown'] if d['max_drawdown'] is None else f'{d['max_drawdown']:.2%}'} | {d['longest_drawdown_days']} | {x['diff_vs_btc_buyhold_usdt'] if x['diff_vs_btc_buyhold_usdt'] is None else f'{x['diff_vs_btc_buyhold_usdt']:+,.2f}'} | {'通过' if x['timeline_ok'] else '违规'} | {'一致' if x['ledger_ok'] else '问题'} | {'ok' if x['chain_ok'] else 'BROKEN'} |")
    L += ["", "## 窗口绩效（只评价已到期窗口）", "", "| 影子账户 | 30 天 | 90 天 | 180 天 | 365 天 |", "|---|---|---|---|---|"]
    for n, x in a["accounts"].items():
        cells = []
        for h in HORIZONS:
            v = x["horizons"][f"{h}d"]; cells.append(v["status"] if v["status"] != "已到期" else f"{v['liquidation_usdt_at_horizon']:,.0f}（{v['net_profit_usdt']:+,.0f}；vs BTC {v.get('diff_vs_btc_buyhold_usdt', float('nan')):+,.0f}；回撤 {v['max_drawdown']:.1%}）")
        L.append(f"| {n} | " + " | ".join(cells) + " |")
    L += ["", "## 行为观察"] + [f"- {n}: {x['behaviour']}" for n, x in a["accounts"].items()]
    L += ["", "## 服务连续性与数据", f"- {a['service']['continuity']}", f"- 超过 3 个周期的中断：{a['service']['gaps_gt_3_intervals'] or '无'}", f"- 延迟：" + "; ".join(f"{n} 信号 {x['signal_latency_min']} 分钟 / 成交 {x['fill_latency_min']} 分钟" for n, x in a["accounts"].items()), f"- 未来日期检查：{'发现未来时间戳！' if a['future_dates_found'] else '无'}", "", f"评价规则：{a['evaluation_rules']}；真实交易禁用；本审计未写任何账户文件。"]
    return "\n".join(L)
