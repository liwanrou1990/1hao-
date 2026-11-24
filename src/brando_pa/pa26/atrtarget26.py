"""第二十六阶段第三轮（预登记 docs/brando_pa_validation/phase26_pullback_ab/preregistration_c.md）：
A = 现行规则；C = 入场时拟入场价上方没有任何已确认阻力区 → T1 = 拟入场价 + 3 × ATR14；其余（含净盈亏比 ≥ 1.5 检查）不变。
另含诊断：第二轮 9 条“突破回踩被 1.5R 拒绝”拆成“无已确认阻力 / 有阻力但不足 1.5R”。只读，无交易接口。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..pa11.engine11 import SYMS, Config
from ..pa11.signals import EntryRiskRewardEvaluator
from ..pa11.zones import SupportResistanceZones
from . import ab26 as M
from . import handoff26 as H

ATR_MULT = 3.0
WINDOWS = H.WINDOWS


class ATRTargetEvaluator(EntryRiskRewardEvaluator):
    """上方无任何已确认阻力区时，T1 = 入场 + 3 ATR；否则与 A 完全相同。"""
    atr_now: float = float("nan")

    def plan(self, entry_ref, invalidation, zones, st, cost_one_side, entry_zone, reason):
        if SupportResistanceZones.resistances_above(zones, entry_ref) or not np.isfinite(self.atr_now):
            return super().plan(entry_ref, invalidation, zones, st, cost_one_side, entry_zone, reason) | {"t1_basis": "已确认阻力区"}
        risk = entry_ref - invalidation
        if risk <= 0:
            return {"ok": False, "reason": f"{reason}; 失效价不低于入场价 → WAIT"}
        t1 = entry_ref + ATR_MULT * self.atr_now
        if st.get("last_high") and st.get("prev_low") is not None and st["last_high"] > st["prev_low"] and entry_ref + (st["last_high"] - st["prev_low"]) > t1:
            t2 = entry_ref + (st["last_high"] - st["prev_low"]); t2_basis = "测量目标：最近已确认摆动低→高幅度"
        else:
            t2 = None; t2_basis = "无"
        c = cost_one_side; net_gain = (t1 - entry_ref) - 2 * c * entry_ref; net_risk = risk + 2 * c * entry_ref; rr = net_gain / net_risk; ok = rr >= self.min_rr
        return {"ok": ok, "entry_zone": [float(entry_zone[0]), float(entry_zone[1])], "entry_ref": float(entry_ref), "invalidation": float(invalidation), "target_1": float(t1), "target_1_zone": [float(t1), float(t1)], "target_1_strength": 0,
                "target_2": float(t2) if t2 else None, "target_2_basis": t2_basis, "expected_net_return": float(net_gain / entry_ref), "expected_net_reward_risk": float(rr), "risk_per_unit": float(risk), "cost_one_side": c, "t1_basis": f"上方无已确认阻力：入场 + {ATR_MULT:g} ATR",
                "reason": reason + f"; 上方无已确认阻力，T1 = 入场 + {ATR_MULT:g} ATR = {t1:.0f}" + ("" if ok else f"; 净盈亏比 {rr:.2f} < {self.min_rr} → WAIT")}


class ATRBacktester(M.SymbolBacktester):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.rr = ATRTargetEvaluator(self.cfg.min_rr)

    def _decide_entry(self, s, i, eq, cl):
        self.rr.atr_now = float(self.d[s].atr[i]); super()._decide_entry(s, i, eq, cl)


def run_bt(bars, ctx, variant: str, allowed: tuple, start: str, end: str):
    cls = ATRBacktester if variant == "C" else M.SymbolBacktester
    r = cls(bars, Config(**M.MAIN_CFG, name=variant), M.FEE_BPS, M.SLIP_BPS, start, end, initial=M.INITIAL, ctx=ctx, allowed=allowed).run()
    return r


def positions(r) -> pd.DataFrame:
    t = r.trades
    if not len(t):
        return pd.DataFrame(columns=["symbol", "entry_ts", "strategy", "entry_price", "exit_ts", "net_pnl", "reasons"])
    t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    return t.groupby(["symbol", "entry_ts"]).agg(strategy=("strategy", "first"), entry_price=("entry_price", "first"), exit_ts=("exit_ts", "max"), net_pnl=("net_pnl", "sum"), reasons=("reason", lambda x: " + ".join(x))).reset_index()


def split9(ctxs: dict) -> pd.DataFrame:
    """第二轮 9 条“突破回踩触发但被 1.5R 拒绝”：在第二轮 B 的主区间组合回测里找到被拒的那一天，列出当时上方全部已确认阻力。"""
    rec = pd.read_csv(M.OUT_DIR / "handoff" / "reclassified.csv"); rec = rec[rec.B_reason.str.startswith("突破回踩触发但被拒")]
    rb = run_bt(M.load(), ctxs["B"], "B", tuple(SYMS), *WINDOWS["主区间"]); sig = rb.signals.copy(); sig["ts"] = pd.to_datetime(sig["ts"], utc=True); cb = ctxs["B"]; rows = []
    for _, r in rec.iterrows():
        s = r.symbol; i = int(r.signal_idx); w = sig[(sig.symbol == s) & (sig.strategy == "B") & (~sig.accepted.astype(bool)) & (sig.ts >= cb.idx[i]) & (sig.ts <= cb.idx[min(cb.n - 1, i + H.FOLLOW_DAYS)])]
        x = w.iloc[0]; j = int(np.searchsorted(cb.idx, x.ts)); entry = float(cb.close[s][j]); inval = float(cb.det_res[s]["B"][j]["invalidation"]); risk = entry - inval; a = float(cb.d[s].atr[j])     # 计划被拒时引擎不记录失效价，取检测器当日结果
        res = SupportResistanceZones.resistances_above(cb.zones[s][j], entry)
        lst = [{"low": float(z.low), "high": float(z.high), "strength": int(z.strength), "R": float((z.low - entry) / risk)} for z in res]
        gross_ok = [z for z in lst if z["R"] >= 1.5]
        cat = "无已确认阻力" if not lst else ("有阻力但不足 1.5R" if not gross_ok else "有 ≥1.5R 阻力但扣成本后净盈亏比 < 1.5")
        rows.append({"symbol": s, "reclassified_date": r.signal_date, "rejected_day": str(x.ts.date()), "entry_close": entry, "invalidation": inval, "risk_pct": risk / entry, "risk_atr": risk / a, "atr14": a,
                     "need_1_5R_at": entry + 1.5 * risk, "n_resistances_above": len(lst), "resistances_above": lst, "category": cat, "engine_reason": str(x.reason).split("; ")[-1], "seen_in_prior_audit": bool(r.seen_in_prior_audit),
                     "if_3ATR_target_R": (3 * a) / risk})
    return pd.DataFrame(rows)


def judge(rows: list[dict], new_main: pd.DataFrame, ex_top_net: float) -> dict:
    g = {(x["variant"], x["scope"], x["window"]): x for x in rows}; a = g[("A", "BTC+ETH", "主区间")]; c = g[("C", "BTC+ETH", "主区间")]
    k = {"c1_main_net_higher": c["net_usdt"] > a["net_usdt"], "c2_main_dd_not_worse_2pp": c["max_drawdown"] >= a["max_drawdown"] - 0.02, "c3_still_higher_ex_top": ex_top_net > a["net_usdt"], "c4_new_entries_ge_5": len(new_main) >= 5}
    return {kk: bool(v) for kk, v in k.items()} | {"n_new_entries_main_combo": len(new_main), "verdict": "进入前瞻影子观察（不采用、不改生产）" if all(k.values()) else "证据不足：保留 A"}


def run(out_dir: Path | None = None) -> dict:
    out = Path(out_dir or M.OUT_DIR) / "atr_target"; out.mkdir(parents=True, exist_ok=True)
    bars = M.load(); ctxs = H.contexts(bars); ctx = ctxs["A"]
    sp = split9(ctxs); sp.to_csv(out / "split9.csv", index=False)
    rows = []; new_all = []; lost_all = []; ex_top = {}
    for w, (a, b) in WINDOWS.items():
        for allowed in (("BTCUSDT",), ("ETHUSDT",), tuple(SYMS)):
            ra = run_bt(bars, ctx, "A", allowed, a, b); rc = run_bt(bars, ctx, "C", allowed, a, b); scope = "+".join(x.replace("USDT", "") for x in allowed)
            pa, pc = positions(ra), positions(rc); key = ["symbol", "entry_ts"]
            new = pc.merge(pa[key], on=key, how="left", indicator=True); new = new[new._merge == "left_only"].drop(columns="_merge")
            lost = pa.merge(pc[key], on=key, how="left", indicator=True); lost = lost[lost._merge == "left_only"].drop(columns="_merge")
            if len(new):
                sigc = rc.signals.copy(); sigc["ts"] = pd.to_datetime(sigc["ts"], utc=True)
                basis = []
                for _, n in new.iterrows():
                    m = sigc[(sigc.symbol == n.symbol) & sigc.accepted.astype(bool) & (sigc.ts < n.entry_ts)].tail(1)
                    basis.append((str(m.t1_basis.iloc[0]) if "t1_basis" in m and len(m) else "?", float(m.target_1.iloc[0]) if len(m) else None, float(m.invalidation.iloc[0]) if len(m) else None, str(m.ts.iloc[0].date()) if len(m) else None))
                new = new.assign(t1_basis=[x[0] for x in basis], target_1=[x[1] for x in basis], invalidation=[x[2] for x in basis], signal_day=[x[3] for x in basis])
            top = float(new.net_pnl.max()) if len(new) and new.net_pnl.max() > 0 else 0.0
            for v, r in (("A", ra), ("C", rc)):
                nav = r.nav; rows.append({"variant": v, "scope": scope, "window": w, "net_usdt": float(r.final["liquidation_usdt"] - M.INITIAL), "max_drawdown": M.max_dd(nav["liquidation"]), "n_entries": int(len(positions(r))), "identity_ok": bool(r.final["identity_ok"])})
            ex_top[(scope, w)] = rows[-1]["net_usdt"] - top
            rows[-1]["net_usdt_ex_top_new_trade"] = ex_top[(scope, w)]; rows[-1]["n_new_vs_A"] = len(new); rows[-1]["n_A_entries_lost"] = len(lost)
            new_all += [x | {"scope": scope, "window": w} for x in new.to_dict("records")]; lost_all += [x | {"scope": scope, "window": w} for x in lost.to_dict("records")]
    newdf = pd.DataFrame(new_all); main_new = newdf[(newdf.scope == "BTC+ETH") & (newdf.window == "主区间")] if len(newdf) else newdf
    R = {"atr_mult": ATR_MULT, "windows": WINDOWS, "split9": sp.to_dict("records"), "results": rows, "new_entries": new_all, "A_entries_lost_in_C": lost_all,
         "judgement": judge(rows, main_new, ex_top[("BTC+ETH", "主区间")]), "generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史诊断（全部已看过，非盲测）", "real_trading": "disabled (no implementation)"}
    (out / "atr_target_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.DataFrame(rows).to_csv(out / "results.csv", index=False); newdf.to_csv(out / "new_entries.csv", index=False); pd.DataFrame(lost_all).to_csv(out / "A_entries_lost_in_C.csv", index=False)
    return R


if __name__ == "__main__":
    print(run()["judgement"])
