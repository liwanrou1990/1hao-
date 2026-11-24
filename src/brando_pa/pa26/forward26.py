"""第二十六阶段收尾：冻结 A（第十一阶段 V4 主配置，代码与参数不改），只用冻结之后的新日线做前瞻模拟。
每次运行：校验冻结指纹 → 取 OKX 现货已收盘日线 → 用全部历史作预热、从 FORWARD_START 起以 100,000 USDT 重放 →
新的一天追加进只追加哈希链 daily.jsonl（已记录的日子不改写；重放不一致只记 replay_mismatch 事件）→ 写 summary.json。
记录：信号（接受 / 拒绝及原因）、模拟成交、扣成本后净 USDT（清仓价值）、最大回撤。只读，无交易接口。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..pa11.engine11 import SYMS, Config
from ..trendsys.paper import _append, verify_chain
from . import ab26 as M

FORWARD_START = "2026-11-24"
FWD_DIR = PROJECT_ROOT / "docs" / "brando_pa_validation" / "phase26_pullback_ab" / "forward"
FROZEN_FILES = ["src/brando_pa/pa11/signals.py", "src/brando_pa/pa11/engine11.py", "src/brando_pa/pa11/structure.py", "src/brando_pa/pa11/zones.py", "src/brando_pa/pa11/macro.py", "src/brando_pa/pa26/ab26.py"]
SCOPES = {"BTC": ("BTCUSDT",), "ETH": ("ETHUSDT",), "BTC+ETH": tuple(SYMS)}
HIST_CHECK_END = M.DATA_END


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def spec() -> dict:
    return {"rule": "A = 第十一阶段 V4 主配置（趋势回调 + 突破回踩，退出 B），B/C 已停用", "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in M.MAIN_CFG.items()}, "fee_bps": M.FEE_BPS, "slip_bps": M.SLIP_BPS, "initial_usdt": M.INITIAL,
            "forward_start": FORWARD_START, "data": "OKX 现货 history-candles 1Dutc（只用已收盘日）", "fill": "信号日收盘决策，下一根日线开盘成交", "scopes": {k: list(v) for k, v in SCOPES.items()}}


def freeze(fwd_dir: Path = FWD_DIR) -> dict:
    fwd_dir.mkdir(parents=True, exist_ok=True); man = {"files": {f: _sha(PROJECT_ROOT / f) for f in FROZEN_FILES}, "spec": spec(), "spec_sha256": hashlib.sha256(json.dumps(spec(), sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "frozen_at_utc": str(pd.Timestamp.now(tz="UTC"))}
    (fwd_dir / "frozen_manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8"); return man


def verify_freeze(fwd_dir: Path = FWD_DIR) -> dict:
    man = json.loads((fwd_dir / "frozen_manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in man["files"].items() if _sha(PROJECT_ROOT / f) != h]
    spec_ok = hashlib.sha256(json.dumps(spec(), sort_keys=True, ensure_ascii=False).encode()).hexdigest() == man["spec_sha256"]
    return {"ok": not bad and spec_ok, "changed_files": bad, "spec_ok": spec_ok}


def fetch_bars(last_closed: pd.Timestamp) -> dict:
    from ..holding.data import okx_daily
    out = {}
    for s in SYMS:
        d = okx_daily(s, pd.Timestamp(M.DATA_START, tz="UTC"), last_closed).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
        assert len(pd.date_range(d.ts.min(), d.ts.max(), freq="D")) == len(d), f"{s} 日线有缺天"
        out[s] = d[["ts", "open", "high", "low", "close", "volume"]]
    return out


def history_check(bars: dict) -> dict:
    """冻结时的历史数据（至 2026-11-23）与本次下载是否一致（OKX 偶有修订，只记录不改用）。"""
    old = M.load(); res = {}
    for s in SYMS:
        m = bars[s].merge(old[s], on="ts", suffixes=("", "_old")); m = m[m.ts <= pd.Timestamp(HIST_CHECK_END, tz="UTC")]
        diff = {k: int((~np.isclose(m[k], m[k + "_old"], rtol=1e-9)).sum()) for k in ("open", "high", "low", "close", "volume")}
        res[s] = {"days": len(m), "mismatch_counts": diff}
    return res


def _day_record(r, scope: str, day: pd.Timestamp) -> dict:
    nav = r.nav.copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True); row = nav[nav.ts == day].iloc[0]; upto = nav[nav.ts <= day]
    dec = r.decisions.copy(); dec["ts"] = pd.to_datetime(dec["ts"], utc=True) if len(dec) else dec.get("ts")
    sig = r.signals.copy(); fil = r.fills.copy()
    for x in (sig, fil):
        if len(x):
            x["ts"] = pd.to_datetime(x["ts"], utc=True)
    rd = lambda v: round(float(v), 6)
    return {"kind": "day", "scope": scope, "date": str(day.date()), "liquidation_usdt": rd(row["liquidation"]), "equity_usdt": rd(row["equity"]), "cash_usdt": rd(row["cash"]), "net_usdt": rd(row["liquidation"] - M.INITIAL),
            "max_drawdown_to_date": rd((upto["liquidation"] / upto["liquidation"].cummax() - 1).min()), "holdings": {k: rd(v) for k, v in row["holdings"].items()},
            "decisions": [{"symbol": x["symbol"], "action": x["action"], "reason": x["reason"]} for x in dec[dec.ts == day].to_dict("records")] if len(dec) else [],
            "signals": [{"symbol": x["symbol"], "strategy": x["strategy"], "accepted": bool(x["accepted"]), "reason": str(x.get("reason", "")), "invalidation": x.get("invalidation"), "target_1": x.get("target_1")} for x in sig[sig.ts == day].to_dict("records")] if len(sig) else [],
            "fills": [{"symbol": x["symbol"], "side": x["side"], "qty": rd(x["qty"]), "fill_price": rd(x["fill_price"]), "fee": rd(x["fee"]), "reason": x["reason"]} for x in fil[fil.ts == day].to_dict("records")] if len(fil) else []}


def _core(rec: dict) -> dict:
    return {k: rec[k] for k in ("scope", "date", "liquidation_usdt", "cash_usdt", "holdings", "decisions", "signals", "fills")}


def run(now: pd.Timestamp | None = None, fwd_dir: Path = FWD_DIR) -> dict:
    now = now or pd.Timestamp.now(tz="UTC"); daily = fwd_dir / "daily.jsonl"; events = fwd_dir / "events.jsonl"
    fz = verify_freeze(fwd_dir)
    if not fz["ok"]:
        _append(events, {"kind": "frozen_rule_violation", "detail": fz}); return {"status": "冻结规则被改动，停止", "detail": fz}
    last_closed = (now - pd.Timedelta(days=1)).floor("D")
    if last_closed < pd.Timestamp(FORWARD_START, tz="UTC"):
        return {"status": f"等待首根新日线（{FORWARD_START} UTC 收盘后）", "last_closed": str(last_closed.date())}
    try:
        bars = fetch_bars(last_closed)
    except Exception as e:  # noqa: BLE001
        _append(events, {"kind": "data_unavailable", "detail": f"{type(e).__name__}: {e}"}); return {"status": "数据不可用", "detail": str(e)}
    hc = history_check(bars); ctx = M.MarketContext(bars)
    logged = {}
    if daily.exists():
        for line in open(daily, encoding="utf-8"):
            if line.strip():
                x = json.loads(line); logged[(x["scope"], x["date"])] = x
    summary = {"as_of_day": str(bars["BTCUSDT"].ts.max().date()), "run_at_utc": str(now), "freeze": fz, "history_check": hc, "scopes": {}}; n_new = 0
    for name, allowed in SCOPES.items():
        r = M.SymbolBacktester(bars, Config(**M.MAIN_CFG, name=f"A_fwd_{name}"), M.FEE_BPS, M.SLIP_BPS, FORWARD_START, str(last_closed.date()), initial=M.INITIAL, ctx=ctx, allowed=allowed).run()
        for day in pd.to_datetime(r.nav["ts"], utc=True):
            rec = _day_record(r, name, day); key = (name, rec["date"])
            if key in logged:
                if json.dumps(_core(logged[key]), sort_keys=True, default=str) != json.dumps(_core(rec), sort_keys=True, default=str):
                    _append(events, {"kind": "replay_mismatch", "scope": name, "date": rec["date"], "note": "已记录的历史不改写", "replayed": _core(rec)})
                continue
            lag_h = (now - (day + pd.Timedelta(days=1))).total_seconds() / 3600
            rec["record_class"] = "实时记录" if lag_h <= 36 else "补记"; rec["lag_hours"] = round(lag_h, 2); _append(daily, rec); n_new += 1
        nav = r.nav; sig = r.signals
        summary["scopes"][name] = {"net_usdt": float(r.final["liquidation_usdt"] - M.INITIAL), "liquidation_usdt": float(r.final["liquidation_usdt"]), "max_drawdown": M.max_dd(nav["liquidation"]), "days": len(nav),
                                   "n_signals": int(len(sig)), "n_signals_accepted": int(sig.accepted.astype(bool).sum()) if len(sig) else 0, "rejection_reasons": (sig[~sig.accepted.astype(bool)].reason.astype(str).str.split("; ").str[-1].value_counts().to_dict() if len(sig) else {}),
                                   "n_fills": int(len(r.fills)), "open_positions": int(r.final["open_positions"]), "fees_usdt": float(r.final["fees_usdt"])}
    summary["new_day_records"] = n_new; summary["chains"] = {"daily": verify_chain(daily), "events": verify_chain(events)}; summary["status"] = "ok"
    (fwd_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    s = run(); c = s.get("scopes", {}).get("BTC+ETH")
    print(json.dumps({"status": s["status"], "as_of_day": s.get("as_of_day"), "new_day_records": s.get("new_day_records"), "BTC+ETH": c, "chains_ok": all(v.get("ok") for v in s.get("chains", {}).values()) if s.get("chains") else None}, ensure_ascii=False, default=str))
