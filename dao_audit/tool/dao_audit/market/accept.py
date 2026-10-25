"""Acceptance gate across the four phases. Honest dependency gating: a phase is
BLOCKED if its prerequisite did not pass. Nothing is marked passed without real
evidence from the actual run.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone


def evaluate(reports_dir: Path) -> dict:
    def _load(name):
        p = reports_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    conn = _load("connection_report.json")
    edge = _load("edge_report.json")
    scan = _load("market_scan.json")
    paper = _load("paper_book.json")

    phases = []

    # Phase 1: the data sources the edge study actually needs (governance +
    # historical prices) must be OK. RPC degradation is noted, not blocking,
    # because the estimator uses Snapshot + DefiLlama, not chain RPC.
    def _cat(name_sub):
        if not conn:
            return None
        for c in conn.get("checks", []):
            if name_sub in c.get("name", ""):
                return c.get("category")
        return None
    price_ok = _cat("DefiLlama price (current)") == "OK" and _cat("DefiLlama price (historical)") == "OK"
    gov_ok = _cat("Snapshot") == "OK"
    denied = conn.get("denied_hosts", []) if conn else []
    p1_ok = bool(conn and price_ok and gov_ok and not denied)
    rpc_note = []
    for c in (conn.get("checks", []) if conn else []):
        if c.get("name", "").startswith("RPC") and c.get("category") != "OK":
            rpc_note.append(f"{c['name']}={c['category']}")
    phases.append({"phase": "1 真实数据连接", "status": "PASS" if p1_ok else "BLOCKED",
                   "evidence": ((f"{conn.get('ok')}/{conn.get('total')} 源 OK; 行情&治理源可用"
                                 + ("; RPC降级: " + ", ".join(rpc_note) if rpc_note else ""))
                                if conn else "未运行"),
                   "blocker": None if p1_ok else (
                       (f"被网络策略拒绝: {', '.join(denied)}" if denied else
                        "行情或治理源不可用") if conn else "缺 connection_report.json")})

    # Phase 2: event-edge model with >=1 confirmed OOS-positive edge
    p2_ok = bool(edge and edge.get("status") == "OK" and edge.get("edge_model"))
    phases.append({"phase": "2 事件收益模型", "status": "PASS" if p2_ok else "BLOCKED",
                   "evidence": (f"events={edge.get('events')}, confirmed={len(edge.get('edge_model', {}))}"
                                if edge else "未运行"),
                   "blocker": None if p2_ok else (
                       ("0 个样本外确认的正edge（多重检验校正后，真实数据未显示可持续优势）"
                        if (edge and edge.get("status") == "OK") else
                        (edge.get("reason") if edge else "缺 edge_report.json")))})

    # Phase 3: scan produced >=1 TRADE from real data
    n_trade = (scan or {}).get("summary", {}).get("trade_signals", 0)
    p3_ok = bool(p2_ok and n_trade >= 1)
    phases.append({"phase": "3 真实成本后 TRADE 信号", "status": "PASS" if p3_ok else "BLOCKED",
                   "evidence": f"TRADE={n_trade}" if scan else "未运行",
                   "blocker": None if p3_ok else "无确认edge或净收益非正→NO_TRADE（符合预期的保守行为）"})

    # Phase 4: paper-trading records exist
    n_paper = len((paper or {}).get("trades", []))
    p4_ok = bool(p3_ok and n_paper >= 1)
    phases.append({"phase": "4 实时模拟交易记录", "status": "PASS" if p4_ok else "BLOCKED",
                   "evidence": f"paper_trades={n_paper}",
                   "blocker": None if p4_ok else "无 TRADE 信号可模拟（上游 BLOCKED）"})

    overall = "PASS" if all(p["status"] == "PASS" for p in phases) else "BLOCKED"
    first_blocker = next((p for p in phases if p["status"] == "BLOCKED"), None)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall": overall, "phases": phases,
            "first_blocking_phase": first_blocker["phase"] if first_blocker else None,
            "note": ("验收为真实数据驱动；任一阶段无真实证据即 BLOCKED，不以模拟数据冒充通过。"
                     "当前若 BLOCKED 于 Phase1，根因是云环境出口网络策略，需放行数据主机后开新会话重跑。")}


def to_markdown(r: dict) -> str:
    L = ["# 验收报告 (Acceptance)", "",
         f"- 生成时间: {r['generated_at']}",
         f"- 总体: **{r['overall']}**",
         f"- 首个阻塞阶段: {r.get('first_blocking_phase') or '无'}", "",
         "| 阶段 | 状态 | 证据 | 阻塞原因 |", "|------|------|------|----------|"]
    for p in r["phases"]:
        L.append(f"| {p['phase']} | {p['status']} | {p['evidence']} | {p.get('blocker') or '-'} |")
    L += ["", f"> {r['note']}"]
    return "\n".join(L)
