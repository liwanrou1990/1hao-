"""前瞻账户费用 / 净值一致性只读审计（2026-11-08）。

只读：只读取 events.jsonl / corrections.jsonl / state.json / runner_events.jsonl / alerts.jsonl / health.json 与行情仓；
不写账户目录中的任何文件（审计结果写到独立的 out_dir）。不触碰策略参数、不重置账户、不启动服务。

审计内容：
  F1 费用公式逐笔复核：BUY 成交价 = 参考价 × (1 + 滑点)，手续费 = 成交价 × 数量 × 费率，现金减少 = 成交额 + 手续费；
     SELL 成交价 = 参考价 × (1 − 滑点)，手续费同上，现金增加 = 成交额 − 手续费；逐笔比对事件里的 fee / execution_price / cash_before→cash_after。
  F2 从事件链重放全部成交到一本新账（SpotLedger），比对 state.json 与最新 nav 的现金、持仓、累计手续费、已实现盈亏（口径一致性）。
  F3 净值恒等式：权益 = 现金 + Σ 持仓 × 标记价；权益 − 初始 = 已实现 + 未实现 − 买入手续费（买入手续费已从现金扣除、不在已实现/未实现中）；
     清仓余额 = 现金 + Σ 持仓 × 标记价 × (1 − 滑点) × (1 − 费率)；清仓余额 − 权益 = **假设**卖出成本（尚未发生）。
  F4 费用明细：买入手续费、卖出手续费、买入滑点成本、卖出滑点成本、报价半价差成本（有 Bid/Ask 的成交）、假设清仓成本（滑点 / 手续费分列）。
  F5 日线信号：同一 signal_ts 决策次数、成交去重、错过信号、过期订单、暂停成交、挂单；成交时间因果（price_time > decision_time，记账 ≥ 执行）。
  F6 72 小时验收分段（复用 acceptance_status，只读）与服务进程 / 单实例锁 / 心跳状态。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..spot.ledger import SpotConstraintError, SpotLedger
from ..trendsys.paper import verify_chain
from ..usdt.engine import liquidation_value
from .acceptance import acceptance_status
from .audit import audit_account

SYMS = ["BTCUSDT", "ETHUSDT"]
TOL = 1e-6


def _events(p: Path) -> list[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()] if p.exists() else []


def check_fill_arithmetic(f: dict, fee_bps: float, slip_bps: float) -> list[dict]:
    """逐笔成交费用算术复核（F1）。返回问题列表（空 = 通过）。"""
    issues = []; side = f["side"]; q = float(f["qty"]); ref = f.get("reference_price"); px = float(f["execution_price"])
    if ref is not None:
        exp_px = float(ref) * (1 + slip_bps / 1e4) if side == "BUY" else float(ref) * (1 - slip_bps / 1e4)
        if abs(exp_px - px) > TOL * max(1.0, px):
            issues.append({"check": "execution_price = reference × (1 ± slippage)", "expected": exp_px, "actual": px})
    exp_fee = px * q * fee_bps / 1e4
    if abs(exp_fee - float(f.get("fee", 0.0))) > TOL * max(1.0, exp_fee):
        issues.append({"check": "fee = execution_price × qty × fee_bps", "expected": exp_fee, "actual": f.get("fee")})
    if "cash_before" in f and "cash_after" in f:
        delta = float(f["cash_after"]) - float(f["cash_before"])
        exp_delta = -(px * q + exp_fee) if side == "BUY" else (px * q - exp_fee)
        if abs(delta - exp_delta) > 1e-6 * max(1.0, abs(exp_delta)):
            issues.append({"check": "cash_after − cash_before = ∓(execution_price × qty) ∓ fee（手续费只扣一次）", "expected": exp_delta, "actual": delta})
    if "position_before" in f and "position_after" in f:
        dq = float(f["position_after"][f["symbol"]]) - float(f["position_before"][f["symbol"]])
        if abs(dq - (q if side == "BUY" else -q)) > 1e-9:
            issues.append({"check": "position_after − position_before = ±qty", "expected": q if side == "BUY" else -q, "actual": dq})
    return issues


def replay_ledger(fills: list[dict], initial: float, fee_bps: float, slip_bps: float) -> SpotLedger:
    """用事件链里的成交（按记录顺序）重放到一本新账；成交参考价取事件 reference_price（无则由 execution_price 反推）。"""
    L = SpotLedger(initial, fee_bps, slip_bps); L.replay_errors = []                      # type: ignore[attr-defined]
    for f in fills:
        q = float(f["qty"]); px = float(f["execution_price"])
        ref = float(f["reference_price"]) if f.get("reference_price") is not None else (px / (1 + slip_bps / 1e4) if f["side"] == "BUY" else px / (1 - slip_bps / 1e4))
        try:
            if f["side"] == "BUY":
                L.buy(f["symbol"], q, ref, f.get("fill_ts") or f.get("execution_time"), f.get("reason", ""), 0.0, 0.0, 0.0)
            else:
                L.sell(f["symbol"], q, ref, f.get("fill_ts") or f.get("execution_time"), f.get("reason", ""))
        except SpotConstraintError as e_:                                                   # 现金不足 / 超卖：事件链与现货约束矛盾（例如重复成交）
            L.replay_errors.append({"order_id": f.get("order_id"), "error": str(e_)})     # type: ignore[attr-defined]
    return L


def fee_breakdown(fills: list[dict], L: SpotLedger, prices: dict, fee_bps: float, slip_bps: float) -> dict:
    """费用明细（F4）：已发生的（买/卖手续费、买/卖滑点、半价差）与假设清仓成本（未发生）。"""
    buy_fee = sum(float(f.get("fee", 0.0)) for f in fills if f["side"] == "BUY"); sell_fee = sum(float(f.get("fee", 0.0)) for f in fills if f["side"] == "SELL")
    buy_slip = sum((float(f["execution_price"]) - float(f["reference_price"])) * float(f["qty"]) for f in fills if f["side"] == "BUY" and f.get("reference_price") is not None)
    sell_slip = sum((float(f["reference_price"]) - float(f["execution_price"])) * float(f["qty"]) for f in fills if f["side"] == "SELL" and f.get("reference_price") is not None)
    half_spread = 0.0; n_quote = 0
    for f in fills:
        qd = f.get("quote") or {}
        if isinstance(qd, dict) and qd.get("bid") is not None and qd.get("ask") is not None and str(f.get("price_kind", "")).startswith("live_quote"):
            mid = (float(qd["bid"]) + float(qd["ask"])) / 2; half_spread += abs(float(f["reference_price"]) - mid) * float(f["qty"]); n_quote += 1
    gross = sum(L.qty(s) * float(prices[s]) for s in SYMS if L.qty(s) > 0)
    liq_slip = gross * slip_bps / 1e4; liq_fee = gross * (1 - slip_bps / 1e4) * fee_bps / 1e4
    return {"incurred": {"buy_fee_usdt": buy_fee, "sell_fee_usdt": sell_fee, "buy_slippage_usdt": buy_slip, "sell_slippage_usdt": sell_slip, "half_spread_usdt_on_live_quotes": half_spread, "n_fills_with_live_quote": n_quote, "total_incurred_usdt": buy_fee + sell_fee + buy_slip + sell_slip},
            "hypothetical_liquidation": {"gross_holdings_usdt": gross, "sell_slippage_usdt": liq_slip, "sell_fee_usdt": liq_fee, "total_usdt": liq_slip + liq_fee, "note": "假设此刻按标记价全部卖出才会发生；尚未发生，不在权益中扣除"}}


def audit_ledger_account(acc_dir: Path, mark_prices: dict | None = None) -> dict:
    ev = _events(acc_dir / "events.jsonl"); st = json.loads((acc_dir / "state.json").read_text()) if (acc_dir / "state.json").exists() else {}
    initial = float(st.get("initial_usdt", 10000.0)); navs = [e for e in ev if e["kind"] == "nav"]; last_nav = navs[-1] if navs else None
    prices = mark_prices or (last_nav.get("prices") if last_nav else None) or {}
    fills_all = [e for e in ev if e["kind"] == "fill"]
    out = {"account": acc_dir.name, "initial_usdt": initial, "fee_bps": st.get("fee_bps"), "slippage_bps": st.get("slippage_bps"), "mark_prices": prices, "mark_source": "argument" if mark_prices else "latest nav event", "as_of_nav_utc": last_nav["ts"] if last_nav else None, "ledgers": {}, "issues": []}
    for k in ("cost", "ideal"):
        fee = 0.0 if k == "ideal" else float(st.get("fee_bps", 10.0)); slip = 0.0 if k == "ideal" else float(st.get("slippage_bps", 5.0))
        fills = [f for f in fills_all if f.get("ledger") == k]
        arith = [{"order_id": f["order_id"], "issues": iss} for f in fills if (iss := check_fill_arithmetic(f, fee, slip))]
        L = replay_ledger(fills, initial, fee, slip)
        s = st.get("ledgers", {}).get(k, {}); state_hold = {sym: sum(float(x["qty"]) for x in s.get("lots", {}).get(sym, [])) for sym in SYMS}
        cmp_state = {"cash": (L.cash, s.get("cash")), "fees_paid": (L.fees_paid, s.get("fees_paid")), "realized": (L.realized, s.get("realized"))} | {f"qty_{sym}": (L.qty(sym), state_hold[sym]) for sym in SYMS}
        mism_state = {n: {"replayed": a, "state": b} for n, (a, b) in cmp_state.items() if b is None or abs(float(a) - float(b)) > 1e-6 * max(1.0, abs(float(a)))}
        nav_l = (last_nav or {}).get("ledgers", {}).get(k, {})
        mism_nav = {}
        if nav_l:
            for n, a, b in [("cash", L.cash, nav_l.get("cash")), ("fees_paid", L.fees_paid, nav_l.get("fees_paid")), ("realized", L.realized, nav_l.get("realized_usdt"))] + [(f"qty_{sym}", L.qty(sym), (nav_l.get("holdings") or {}).get(sym)) for sym in SYMS]:
                if b is None or abs(float(a) - float(b)) > 1e-6 * max(1.0, abs(float(a))):
                    mism_nav[n] = {"replayed": a, "nav": b}
        eq = L.equity(prices) if prices else None; liq = liquidation_value(L, prices) if prices else None
        unreal = L.unrealized(prices) if prices else None; buy_fee = sum(float(f.get("fee", 0.0)) for f in fills if f["side"] == "BUY")
        identity = {}
        if eq is not None:
            identity = {"equity_minus_initial": eq - initial, "realized_plus_unrealized_minus_buy_fee": L.realized + unreal - buy_fee, "ok": abs((eq - initial) - (L.realized + unreal - buy_fee)) < 1e-6 * max(1.0, abs(eq)),
                        "equity_recomputed_vs_nav": (eq, nav_l.get("equity_usdt")) if nav_l and not mark_prices else None, "liquidation_recomputed_vs_nav": (liq, nav_l.get("liquidation_usdt")) if nav_l and not mark_prices else None}
            if nav_l and not mark_prices and (abs(eq - float(nav_l.get("equity_usdt", eq))) > 1e-6 * max(1.0, eq) or abs(liq - float(nav_l.get("liquidation_usdt", liq))) > 1e-6 * max(1.0, liq)):
                mism_nav["equity_or_liquidation"] = {"recomputed": (eq, liq), "nav": (nav_l.get("equity_usdt"), nav_l.get("liquidation_usdt"))}
        fb = fee_breakdown(fills, L, prices, fee, slip) if prices else None
        out["ledgers"][k] = {"n_fills": len(fills), "arithmetic_issues": arith, "state_mismatch": mism_state, "nav_mismatch": mism_nav,
                             "cash_usdt": L.cash, "holdings": {sym: L.qty(sym) for sym in SYMS}, "avg_cost": {sym: L.avg_cost(sym) for sym in SYMS if L.qty(sym) > 0},
                             "equity_usdt": eq, "liquidation_usdt": liq, "liquidation_net_profit_usdt": (liq - initial) if liq is not None else None, "equity_profit_usdt": (eq - initial) if eq is not None else None,
                             "realized_usdt": L.realized, "unrealized_usdt": unreal, "fees_paid_usdt": L.fees_paid, "exit_cost_if_liquidated_usdt": (eq - liq) if eq is not None else None,
                             "identity": identity, "fees": fb,
                             "notes": ["unrealized 以成交价（含滑点）为成本，不含买入手续费；买入手续费已从现金扣除", "liquidation = cash + Σ qty × mark × (1 − slippage) × (1 − fee)：卖出成本只在假设清仓时计一次，买入成本已在成交时扣除，无重复"]}
        if getattr(L, "replay_errors", None):
            out["ledgers"][k]["replay_errors"] = L.replay_errors; out["issues"].append({"ledger": k, "type": "replay_violates_spot_constraints", "detail": L.replay_errors})
        if arith:
            out["issues"].append({"ledger": k, "type": "fee_arithmetic", "n": len(arith)})
        if mism_state:
            out["issues"].append({"ledger": k, "type": "state_vs_replayed_fills", "detail": mism_state})
        if mism_nav:
            out["issues"].append({"ledger": k, "type": "nav_vs_replayed_fills", "detail": mism_nav})
        if identity and not identity.get("ok", True):
            out["issues"].append({"ledger": k, "type": "pnl_identity", "detail": identity})
    # F5 信号 / 订单 / 时间因果（复用时间顺序审计，只读）
    ta = audit_account(acc_dir)
    sigs = [e for e in ev if e["kind"] in ("signal", "missed_signal")]; by_ts = {}
    for e in sigs:
        by_ts.setdefault(e["signal_ts"], []).append(e["kind"])
    dup = {ts: ks for ts, ks in by_ts.items() if len(ks) > 1}
    # 同一 signal_ts 多次决策：只有“产生成交的决策”超过一次才是重复执行；其余若在 state.requeued_after_correction 中登记，属于已纠错的重决策（原订单作废、未成交）
    orders_ev = [e for e in ev if e["kind"] == "order"]; filled_oids = {f["order_id"] for f in fills_all}
    executed_decisions = {}
    for ts_ in dup:
        decs = {str(o.get("decision_time") or o.get("order_simulated_at")) for o in orders_ev if o.get("signal_ts") == ts_ and o["order_id"] in filled_oids}
        executed_decisions[ts_] = sorted(decs)
    requeued = st.get("requeued_after_correction")
    dup_executed = {ts_: d for ts_, d in executed_decisions.items() if len(d) > 1}
    dup_undocumented = {ts_: ks for ts_, ks in dup.items() if not requeued and len(executed_decisions.get(ts_, [])) <= 1}
    kinds = pd.Series([e["kind"] for e in ev]).value_counts().to_dict()
    fills_by_oid = pd.Series([f["order_id"] for f in fills_all]).value_counts() if fills_all else pd.Series(dtype=int)
    causality = []
    for f in fills_all:
        dec = f.get("decision_time"); pt = f.get("price_time"); ex = f.get("execution_time"); rec = f.get("fill_recorded_at") or f.get("recorded_at")
        if dec and pt and not (pd.Timestamp(dec) < pd.Timestamp(pt) <= pd.Timestamp(ex or rec)):
            causality.append({"order_id": f["order_id"], "decision_time": dec, "price_time": pt, "execution_time": ex})
        if ex and rec and pd.Timestamp(rec) < pd.Timestamp(ex):
            causality.append({"order_id": f["order_id"], "recorded_before_execution": (rec, ex)})
    legacy_fills = [f["order_id"] for f in fills_all if "price_kind" not in f]
    out["signals"] = {"event_kinds": kinds, "n_daily_decisions": len(sigs), "duplicate_signal_ts": dup, "n_missed_signal": kinds.get("missed_signal", 0), "n_order_expired": kinds.get("order_expired", 0), "n_fill_paused": kinds.get("fill_paused", 0), "n_order_cancelled": kinds.get("order_cancelled", 0), "n_invalidated_corrections": ta["n_invalidated"],
                      "duplicate_fills_per_order": {k: int(v) for k, v in fills_by_oid[fills_by_oid > 1].items()}, "pending_now": len(st.get("pending", [])), "processed_orders": len(st.get("processed_orders", [])), "last_daily_ts": st.get("last_daily_ts"), "forward_start_utc": st.get("forward_start_utc"),
                      "duplicate_executed_decisions": dup_executed, "duplicate_undocumented": dup_undocumented, "requeued_after_correction": requeued,
                      "duplicate_note": "同一日线多次决策但只有一次产生成交，且已在 state.requeued_after_correction 登记（原订单因 T3 作废、未成交）→ 已纠错的重决策，不是重复执行" if dup and not dup_executed and not dup_undocumented else None,
                      "causality_violations": causality, "legacy_fills_without_price_kind": legacy_fills, "legacy_note": "price_kind 缺失 = 报价模块上线前的成交：参考价为处理时刻最新已收盘 1h 收盘价（非 Bid/Ask），事件中 price_time 已记录" if legacy_fills else None,
                      "timeline_audit_ok": ta["ok"], "timeline_open_violations": ta["open_violations"], "chain": ta["chain"], "corrections_chain": ta["corrections_chain"]}
    if dup_executed:
        out["issues"].append({"type": "duplicate_executed_daily_decision", "detail": dup_executed})
    if dup_undocumented:
        out["issues"].append({"type": "duplicate_daily_decision_undocumented", "detail": dup_undocumented})
    if out["signals"]["duplicate_fills_per_order"]:
        out["issues"].append({"type": "duplicate_fill", "detail": out["signals"]["duplicate_fills_per_order"]})
    if causality:
        out["issues"].append({"type": "fill_causality", "detail": causality})
    if not ta["ok"]:
        out["issues"].append({"type": "timeline_audit", "detail": ta["open_violations"]})
    if not ta["chain"]["ok"]:
        out["issues"].append({"type": "hash_chain_broken", "detail": ta["chain"]})
    out["ok"] = not out["issues"]
    return out


def service_status(log_dir: Path) -> dict:
    """服务进程 / 单实例锁 / 心跳（只读；锁用非阻塞试探后立即释放，不影响运行中的服务）。"""
    lock = log_dir / "service.lock"; held = None
    if lock.exists():
        import fcntl
        fh = open(lock, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fh, fcntl.LOCK_UN); held = False
        except OSError:
            held = True
        finally:
            fh.close()
    try:
        pids = subprocess.run(["pgrep", "-f", "brando_pa.cli forward-service"], capture_output=True, text=True, timeout=5).stdout.split()
    except Exception:  # noqa: BLE001
        pids = None
    pids = [p for p in (pids or []) if p != str(os.getpid())]
    h = json.loads((log_dir / "health.json").read_text()) if (log_dir / "health.json").exists() else None
    hb_age = None
    if h and h.get("heartbeat_utc"):
        hb_age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(h["heartbeat_utc"])).total_seconds()
    return {"lock_file": str(lock), "lock_held_by_running_service": held, "service_pids": pids, "health": {k: h.get(k) for k in ("ok", "heartbeat_utc", "consecutive_failures", "data_fault", "source", "host", "pid") if h and k in h} if h else None, "heartbeat_age_s": hb_age}


def ledger_audit(log_dir: Path, out_dir: Path | None = None, interval_s: int = 600, local_start: str | None = None, mark_prices: dict | None = None, write: bool = True) -> dict:
    log_dir = Path(log_dir); out_dir = Path(out_dir or (log_dir.parent / "forward_audit")); now = pd.Timestamp.now(tz="UTC")
    rep = {"checked_at_utc": str(now), "log_dir": str(log_dir), "read_only": True, "category": "forward（真正前瞻；含成本 = 10 bps 手续费 + 5 bps 滑点）", "accounts": {}, "ok": True}
    for d in sorted(p for p in log_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        a = audit_ledger_account(d, mark_prices); rep["accounts"][d.name] = a; rep["ok"] &= a["ok"]
    try:
        acc = acceptance_status(log_dir, interval_s, local_start=local_start, now=now, write=False)
        rep["acceptance"] = {"status": acc["status"], "local_start": acc.get("local_start"), "segments": {k: {kk: vv for kk, vv in v.items() if kk != "per_day"} for k, v in acc["segments"].items()}}
    except Exception as e_:  # noqa: BLE001
        rep["acceptance"] = {"error": f"{type(e_).__name__}: {e_}"}
    rep["service"] = service_status(log_dir); rep["runner_chain"] = verify_chain(log_dir / "runner_events.jsonl")
    rep["formulas"] = {"buy": "fill = ref × (1 + slip_bps/1e4); fee = fill × qty × fee_bps/1e4; cash −= fill × qty + fee（ref：实时 Ask，否则最新已收盘 1h 收盘价=estimated）",
                       "sell": "fill = ref × (1 − slip_bps/1e4); fee = fill × qty × fee_bps/1e4; cash += fill × qty − fee; realized += (fill − lot_cost) × qty − fee（ref：实时 Bid，否则估算）",
                       "equity": "cash + Σ qty × mark（mark = 最新已收盘 1h 收盘价）", "unrealized": "Σ (mark − lot_fill_price) × qty（lot_fill_price 含买入滑点，不含买入手续费）",
                       "liquidation": "cash + Σ qty × mark × (1 − slip) × (1 − fee)；= equity − 15 bps × 持仓市值（假设卖出成本，未发生）", "identity": "equity − initial = realized + unrealized − buy_fees"}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True); stamp = now.strftime("%Y%m%dT%H%M%SZ")
        atomic_write_json(out_dir / f"ledger_audit_{stamp}.json", rep, indent=1, default=str)
        atomic_write_text(out_dir / f"ledger_audit_{stamp}.md", render_md(rep)); rep["written"] = [str(out_dir / f"ledger_audit_{stamp}.json"), str(out_dir / f"ledger_audit_{stamp}.md")]
    return rep


def _f(x, nd=2):
    return "—" if x is None else f"{x:,.{nd}f}"


def render_md(rep: dict) -> str:
    L = [f"# 前瞻账户费用 / 净值一致性只读审计 · {rep['checked_at_utc'][:19]} UTC · 总体 {'通过' if rep['ok'] else '发现问题'}", "", f"记录目录：`{rep['log_dir']}` · 类别：{rep['category']} · 只读：不改任何账户文件", "",
         "## 1. 三账户含成本账本（cost）", "", "| 账户 | 标记价 BTC/ETH | 现金 | 持仓 BTC | 持仓 ETH | 当前权益 | 预估清仓余额 | 清仓净利润 | 权益口径利润 | 已实现 | 未实现 | 已付手续费 | 假设清仓成本 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        c = a["ledgers"]["cost"]; p = a.get("mark_prices") or {}
        L.append(f"| {n} | {_f(p.get('BTCUSDT'), 1)}/{_f(p.get('ETHUSDT'), 2)} | {_f(c['cash_usdt'])} | {c['holdings']['BTCUSDT']:.8f} | {c['holdings']['ETHUSDT']:.8f} | {_f(c['equity_usdt'])} | {_f(c['liquidation_usdt'])} | {_f(c['liquidation_net_profit_usdt'])} | {_f(c['equity_profit_usdt'])} | {_f(c['realized_usdt'])} | {_f(c['unrealized_usdt'])} | {_f(c['fees_paid_usdt'])} | {_f(c['exit_cost_if_liquidated_usdt'])} |")
    L += ["", "## 2. 费用明细（cost 账本；已发生 vs 假设清仓）", "", "| 账户 | 买入手续费 | 卖出手续费 | 买入滑点 | 卖出滑点 | 半价差（实时报价成交） | 已发生合计 | 假设清仓滑点 | 假设清仓手续费 | 假设清仓合计 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        fb = a["ledgers"]["cost"].get("fees")
        if fb:
            i, h = fb["incurred"], fb["hypothetical_liquidation"]
            L.append(f"| {n} | {_f(i['buy_fee_usdt'])} | {_f(i['sell_fee_usdt'])} | {_f(i['buy_slippage_usdt'])} | {_f(i['sell_slippage_usdt'])} | {_f(i['half_spread_usdt_on_live_quotes'])}（{i['n_fills_with_live_quote']} 笔） | {_f(i['total_incurred_usdt'])} | {_f(h['sell_slippage_usdt'])} | {_f(h['sell_fee_usdt'])} | {_f(h['total_usdt'])} |")
    L += ["", "## 3. 一致性核对", "", "| 账户 | 账本 | 成交数 | 费用算术问题 | state.json 与重放差异 | nav 与重放差异 | 盈亏恒等式 |", "|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        for k, c in a["ledgers"].items():
            idn = c.get("identity") or {}
            L.append(f"| {n} | {k} | {c['n_fills']} | {len(c['arithmetic_issues'])} | {'无' if not c['state_mismatch'] else c['state_mismatch']} | {'无' if not c['nav_mismatch'] else c['nav_mismatch']} | {'通过' if idn.get('ok') else ('—' if not idn else '不通过')} |")
    L += ["", "## 4. 日线信号 / 订单 / 时间因果", "", "| 账户 | 日线决策数 | 同一日线多次决策（其中产生成交的决策数） | 错过信号 | 过期订单 | 暂停成交 | 作废/取消 | 同一订单多次成交 | 因果违规 | 挂单 | last_daily_ts | 旧格式成交（无 price_kind） | 时间审计 | 事件链 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in rep["accounts"].items():
        s = a["signals"]
        L.append(f"| {n} | {s['n_daily_decisions']} | {(str({ts_: len(ks) for ts_, ks in s['duplicate_signal_ts'].items()}) + '（成交决策 ' + str({ts_: len(d) for ts_, d in (s.get('duplicate_executed_decisions') or {}).items()} or '各≤1') + ('；已登记纠错' if s.get('requeued_after_correction') else '') + '）') if s['duplicate_signal_ts'] else '无'} | {s['n_missed_signal']} | {s['n_order_expired']} | {s['n_fill_paused']} | {s['n_order_cancelled']}（纠错 {s['n_invalidated_corrections']}） | {s['duplicate_fills_per_order'] or '无'} | {len(s['causality_violations'])} | {s['pending_now']} | {s['last_daily_ts']} | {len(s['legacy_fills_without_price_kind'])} | {'通过' if s['timeline_audit_ok'] else '违规'} | {'ok' if s['chain']['ok'] else 'BROKEN'} |")
    acc = rep.get("acceptance", {}); L += ["", f"## 5. 72 小时验收（只读复算）· 本地段状态：**{acc.get('status', acc.get('error'))}**", ""]
    for k, sgm in (acc.get("segments") or {}).items():
        if "cycles_observed" in sgm:
            L.append(f"- {k}：{sgm['start_utc'][:16]} → {sgm['end_utc'][:16]}，{sgm['elapsed_hours']} h，轮询 {sgm['cycles_observed']}/{sgm['cycles_expected']}，覆盖率 {sgm['coverage']:.1%}，最长中断 {sgm['longest_gap_hours']} h，主机 {sgm['hosts']}，错过 {sgm['missed_signals']}，过期 {sgm['expired_orders']}，暂停 {sgm['fill_paused']}，成交 {sgm['fills']}")
        else:
            L.append(f"- {k}：{sgm.get('status')} {sgm.get('note', '')}")
    sv = rep.get("service", {}); L += ["", "## 6. 服务进程", "", f"- 单实例锁被运行中的服务持有：{sv.get('lock_held_by_running_service')}；forward-service 进程：{sv.get('service_pids')}；心跳年龄 {_f(sv.get('heartbeat_age_s'), 0)} s；health：{sv.get('health')}", f"- runner_events 链：{rep.get('runner_chain')}"]
    L += ["", "## 7. 公式", ""] + [f"- {k}：{v}" for k, v in rep["formulas"].items()]
    L += ["", "## 8. 问题清单", ""] + ([f"- {n}: {json.dumps(i, ensure_ascii=False, default=str)}" for n, a in rep["accounts"].items() for i in a["issues"]] or ["- 无"])
    L += ["", "真实交易 API 禁用；本审计未修改任何账户、事件链、行情或策略参数。"]
    return "\n".join(L)
