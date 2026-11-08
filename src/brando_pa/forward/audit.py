"""前瞻事件时间顺序审计与纠错（第九阶段最终检查）。

审计不变量（每个账户 events.jsonl）：
  T1 信号蜡烛收盘 ≤ 决策时刻（signal_generated_at / decision_time）
  T2 订单提交时刻 ≥ 决策时刻
  T3 执行 K 线开盘时刻 > 决策时刻（禁止用决策前的价格成交）且 ≥ exec_after
  T4 成交记账时刻 ≥ 执行 K 线收盘时刻（只用已收盘 K 线）
  T5 同一 order_id 最多一笔成交；expired / cancelled 的订单没有成交
  T6 现金与持仓不为负；成交后 equity_after = cash_after + Σ position_after × mark_prices
  T7 late_signal / missed_signal / pending / filled / expired / cancelled 严格区分（订单状态唯一）
纠错：对违反 T3 的历史订单（如 2026-11-08 03:26 决策、exec_after 03:00）写 corrections.jsonl（哈希链）标记无效并作废，**不修改原始事件**。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text
from ..trendsys.paper import _append, verify_chain


def _events(p: Path) -> list[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8")] if p.exists() else []


def audit_account(acc_dir: Path) -> dict:
    ev = _events(acc_dir / "events.jsonl"); viol = []; H = pd.Timedelta(hours=1)
    orders = {e["order_id"]: e for e in ev if e["kind"] == "order"}
    fills = [e for e in ev if e["kind"] == "fill"]; expired = {e["order_id"] for e in ev if e["kind"] == "order_expired"}; cancelled = {e["order_id"] for e in ev if e["kind"] == "order_cancelled"}
    corr = {c["order_id"] for c in _events(acc_dir / "corrections.jsonl") if c.get("kind") == "order_invalidated"}
    for e in ev:
        if e["kind"] in ("signal", "missed_signal") and e.get("signal_generated_at") and pd.Timestamp(e["signal_ts"]) > pd.Timestamp(e["signal_generated_at"]):
            viol.append({"rule": "T1", "event": e["kind"], "signal_ts": e["signal_ts"], "decision": e["signal_generated_at"]})
    for oid, o in orders.items():
        dec = o.get("decision_time") or o.get("order_simulated_at"); sub = o.get("order_submit_time") or o.get("order_simulated_at")
        if dec and sub and pd.Timestamp(sub) < pd.Timestamp(dec):
            viol.append({"rule": "T2", "order_id": oid, "decision": dec, "submit": sub})
        if dec and pd.Timestamp(o["exec_after"]) <= pd.Timestamp(dec):
            viol.append({"rule": "T3-slot", "order_id": oid, "decision": dec, "exec_after": o["exec_after"], "status": "invalidated" if oid in corr else ("cancelled" if oid in cancelled else ("expired" if oid in expired else ("FILLED" if any(f["order_id"] == oid for f in fills) else "pending")))})
    seen = {}
    for f in fills:
        oid = f["order_id"]; seen[oid] = seen.get(oid, 0) + 1
        o = orders.get(oid, {}); dec = f.get("decision_time") or o.get("decision_time") or o.get("order_simulated_at")
        if f.get("mode") == "replay" or "price_time" not in f:
            # 回放/旧格式：K 线口径——执行 K 线开盘晚于决策、≥ exec_after，记账在该 K 线收盘后
            t_open = pd.Timestamp(f.get("execution_time") or f["fill_ts"])
            if dec and t_open <= pd.Timestamp(dec):
                viol.append({"rule": "T3-fill", "order_id": oid, "decision": dec, "execution_bar_open": str(t_open)})
            if o and t_open < pd.Timestamp(o["exec_after"]):
                viol.append({"rule": "T3-exec_after", "order_id": oid, "exec_after": o["exec_after"], "execution_bar_open": str(t_open)})
            rec = pd.Timestamp(f.get("fill_recorded_at") or f["recorded_at"])
            if rec < t_open + H:
                viol.append({"rule": "T4", "order_id": oid, "bar_close": str(t_open + H), "recorded_at": str(rec)})
        else:
            # 前瞻：执行时间 = 引擎处理时刻；价格时间 ≤ 执行时间；决策 < 价格时间；执行时间 ≥ exec_after；记账时间 = 执行时间
            ex = pd.Timestamp(f["execution_time"]); pt = pd.Timestamp(f["price_time"]); rec = pd.Timestamp(f.get("fill_recorded_at") or f["recorded_at"])
            if dec and not (pd.Timestamp(dec) < pt <= ex):
                viol.append({"rule": "T3-forward-causality", "order_id": oid, "decision": dec, "price_time": str(pt), "execution_time": str(ex)})
            if o and ex < pd.Timestamp(o["exec_after"]):
                viol.append({"rule": "T3-exec_after", "order_id": oid, "exec_after": o["exec_after"], "execution_time": str(ex)})
            if rec < ex:
                viol.append({"rule": "T4-forward", "order_id": oid, "execution_time": str(ex), "recorded_at": str(rec)})
            if f.get("late") and not str(f.get("order_status", "")).startswith("filled_late"):
                viol.append({"rule": "T7-late-flag", "order_id": oid})
            if f.get("estimated") and "_estimated" not in str(f.get("order_status", "")):
                viol.append({"rule": "T7-estimated-flag", "order_id": oid})
        if oid in expired or oid in cancelled:
            viol.append({"rule": "T5-status", "order_id": oid})
        if "cash_after" in f:
            if f["cash_after"] < -1e-9 or any(v < -1e-12 for v in f["position_after"].values()):
                viol.append({"rule": "T6-negative", "order_id": oid})
            eq = f["cash_after"] + sum(f["position_after"][s] * f["mark_prices"][s] for s in f["position_after"])
            if abs(eq - f["equity_after"]) > 1e-6:
                viol.append({"rule": "T6-equity", "order_id": oid, "equity_after": f["equity_after"], "recomputed": eq})
    viol += [{"rule": "T5-dup", "order_id": oid, "n": n} for oid, n in seen.items() if n > 1]
    navs = [e for e in ev if e["kind"] == "nav"]
    for e in navs:
        for k, l in e["ledgers"].items():
            if l["cash"] < -1e-9 or any(v < -1e-12 for v in l["holdings"].values()):
                viol.append({"rule": "T6-nav", "ts": e["ts"], "ledger": k})
    statuses = {}
    for oid in orders:
        st = []
        if any(f["order_id"] == oid for f in fills): st.append("filled")
        if oid in expired: st.append("expired")
        if oid in cancelled: st.append("cancelled")
        if oid in corr: st.append("invalidated")
        statuses[oid] = st or ["pending"]
        if len(set(st) - {"invalidated"}) > 1:
            viol.append({"rule": "T7", "order_id": oid, "statuses": st})
    open_viol = [v for v in viol if not (v["rule"] == "T3-slot" and v.get("status") == "invalidated")]      # 已纠错（作废、未成交）的时隙违规保留在报告中，但不再构成未决问题
    return {"account": acc_dir.name, "n_events": len(ev), "n_orders": len(orders), "n_fills": len(fills), "n_expired": len(expired), "n_cancelled": len(cancelled), "n_invalidated": len(corr), "order_statuses": statuses, "violations": viol, "open_violations": open_viol, "chain": verify_chain(acc_dir / "events.jsonl"), "corrections_chain": verify_chain(acc_dir / "corrections.jsonl") if (acc_dir / "corrections.jsonl").exists() else None, "ok": not open_viol}


def audit_timeline(log_dir: Path, write: bool = True) -> dict:
    log_dir = Path(log_dir); out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "accounts": {}, "ok": True}
    for d in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        a = audit_account(d); out["accounts"][d.name] = a; out["ok"] &= a["ok"]
    if write:
        atomic_write_json(log_dir / "timeline_audit.json", out, indent=1, default=str)
        L = [f"# 前瞻事件时间顺序审计 · {out['checked_at_utc'][:19]} UTC · 总体 {'通过' if out['ok'] else '发现违规'}", "", "| 账户 | 事件 | 订单 | 成交 | 过期 | 作废 | 已纠错 | 违规 | 链 |", "|---|---|---|---|---|---|---|---|---|"]
        for n, a in out["accounts"].items():
            L.append(f"| {n} | {a['n_events']} | {a['n_orders']} | {a['n_fills']} | {a['n_expired']} | {a['n_cancelled']} | {a['n_invalidated']} | {len(a['open_violations'])}（含已纠错 {len(a['violations']) - len(a['open_violations'])}） | {'ok' if a['chain']['ok'] else 'BROKEN'} |")
        for n, a in out["accounts"].items():
            for v in a["violations"]:
                L.append(f"- {n}: {json.dumps(v, ensure_ascii=False)}")
        atomic_write_text(log_dir / "timeline_audit.md", "\n".join(L))
    return out


def repair_invalid_orders(log_dir: Path, now: pd.Timestamp | None = None) -> dict:
    """对 exec_after ≤ decision_time 的挂单：写 corrections.jsonl（哈希链）标记无效，追加 order_cancelled 事件，把订单移出 pending；原始 order 事件保持不变。只处理尚未成交的订单；已成交的（若有）只标记，不改账本。"""
    now = now or pd.Timestamp.now(tz="UTC"); log_dir = Path(log_dir); out = {}
    lock = log_dir / "service.lock"
    if lock.exists():
        import fcntl
        fh = open(lock, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            raise RuntimeError("forward-service 正在运行（持有 service.lock）；请先停止服务再执行纠错")
        finally:
            fh.close()
    for d in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        ev = _events(d / "events.jsonl"); orders = [e for e in ev if e["kind"] == "order"]; fills = {e["order_id"] for e in ev if e["kind"] == "fill"}
        sp = d / "state.json"; st = json.loads(sp.read_text()) if sp.exists() else None; done = []
        for o in orders:
            dec = o.get("decision_time") or o.get("order_simulated_at")
            if not dec or pd.Timestamp(o["exec_after"]) > pd.Timestamp(dec):
                continue
            already = {c["order_id"] for c in _events(d / "corrections.jsonl")}
            if o["order_id"] in already:
                continue
            rec = {"kind": "order_invalidated", "order_id": o["order_id"], "original_event_hash": o.get("hash"), "decision_time": dec, "exec_after": o["exec_after"], "filled": o["order_id"] in fills, "reason": "exec_after 不晚于决策时刻：决策形成后不得以更早开盘的 K 线价格成交（规则 T3）", "action": "订单作废；原始事件保留；账本未变动" if o["order_id"] not in fills else "标记无效（已成交，账本未回滚，供人工复核）", "corrected_at": str(now)}
            _append(d / "corrections.jsonl", rec)
            _append(d / "events.jsonl", {**{k: v for k, v in o.items() if k not in ("kind", "prev_hash", "hash", "recorded_at")}, "kind": "order_cancelled", "account": d.name, "order_status": "cancelled_invalid", "cancelled_at": str(now), "reason_cancelled": "invalidated by corrections.jsonl (T3)"})
            if st is not None:
                st["pending"] = [p_ for p_ in st.get("pending", []) if p_["order_id"] != o["order_id"]]; st.setdefault("processed_orders", []).append(o["order_id"])
            done.append(o["order_id"])
        if st is not None and done:
            atomic_write_json(sp, st, indent=1, default=str)
        out[d.name] = done
    return out
