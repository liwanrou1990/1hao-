"""第二十六阶段第二轮（预登记 docs/brando_pa_validation/phase26_pullback_ab/preregistration_b2.md）：
A = 现行规则；B = 回调候选若在本轮当时已确认前高 H 之后出现收盘 > H，则退出回调流程，交给原突破回踩规则独立判断（不直接入场）。
其余全部不变。只读，无交易接口。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..pa11.engine11 import SYMS, MarketContext
from ..pa11.signals import PullbackConfirmation
from ..pa11.structure import DailyStructureDetector, structure_state
from ..pa11.zones import SupportResistanceZones
from . import ab26 as M

WINDOWS = {"主区间": ("2018-07-30", M.DATA_END), "样本内": ("2018-07-30", "2023-12-31"), "2024年以后": ("2024-01-01", M.DATA_END)}
FOLLOW_DAYS = 25
SEEN = {("BTCUSDT", "2025-07-09"), ("BTCUSDT", "2025-07-22"), ("BTCUSDT", "2025-09-10"), ("ETHUSDT", "2024-03-11")}


class PullbackWithHandoff(PullbackConfirmation):
    """与 A 相同；但本轮前高 H 之后若已有收盘 > H，退出回调流程（不产生回调信号）。"""

    def check(self, d: DailyStructureDetector, zones_obj: SupportResistanceZones, i: int, zones: list) -> dict:
        st = structure_state(d.sw, i)
        if st["last_high_idx"] is not None and st["last_high_idx"] < i:
            hi = st["last_high_idx"]; H = st["last_high"]; mx = float(np.max(d.close[hi + 1:i + 1]))
            if mx > H:
                return {"signal": False, "handoff": True, "H": float(H), "reason": f"本轮已有收盘 {mx:.0f} > 当时已确认前高 {H:.0f}：退出回调流程，转交突破回踩"}
        return super().check(d, zones_obj, i, zones)


def contexts(bars: dict) -> dict:
    ca = MarketContext(bars); cb = copy.copy(ca); cb.det_res = {s: dict(ca.det_res[s]) for s in SYMS}; det = PullbackWithHandoff()
    for s in SYMS:
        cb.det_res[s]["A"] = [det.check(ca.d[s], ca.z[s], i, ca.zones[s][i]) if i >= 30 else {"signal": False, "reason": "数据不足"} for i in range(ca.n)]
    return {"A": ca, "B": cb}


def prereq(ctx, s, i) -> bool:
    return ctx.wt[s][i]["trend"] == "UP" and ctx.ds[s][i]["sma_filter_ok"]


def reclassified(ctxs: dict) -> pd.DataFrame:
    ca, cb = ctxs["A"], ctxs["B"]; rows = []
    for s in SYMS:
        seen_hi = set()
        for i in range(ca.n):
            ra = ca.det_res[s]["A"][i]; rb = cb.det_res[s]["A"][i]
            if not (ra["signal"] and prereq(ca, s, i) and rb.get("handoff")):
                continue
            hi = ca.st[s][i]["last_high_idx"]
            if hi in seen_hi:
                continue
            seen_hi.add(hi); d = str(ca.idx[i].date())
            rows.append({"symbol": s, "signal_idx": i, "signal_date": d, "close": float(ca.close[s][i]), "H": float(ca.st[s][i]["last_high"]), "H_date": str(ca.idx[hi].date()), "close_over_H": float(ca.close[s][i] / ca.st[s][i]["last_high"] - 1),
                         "invalidation_A": float(ra["invalidation"]), "seen_in_prior_audit": (s, d) in SEEN})
    return pd.DataFrame(rows)


def follow_up(rec: pd.DataFrame, ctxs: dict, runs: dict) -> pd.DataFrame:
    """在主区间组合回测里追踪每条改分类记录的去向。"""
    cb = ctxs["B"]; rA = runs["A"]; rB = runs["B"]; out = []
    sigA = rA.signals.copy() if len(rA.signals) else pd.DataFrame(columns=["ts", "symbol", "strategy", "accepted", "reason"]); sigB = rB.signals.copy() if len(rB.signals) else sigA.iloc[0:0]
    for x in (sigA, sigB):
        if len(x):
            x["ts"] = pd.to_datetime(x["ts"], utc=True)
    fB = rB.fills.copy(); fB["ts"] = pd.to_datetime(fB["ts"], utc=True); tB = rB.trades.copy(); tA = rA.trades.copy()
    for t in (tA, tB):
        if len(t):
            t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    nav = rB.nav.set_index(pd.to_datetime(rB.nav["ts"], utc=True))
    for _, r in rec.iterrows():
        s = r.symbol; i = int(r.signal_idx); t0 = cb.idx[i]; t1 = cb.idx[min(cb.n - 1, i + FOLLOW_DAYS)]
        a_sig = sigA[(sigA.ts == t0) & (sigA.symbol == s) & (sigA.strategy == "A")] if len(sigA) else sigA
        a_trade = tA[(tA.symbol == s) & (tA.entry_ts == cb.idx[i + 1])] if len(tA) and i + 1 < cb.n else tA.iloc[0:0]
        a_status = ("A 当时入场" if len(a_sig) and bool(a_sig.accepted.iloc[0]) else ("A 当时也被拒：" + str(a_sig.reason.iloc[0]).split("; ")[-1] if len(a_sig) else "A 当时未评估（已有持仓或不在区间内）"))
        a_pnl = float(a_trade.net_pnl.sum()) if len(a_trade) else None
        buys = fB[(fB.symbol == s) & (fB.side == "BUY") & (fB.ts > t0) & (fB.ts <= cb.idx[min(cb.n - 1, i + FOLLOW_DAYS + 1)])]
        held = bool(nav.loc[t0, "holdings"].get(s, 0) > 1e-12) if t0 in nav.index else False
        if len(buys):
            b = buys.iloc[0]; tr = tB[(tB.symbol == s) & (tB.entry_ts == b.ts)]
            status = "确认（突破回踩入场）" if b.reason == "B" else f"确认（{b.reason} 入场）"; why = f"{b.ts.date()} 开盘 {b.fill_price:.2f} 入场"; pnl = float(tr.net_pnl.sum()) if len(tr) else None
        else:
            pnl = None; rej = sigB[(sigB.symbol == s) & (sigB.ts >= t0) & (sigB.ts <= t1) & (sigB.strategy == "B") & (~sigB.accepted.astype(bool))] if len(sigB) else sigB
            det_days = [j for j in range(i, min(cb.n, i + FOLLOW_DAYS + 1)) if cb.det_res[s]["B"][j]["signal"]]
            if held:
                status, why = "淘汰", "当时已有该币持仓"
            elif len(rej):
                status, why = "淘汰", "突破回踩触发但被拒：" + str(rej.reason.iloc[0]).split("; ")[-1]
            elif det_days and not any(prereq(cb, s, j) for j in det_days):
                status, why = "淘汰", "突破回踩触发但前置条件不满足"
            else:
                status, why = "淘汰", f"{FOLLOW_DAYS} 根日线内突破回踩未触发"
        out.append(r.to_dict() | {"A_status": a_status, "A_trade_net_pnl": a_pnl, "B_final": status, "B_reason": why, "B_trade_net_pnl": pnl})
    return pd.DataFrame(out)


def judge(rows: list[dict], rec: pd.DataFrame) -> dict:
    g = {(x["variant"], x["scope"], x["window"]): x for x in rows}
    ma, mb = g[("A", "BTC+ETH", "主区间")], g[("B", "BTC+ETH", "主区间")]; ia, ib = g[("A", "BTC+ETH", "样本内")], g[("B", "BTC+ETH", "样本内")]
    n_unseen = int((~rec.seen_in_prior_audit).sum()) if len(rec) else 0
    c = {"c1_main_net_higher": mb["net_usdt"] > ma["net_usdt"], "c2_main_dd_not_worse_2pp": mb["max_drawdown"] >= ma["max_drawdown"] - 0.02, "c3_in_sample_not_worse": ib["net_usdt"] >= ia["net_usdt"], "c4_unseen_reclassified_ge_10": n_unseen >= 10}
    return {k: bool(v) for k, v in c.items()} | {"n_reclassified": len(rec), "n_reclassified_unseen": n_unseen, "verdict": "B 优于 A（按事先规定）" if all(c.values()) else "证据不足：保留 A，继续前瞻模拟"}


def run(out_dir: Path | None = None) -> dict:
    out = Path(out_dir or M.OUT_DIR) / "handoff"; out.mkdir(parents=True, exist_ok=True)
    bars = M.load(); ctxs = contexts(bars); rec = reclassified(ctxs)
    rows = []; runs_main = {}; trades = []
    for w, (a, b) in WINDOWS.items():
        for v in ("A", "B"):
            for allowed in (("BTCUSDT",), ("ETHUSDT",), tuple(SYMS)):
                x = M.backtest(bars, ctxs[v], v, allowed, a, b); x["row"]["window"] = w; rows.append(x["row"])
                if w == "主区间" and len(allowed) == 2:
                    runs_main[v] = x["res"]
                if len(x["res"].trades):
                    trades += [t | {"variant": v, "scope": x["row"]["scope"], "window": w} for t in x["res"].trades.to_dict("records")]
    fu = follow_up(rec, ctxs, runs_main)
    R = {"windows": WINDOWS, "follow_days": FOLLOW_DAYS, "results": rows, "reclassified": fu.to_dict("records"), "judgement": judge(rows, fu),
         "data_manifest": json.loads((M.DATA_DIR / "manifest.json").read_text()), "generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史研究（非盲测；2024 年后含已看过的 4 条，仅作诊断）", "real_trading": "disabled (no implementation)"}
    (out / "handoff_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    fu.to_csv(out / "reclassified.csv", index=False); pd.DataFrame(rows).to_csv(out / "results.csv", index=False); pd.DataFrame(trades).to_csv(out / "trades.csv", index=False)
    return R


if __name__ == "__main__":
    print(run()["judgement"])
