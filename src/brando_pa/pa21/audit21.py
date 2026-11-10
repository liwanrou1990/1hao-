"""第二十一阶段上线前审计（只读）：未来数据泄漏（截断重放一致性）、成本模型时点、历史记录覆盖、本金一致性、
Mac 隔离、证据文件历史、报告数字与结果文件一致、测试报告与冻结校验。输出 audit21.json。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..config import PROJECT_ROOT, cfg
from ..data.store import BarStore
from ..trendsys.paper import verify_chain
from . import ACCOUNTS, FORWARD_START, OUT_DIR, RESEARCH_INITIAL_USDT, SEED_DIR
from . import acceptance21, forward21, freeze21

MAC_COMMIT = "5a19020"


def _git(*a) -> str:
    return subprocess.run(["git", "-C", str(PROJECT_ROOT), *a], capture_output=True, text=True).stdout


def leak_truncation(c=None, start="2025-06-01", cuts=("2025-09-15", "2025-12-31", "2026-01-01", "2026-01-02", "2026-04-20", "2026-10-01")) -> dict:
    """用历史日线（已看过的数据，只用于审计）从 start 重放四个账户；在每个截断日只保留截断日及以前的 K 线重新重放，
    截断日及以前的每日记录必须与完整数据重放逐项相同。若任何规则用到了未来 K 线，这里会出现差异。"""
    c = c or cfg(); bars = forward21.load_bars(BarStore(root=SEED_DIR, fallback_root=None)); full = {r["date"]: r for r in forward21.daily_rows(forward21.simulate(bars, c, start), bars)}; res = []
    for cut in cuts:
        t = pd.Timestamp(cut, tz="UTC"); b2 = {s: v[v["ts"] <= t].reset_index(drop=True) for s, v in bars.items()}; rows = forward21.daily_rows(forward21.simulate(b2, c, start, end=cut), b2); mx = 0.0; n = 0
        for r in rows:
            f = full[r["date"]]
            for a in ACCOUNTS:
                for k, v in r["accounts"][a].items():
                    if isinstance(v, (int, float)) and v is not None:
                        mx = max(mx, abs(float(v) - float(f["accounts"][a][k]))); n += 1
        res.append({"cut": cut, "days_compared": len(rows), "values_compared": n, "max_abs_diff": mx})
    return {"ok": all(r["max_abs_diff"] < 1e-9 for r in res), "start": start, "cuts": res, "note": "历史日线只用于审计，不是前瞻记录"}


def impact_volume_timing() -> dict:
    """成本模型时点：成交冲击用成交当日的完整成交额估计（开盘成交时这一数值尚未完整）。不影响任何买卖决策，只影响冲击成本估计。
    量化：把成交额换成前一日成交额重算（只在审计中，冻结规则不改）。"""
    from ..pa20 import phase20 as P20
    from ..pa20.alloc20 import AllocBacktester
    from ..pa20.execution20 import attach, final_liquidation_with_impact
    from ..pa18.variants18 import RecoveryBacktester
    ctx = P20.Ctx(); sh = lambda liq: {s: t.assign(quote_vol=t["quote_vol"].shift(1)) for s, t in liq.items()}; out = []
    for lab, lb, l2 in (("当日成交额（冻结模型）", ctx.liq_btc, ctx.liq_b2), ("前一日成交额（审计替代）", sh(ctx.liq_btc), sh(ctx.liq_b2))):
        r = AllocBacktester(ctx.btc, 0.25, "Y", 10.0, 5.0, "2014-01-01", None, RESEARCH_INITIAL_USDT, lb).run(); row = {"model": lab, "FIX25_Y_final": float(r.final["liquidation_usdt"])}
        bt = attach(P20.HoldingBacktester({"BTCUSDT": ctx.btc}, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, "2014-01-01", None, initial=RESEARCH_INITIAL_USDT, sma=200), lb); x = bt.run()
        row["SMA200_final"] = final_liquidation_with_impact(bt.L, {"BTCUSDT": float(x.nav["closes"].iloc[-1]["BTCUSDT"])}, x.nav["ts"].iloc[-1])
        bb = {k: v for k, v in ctx.b2bars.items() if not k.startswith("_")}
        bt = attach(RecoveryBacktester(bb, {"name": "B2", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, "2017-01-01", None, variant="V0_original", initial=RESEARCH_INITIAL_USDT, risk_params=ctx.c.get("trendsys", {}).get("risk", {})), l2); x = bt.run()
        row["B2_since2017_final"] = final_liquidation_with_impact(bt.L, {s: float(x.nav["closes"].iloc[-1][s]) for s in ("BTCUSDT", "ETHUSDT")}, x.nav["ts"].iloc[-1]); out.append(row)
    d = {k: out[1][k] / out[0][k] - 1 for k in out[0] if k != "model"}
    return {"finding": "成交冲击按成交当日完整成交额估计（轻微的成本估计前视；不影响决策）", "rows": out, "relative_change_if_prior_day": d, "blocking": False,
            "decision": "冻结规则不改；四个账户口径一致；在报告中披露。若要改用前一日成交额，须另行预登记新的观察。"}


def path_sensitivity(fees=(9.0, 10.0, 11.0, 12.0), vols=(0.9, 1.0, 1.1)) -> dict:
    """审计发现（事后，只描述）：在不改规则的前提下，把单边手续费 ±1–2 bps、成交额 ±10% 微调，看各方案历史最终净资产的变化范围。
    原 B2 的风险状态机在阈值附近（如 2017-01-05 回撤恰为 −10.00%）会因极小差异走上完全不同的路径。"""
    from ..pa20 import phase20 as P20
    from ..pa20.alloc20 import AllocBacktester
    from ..pa20.execution20 import attach, final_liquidation_with_impact
    from ..pa18.variants18 import RecoveryBacktester
    ctx = P20.Ctx(); bb = {k: v for k, v in ctx.b2bars.items() if not k.startswith("_")}; sc = lambda liq, k: {s: t.assign(quote_vol=t["quote_vol"] * k) for s, t in liq.items()}; R = {"FIX25_Y（2014 起）": [], "SMA200（2014 起）": [], "原 B2（2017 起）": []}
    for fee in fees:
        for v in vols:
            r = AllocBacktester(ctx.btc, 0.25, "Y", fee, 5.0, "2014-01-01", None, RESEARCH_INITIAL_USDT, sc(ctx.liq_btc, v)).run(); R["FIX25_Y（2014 起）"].append(float(r.final["liquidation_usdt"]))
            bt = attach(P20.HoldingBacktester({"BTCUSDT": ctx.btc}, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, 5.0, "2014-01-01", None, initial=RESEARCH_INITIAL_USDT, sma=200), sc(ctx.liq_btc, v)); x = bt.run()
            R["SMA200（2014 起）"].append(final_liquidation_with_impact(bt.L, {"BTCUSDT": float(x.nav["closes"].iloc[-1]["BTCUSDT"])}, x.nav["ts"].iloc[-1]))
            bt = attach(RecoveryBacktester(bb, {"name": "B2", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, 5.0, "2017-01-01", None, variant="V0_original", initial=RESEARCH_INITIAL_USDT, risk_params=ctx.c.get("trendsys", {}).get("risk", {})), sc(ctx.liq_b2, v)); x = bt.run()
            R["原 B2（2017 起）"].append(final_liquidation_with_impact(bt.L, {s: float(x.nav["closes"].iloc[-1][s]) for s in ("BTCUSDT", "ETHUSDT")}, x.nav["ts"].iloc[-1]))
    out = {k: {"min": float(np.min(v)), "median": float(np.median(v)), "max": float(np.max(v)), "max_over_min": float(np.max(v) / np.min(v)), "n": len(v)} for k, v in R.items()}
    return {"grid": {"fee_bps": list(fees), "volume_multiplier": list(vols), "base_slippage_bps": 5.0}, "final_usdt_range": out, "blocking": False,
            "finding": "原 B2 历史最终净资产对极小的成本差异高度敏感（阈值路径依赖）；FIX25_Y 与 SMA200 稳定。B2 的单一历史数字不可作为稳健结论。",
            "decision": "不修改 B2；前瞻评估时对 B2 另报同样微调下的结果范围（描述性，事后加入，不影响任何规则）"}


def ledger_integrity(out: Path | None = None) -> dict:
    out = Path(out or OUT_DIR); rows = [r for r in acceptance21._read(out / "daily_ledger.jsonl") if r.get("kind") == "daily"]; dates = [r["date"] for r in rows]
    contiguous = all((pd.Timestamp(b) - pd.Timestamp(a)).days == 1 for a, b in zip(dates[:-1], dates[1:]))
    first_ok = (not rows) or (dates[0] == FORWARD_START and all(abs(rows[0]["accounts"][a]["liquidation"] - RESEARCH_INITIAL_USDT) < 1e-6 for a in ACCOUNTS))
    ch = {k: verify_chain(out / f"{k}.jsonl") for k in acceptance21.CHAINS}
    return {"ok": len(set(dates)) == len(dates) and contiguous and first_ok and all(d >= FORWARD_START for d in dates) and all(v["ok"] for v in ch.values()), "n_days": len(rows), "chains": ch,
            "note": "云端当前 0 天（观察起点日线尚未收盘）；追加、去重、不改写与篡改检测由测试在合成数据上验证"}


def capital_consistency() -> dict:
    p20 = PROJECT_ROOT / "docs/brando_pa_validation/phase20_capital100k/phase20_results.json"; p19 = PROJECT_ROOT / "docs/brando_pa_validation/phase20_capital100k/phase19_100k/phase19_results.json"; bad = []
    r20 = json.loads(p20.read_text(encoding="utf-8")); bad += [k for k, v in r20["results"].items() if v["initial_usdt"] != RESEARCH_INITIAL_USDT]
    r19 = json.loads(p19.read_text(encoding="utf-8")); bad += [] if r19.get("initial_usdt") == RESEARCH_INITIAL_USDT else ["phase19_100k"]
    man = json.loads(freeze21.MANIFEST.read_text(encoding="utf-8")); bad += [] if man["params"]["initial_usdt"] == RESEARCH_INITIAL_USDT else ["frozen_manifest"]

    def caps(txt):
        out = []

        def walk(x, p=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{p}.{k}" if p else k)
            elif p.endswith("initial_cash_usdt"):
                out.append([p, x])
        walk(yaml.safe_load(txt)); return out
    now_caps = caps((PROJECT_ROOT / "config/brando_pa.yaml").read_text(encoding="utf-8")); mac_caps = caps(_git("show", f"{MAC_COMMIT}:config/brando_pa.yaml"))
    mac_ok = all(v == 10000 for _, v in now_caps) and {p: v for p, v in mac_caps}.items() <= {p: v for p, v in now_caps}.items()
    return {"ok": not bad and mac_ok, "research_initial_usdt": RESEARCH_INITIAL_USDT, "phase20_results_checked": len(r20["results"]), "mismatches": bad, "mac_config_initial_cash_now": now_caps, "mac_config_initial_cash_at_mac_branch": mac_caps}


def mac_isolation() -> dict:
    cur = yaml.safe_load((PROJECT_ROOT / "config/brando_pa.yaml").read_text(encoding="utf-8")); mac = yaml.safe_load(_git("show", f"{MAC_COMMIT}:config/brando_pa.yaml"))
    same = {k: cur.get(k) == mac.get(k) for k in ("forward", "usdt", "trendsys")}
    writes = [f"{p.name}:{i + 1}" for p in sorted((PROJECT_ROOT / "src/brando_pa/pa21").glob("*.py")) if p.name not in ("macguard21.py", "audit21.py", "sim72.py", "acceptance21.py") for i, l in enumerate(p.read_text(encoding="utf-8").splitlines()) if "phase8_forward" in l or "phase13_forward" in l or "1hao-" in l]
    return {"ok": all(same.values()) and not writes, "config_sections_equal_to_mac_branch": same, "pa21_references_to_mac_dirs": writes, "deploy": "独立克隆目录 + 独立 launchd 标签 + 独立虚拟环境；Mac 运行分支 5a19020 不变"}


def evidence_history() -> dict:
    files = [f for f in _git("ls-files", "docs/brando_pa_validation").split() if f.endswith((".json", ".csv", ".jsonl"))]; mod = []
    for f in files:
        n = len([x for x in _git("log", "--format=%h", "--", f).split() if x])
        if n > 1:
            mod.append({"file": f, "commits": n, "log": _git("log", "--format=%h %s", "--", f).splitlines()[:4]})
    recent = [m for m in mod if any(x in m["file"] for x in ("phase19_longterm", "phase20_capital100k", "phase21_forward"))]
    return {"ok": not recent, "n_files": len(files), "n_modified_after_first_commit": len(mod), "modified_dirs": sorted({str(Path(m["file"]).parts[2]) for m in mod}), "phase19_21_modified": recent,
            "note": "第十九至二十一阶段证据文件提交后均未再修改；更早阶段（第九阶段前瞻日志副本等）按当时说明多次更新快照"}


def docs_consistency() -> dict:
    r20 = json.loads((PROJECT_ROOT / "docs/brando_pa_validation/phase20_capital100k/phase20_results.json").read_text(encoding="utf-8")); s21 = json.loads((PROJECT_ROOT / "docs/brando_pa_validation/phase21_forward/stress21.json").read_text(encoding="utf-8"))
    d43 = next((PROJECT_ROOT / "docs").glob("43_*.md")).read_text(encoding="utf-8"); d44 = next((PROJECT_ROOT / "docs").glob("44_*.md")).read_text(encoding="utf-8"); R = r20["results"]; chk = []
    for k, doc in (("ALLOC_25_Y|full_2014|15", d43), ("BH_BTC|full_2014|15", d43), ("ALLOC_50_Y|full_2014|15", d43), ("B2_original|since_2017|15", d43)):
        m = R[k]; chk.append({"what": k, "final": f"{m['final_usdt']:,.0f}", "mdd": f"−{abs(m['max_drawdown']) * 100:.1f}%", "ok": f"{m['final_usdt']:,.0f}" in doc and f"−{abs(m['max_drawdown']) * 100:.1f}%" in doc})
    rc = {(r["rally_x"], r["crash"]): r for r in s21["rally_then_crash"]}
    for key in ((8, 0.7), (13, 0.8)):
        v = f"−{abs(rc[key]['max_drawdown']) * 100:.1f}%"; chk.append({"what": f"rally {key}", "value": v, "ok": v in d44})
    hw = s21["fix25_hist_max_btc_weight"]; chk.append({"what": "历史最高漂移", "value": f"{hw['weight']:.0%}", "ok": f"{hw['weight']:.0%}" in d44})
    return {"ok": all(x["ok"] for x in chk), "checks": chk}


def test_report() -> dict:
    p = PROJECT_ROOT / "docs/brando_pa_validation/phase21_forward/test_report.txt"; t = p.read_text(encoding="utf-8") if p.exists() else ""; tracked = bool(_git("ls-files", str(p.relative_to(PROJECT_ROOT))).strip())
    return {"ok": "225 passed, 0 failed" in t and tracked, "tracked_in_git": tracked, "line": next((l for l in t.splitlines() if "passed" in l), None), "last_commit": _git("log", "-1", "--format=%h %s", "--", str(p.relative_to(PROJECT_ROOT))).strip()[:80]}


def run(out: Path | None = None) -> dict:
    out = Path(out or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    A = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "git_head": _git("rev-parse", "--short", "HEAD").strip(), "freeze": freeze21.verify(), "no_order_api": acceptance21.static_no_order_api(),
         "leak_truncation": leak_truncation(), "impact_volume_timing": impact_volume_timing(), "path_sensitivity": path_sensitivity(), "ledger_integrity": ledger_integrity(out), "capital_consistency": capital_consistency(), "mac_isolation": mac_isolation(),
         "evidence_history": evidence_history(), "docs_consistency": docs_consistency(), "phase21_test_report": test_report()}
    keys = ("freeze", "no_order_api", "leak_truncation", "ledger_integrity", "capital_consistency", "mac_isolation", "evidence_history", "docs_consistency", "phase21_test_report")
    A["blocking_failures"] = [k for k in keys if not A[k]["ok"]]; A["verdict"] = "无阻断项" if not A["blocking_failures"] else "有阻断项"
    (out / "audit21.json").write_text(json.dumps(A, ensure_ascii=False, indent=1, default=str), encoding="utf-8"); return A


if __name__ == "__main__":
    a = run(); print(json.dumps({"verdict": a["verdict"], "blocking": a["blocking_failures"], **{k: a[k].get("ok") for k in a if isinstance(a[k], dict) and "ok" in a[k]}}, ensure_ascii=False, indent=1))
