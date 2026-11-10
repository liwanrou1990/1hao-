"""第十五阶段实验运行器（预登记 docs/brando_pa_validation/phase15_onchain/preregistration.md）。
基准 + 单因子 + 组合因子 × 窗口（train / valid / oos / full）× 成本；选参只用训练段，验证段确认，样本外只评估一次；网格全部样本外结果公开但不用于选择。
只写 reports_out/brando_pa/phase15_onchain/；不触碰任何账户、服务与历史数据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, cfg
from ..holding.data import load_dataset
from ..holding.metrics import summarize
from ..holding.phase10 import COSTS, WINDOWS
from ..pa11.structure import DailyStructureDetector, structure_state
from .data15 import audit
from .engine15 import FactorBacktester
from .factors15 import all_factors

BOTH = ["BTCUSDT", "ETHUSDT"]
OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase15_onchain"
RANK_COST = 15
BASE_MAP = {"C1": "B1_BTC_SMA200", "C3": "B1_BTC_SMA200", "C4": "B1_BTC_SMA200", "C5": "B1_BTC_SMA200", "H1": "B1_BTC_SMA200", "C2": "B2_modelB_state_machine", "C7": "B2_modelB_state_machine", "C6": "B3_ETH_SMA200",
            "F1B": "B1_BTC_SMA200", "F1E": "B3_ETH_SMA200", "F2": "B1_BTC_SMA200", "F3": "B1_BTC_SMA200", "F4": "B1_BTC_SMA200", "F5": "B3_ETH_SMA200", "F6": "B1_BTC_SMA200"}


def _s(x: pd.Series) -> pd.Series:
    return x.fillna(False).astype(bool)


def structure_gate(bars: dict) -> dict:
    """C7：第 t 日收盘 > 截至 t 已确认的最近摆动低点（第十一阶段 k=3 分形）才允许新入场；无已确认低点 → False。"""
    out = {}
    for s in BOTH:
        b = bars[s].copy(); det = DailyStructureDetector(b); vals = []
        for i in range(len(b)):
            st = structure_state(det.sw, i); vals.append(bool(st["last_low"] is not None and det.close[i] > st["last_low"]))
        out[s] = pd.Series(vals, index=pd.DatetimeIndex(pd.to_datetime(b["ts"], utc=True)))
    return out


def build_strategies(bars: dict, F: dict) -> list[dict]:
    fb, fe = F["BTCUSDT"], F["ETHUSDT"]; S = []
    base = {"risk_exit_dd": None}
    # ---- 基准（与第十阶段一致）
    S += [{"name": "BH_BTC", "family": "BH", "assets": ["BTCUSDT"], "mode": "buyhold", "group": "baseline"},
          {"name": "BH_ETH", "family": "BH", "assets": ["ETHUSDT"], "mode": "buyhold", "group": "baseline"},
          {"name": "BH_5050", "family": "BH", "assets": BOTH, "mode": "buyhold", "group": "baseline"},
          {"name": "B1_BTC_SMA200", "family": "B1", "assets": ["BTCUSDT"], "mode": "trend", "group": "baseline", **base},
          {"name": "B3_ETH_SMA200", "family": "B3", "assets": ["ETHUSDT"], "mode": "trend", "group": "baseline", **base},
          {"name": "B2_modelB_state_machine", "family": "B2", "assets": BOTH, "mode": "trend", "risk_mode": "state_machine", "group": "baseline", **base}]
    # ---- 单因子（factor 模式；预登记禁止作为可执行信号）
    for a in (1.0, 1.2):
        for b in (2.5, 3.0, 3.5):
            S.append({"name": f"F1B_mvrv_in{a}_out{b}", "family": "F1B", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "group": "single", "params": {"a": a, "b": b}, "factor_entry": {"BTCUSDT": _s(fb["mvrv"] < a)}, "factor_exit": {"BTCUSDT": _s(fb["mvrv"] > b)}})
            S.append({"name": f"F1E_mvrv_in{a}_out{b}", "family": "F1E", "assets": ["ETHUSDT"], "mode": "trend", "signal": "factor", "group": "single", "params": {"a": a, "b": b}, "factor_entry": {"ETHUSDT": _s(fe["mvrv"] < a)}, "factor_exit": {"ETHUSDT": _s(fe["mvrv"] > b)}})
    for a in (0.5, 0.6, 0.8):
        for b in (2.5, 4.0):
            S.append({"name": f"F2_puell_in{a}_out{b}", "family": "F2", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "group": "single", "params": {"a": a, "b": b}, "factor_entry": {"BTCUSDT": _s(fb["puell"] < a)}, "factor_exit": {"BTCUSDT": _s(fb["puell"] > b)}})
    S.append({"name": "F3_hash_ribbon", "family": "F3", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "group": "single", "factor_entry": {"BTCUSDT": _s(fb["hash_recovery"])}, "factor_exit": {"BTCUSDT": _s(fb["hash_capitulation"])}})
    for a in (1.0, 1.2):
        for b in (3.0, 4.0):
            S.append({"name": f"F4_cost_in{a}_out{b}", "family": "F4", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "group": "single", "params": {"a": a, "b": b}, "factor_entry": {"BTCUSDT": _s(fb["miner_cost_ratio"] < a)}, "factor_exit": {"BTCUSDT": _s(fb["miner_cost_ratio"] > b)}})
    rs = _s(fe["ethbtc_rs"] > 0)
    S.append({"name": "F5_ethbtc_rotation", "family": "F5", "assets": BOTH, "mode": "trend", "signal": "factor", "group": "single", "factor_entry": {"ETHUSDT": rs, "BTCUSDT": _s(~rs & fe["ethbtc_rs"].notna())}, "factor_exit": {"ETHUSDT": _s(~rs & fe["ethbtc_rs"].notna()), "BTCUSDT": rs}})
    S.append({"name": "F6_ex_netflow", "family": "F6", "assets": ["BTCUSDT"], "mode": "trend", "signal": "factor", "group": "single", "factor_entry": {"BTCUSDT": _s(fb["ex_netflow_z"] < -1)}, "factor_exit": {"BTCUSDT": _s(fb["ex_netflow_z"] > 1)}})
    # ---- 组合（估值背景门控 + SMA200 趋势 + 次日开盘执行）
    for g in (2.0, 2.5, 3.0):
        S.append({"name": f"C1_sma200_mvrv_gate{g}", "family": "C1", "assets": ["BTCUSDT"], "mode": "trend", "group": "combo", "params": {"g": g}, "entry_gate": {"BTCUSDT": _s(fb["mvrv"] < g)}, "force_exit": {"BTCUSDT": _s(fb["mvrv"] > 3.5)}, **base})
        S.append({"name": f"C2_b2_mvrv_gate{g}", "family": "C2", "assets": BOTH, "mode": "trend", "risk_mode": "state_machine", "group": "combo", "params": {"g": g}, "entry_gate": {"BTCUSDT": _s(fb["mvrv"] < g), "ETHUSDT": _s(fe["mvrv"] < g)}, "force_exit": {"BTCUSDT": _s(fb["mvrv"] > 3.5), "ETHUSDT": _s(fe["mvrv"] > 3.5)}, **base})
    S.append({"name": "C3_sma200_hash_gate", "family": "C3", "assets": ["BTCUSDT"], "mode": "trend", "group": "combo", "entry_gate": {"BTCUSDT": _s(~fb["hash_capitulation"].astype(bool) & fb["hash_sma60"].notna())}, **base})
    S.append({"name": "C4_sma200_puell_gate", "family": "C4", "assets": ["BTCUSDT"], "mode": "trend", "group": "combo", "entry_gate": {"BTCUSDT": _s(fb["puell"] < 3)}, "force_exit": {"BTCUSDT": _s(fb["puell"] > 4)}, **base})
    for g in (0.0, 0.5, 1.0):
        S.append({"name": f"C5_sma200_valscore_gate{g}", "family": "C5", "assets": ["BTCUSDT"], "mode": "trend", "group": "combo", "params": {"g": g}, "entry_gate": {"BTCUSDT": _s(fb["valuation_score"] < g)}, **base})
    S.append({"name": "C6_eth_sma200_rs_mvrv_gate", "family": "C6", "assets": ["ETHUSDT"], "mode": "trend", "group": "combo", "entry_gate": {"ETHUSDT": _s((fe["ethbtc_rs"] > 0) & (fe["mvrv"] < 2.5))}, **base})
    sg = structure_gate(bars)
    S.append({"name": "C7_b2_mvrv2.5_structure_gate", "family": "C7", "assets": BOTH, "mode": "trend", "risk_mode": "state_machine", "group": "combo", "entry_gate": {"BTCUSDT": _s((fb["mvrv"] < 2.5) & sg["BTCUSDT"].reindex(fb.index).fillna(False)), "ETHUSDT": _s((fe["mvrv"] < 2.5) & sg["ETHUSDT"].reindex(fe.index).fillna(False))}, "force_exit": {"BTCUSDT": _s(fb["mvrv"] > 3.5), "ETHUSDT": _s(fe["mvrv"] > 3.5)}, **base})
    S.append({"name": "H1_sma200_halving_gate", "family": "H1", "assets": ["BTCUSDT"], "mode": "trend", "group": "combo", "entry_gate": {"BTCUSDT": _s(fb["halving_phase"] < 0.75)}, **base})
    return S


def run_one(bars: dict, spec: dict, cost_bps: int, start: str, end: str | None, c: dict):
    fee, slip = COSTS[cost_bps]
    return FactorBacktester(bars, spec, fee, slip, start, end, sma=int(c["usdt"]["trend"]["sma"]), risk_params=c.get("trendsys", {}).get("risk", {})).run()


def _row(res, spec, window, cost, btc_close, bh: dict) -> dict:
    s = summarize(res, btc_close); liq = s["liquidation_net_profit_usdt"]
    s.update({"window": window, "cost_bps": cost, "family": spec["family"], "group": spec["group"], "params": spec.get("params"), "assets": spec["assets"], "time_flat": 1 - s["time_in_market"], "turnover_cost_usdt": s["total_cost_usdt"],
              "excess_vs_bh": {k: liq - v for k, v in bh.items()}, "evidence": "insufficient(<10 round trips)" if s["trades"]["n_round_trips"] < 10 and spec["mode"] != "buyhold" else "ok"})
    return s


def select_and_judge(rows: list[dict], strategies: list[dict]) -> dict:
    """选参只看训练段 15 bps 清仓净收益；验证段必须 > 对应基准；样本外只报告被选变体；全网格样本外公开（不用于选择）。"""
    R = pd.DataFrame([{k: v for k, v in r.items() if k not in ("annual_returns", "trades", "regimes", "excess_vs_bh", "params", "assets")} | {"n_round_trips": r["trades"]["n_round_trips"]} for r in rows])
    def val(name, window, cost=RANK_COST, col="liquidation_net_profit_usdt"):
        x = R[(R.name == name) & (R.window == window) & (R.cost_bps == cost)]; return float(x[col].iloc[0]) if len(x) else np.nan
    fams = {}
    for sp in strategies:
        fams.setdefault(sp["family"], []).append(sp["name"])
    out = {}
    for fam, names in fams.items():
        if fam in ("BH", "B1", "B3", "B2"):
            continue
        base = BASE_MAP.get(fam); train = {n: val(n, "train") for n in names}; chosen = max(train, key=lambda n: (train[n] if np.isfinite(train[n]) else -1e18))
        rec = {"family": fam, "baseline": base, "grid": names, "train_15bps": train, "chosen_on_train": chosen,
               "valid": {"chosen": val(chosen, "valid"), "baseline": val(base, "valid"), "confirmed": bool(val(chosen, "valid") > val(base, "valid"))},
               "oos": {"chosen": val(chosen, "oos"), "baseline": val(base, "oos"), "all_grid_oos_for_transparency": {n: val(n, "oos") for n in names}},
               "oos_mdd": {"chosen": val(chosen, "oos", col="max_drawdown"), "baseline": val(base, "oos", col="max_drawdown")},
               "cost_consistency": {c: {"chosen": val(chosen, "oos", c), "baseline": val(base, "oos", c)} for c in (0, 25)},
               "train_improve": bool(val(chosen, "train") > val(base, "train")), "n_round_trips_oos": val(chosen, "oos", col="n_round_trips")}
        oos_better = rec["oos"]["chosen"] > rec["oos"]["baseline"]; mdd_ok = rec["oos_mdd"]["chosen"] >= rec["oos_mdd"]["baseline"] - 1e-9
        cost_ok = all((v["chosen"] > v["baseline"]) == oos_better for v in rec["cost_consistency"].values() if np.isfinite(v["chosen"]) and np.isfinite(v["baseline"]))
        enough = (rec["n_round_trips_oos"] or 0) >= 10
        if fam.startswith("F"):
            verdict = "单因子：仅供理解因子行为，预登记不作为可执行信号候选" + ("；样本外优于基准" if oos_better else "；样本外不优于基准") + ("" if enough else "；闭合交易 < 10 → 证据不足")
        elif not enough:
            verdict = "证据不足（样本外闭合交易 < 10）"
        elif rec["train_improve"] and rec["valid"]["confirmed"] and oos_better and mdd_ok and cost_ok:
            verdict = "确实改善样本外（训练 / 验证 / 样本外同向，回撤不劣，0/25 bps 一致）"
        elif oos_better and not (rec["train_improve"] and rec["valid"]["confirmed"]):
            verdict = "样本外优于基准但训练 / 验证未同向 → 证据不足（可能是巧合）"
        elif oos_better and not mdd_ok:
            verdict = "样本外收益更高但回撤更差 → 未通过"
        elif oos_better and not cost_ok:
            verdict = "样本外优于基准但成本敏感（0/25 bps 不一致）→ 证据不足"
        else:
            verdict = "未改善（样本外不优于基准）"
        rec["verdict"] = verdict; out[fam] = rec
    return out


def run_phase15(c: dict | None = None, out_dir: Path | None = None, dry_run: bool = False, costs: list[int] | None = None, families: list[str] | None = None) -> dict:
    c = c or cfg(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); bars = load_dataset(c)
    latest = str(bars["BTCUSDT"]["ts"].iloc[-1].date()); F = all_factors(bars); strategies = build_strategies(bars, F)
    if families:
        strategies = [s for s in strategies if s["family"] in families]
    windows = {"dry": ("2023-01-01", "2023-12-31")} if dry_run else {k: (a, b or latest) for k, (a, b) in WINDOWS.items()}
    costs = costs or ([RANK_COST] if dry_run else [RANK_COST, 0, 25])
    btc_close = bars["BTCUSDT"].set_index(pd.DatetimeIndex(pd.to_datetime(bars["BTCUSDT"]["ts"], utc=True)))["close"].astype(float)
    rows = []; curves = {}
    for w, (a, b) in windows.items():
        for cost in costs:
            bh = {}
            for sp in strategies:
                if sp["group"] == "baseline" and sp["mode"] == "buyhold":
                    r = run_one(bars, sp, cost, a, b, c); bh[sp["name"]] = summarize(r)["liquidation_net_profit_usdt"]
            for sp in strategies:
                r = run_one(bars, sp, cost, a, b, c); rows.append(_row(r, sp, w, cost, btc_close, bh))
                if w in ("oos", "full", "dry") and cost == RANK_COST:
                    curves[f"{w}|{sp['name']}"] = r.nav[["ts", "equity", "liquidation", "exposure"]].assign(ts=lambda d: d["ts"].astype(str)).to_dict("records")
    judge = select_and_judge(rows, strategies) if not dry_run else {}
    res = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；日线决策 + 链上因子滞后 1 日；次日开盘执行；非前瞻）", "latest_complete_day": latest, "windows": windows, "costs": costs, "dry_run": dry_run,
           "preregistration": "docs/brando_pa_validation/phase15_onchain/preregistration.md", "data_audit": audit(bars), "n_strategies": len(strategies), "strategies": [{k: v for k, v in s.items() if k in ("name", "family", "group", "assets", "mode", "signal", "risk_mode", "params")} for s in strategies],
           "rows": rows, "selection_and_verdicts": judge, "real_trading": "disabled (no implementation)"}
    (out / ("phase15_results_dry.json" if dry_run else "phase15_results.json")).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if not dry_run:
        (out / "phase15_curves_15bps.json").write_text(json.dumps(curves, ensure_ascii=False, default=str), encoding="utf-8")
        (out / "phase15_summary.md").write_text(render_summary(res), encoding="utf-8")
    return res


def _pct(x):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.1%}"


def render_summary(res: dict) -> str:
    L = [f"# 第十五阶段回测结果（{res['generated_at_utc'][:16]} UTC · 最新完整日线 {res['latest_complete_day']} · {res['category']}）", "", "预登记：" + res["preregistration"], ""]
    rows = res["rows"]
    for w in ("train", "valid", "oos", "full"):
        for cost in (15,):
            sel = [r for r in rows if r["window"] == w and r["cost_bps"] == cost]
            if not sel:
                continue
            a, b = res["windows"][w]; L += [f"## 窗口 {w}（{a} → {b}）· 单侧成本 {cost} bps · 初始 10,000 USDT", "", "| 策略 | 组 | 假设清仓净收益 | 最大回撤 | 成交 | 闭合 | 空仓比例 | 均持仓(日) | 换手成本 | vs BH_BTC | vs BH_ETH | vs BH_5050 | 牛/熊/震荡 盈亏 | 证据 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for r in sorted(sel, key=lambda r: -r["liquidation_net_profit_usdt"]):
                rg = r.get("regimes", {}); t = r["trades"]
                L.append(f"| {r['name']} | {r['group']} | {r['liquidation_net_profit_usdt']:,.0f} | {_pct(r['max_drawdown'])} | {r['n_fills']} | {t['n_round_trips']} | {_pct(r['time_flat'])} | {t['avg_hold_days'] if t['avg_hold_days'] is None else round(t['avg_hold_days'])} | {r['turnover_cost_usdt']:,.0f} | {r['excess_vs_bh'].get('BH_BTC', float('nan')):,.0f} | {r['excess_vs_bh'].get('BH_ETH', float('nan')):,.0f} | {r['excess_vs_bh'].get('BH_5050', float('nan')):,.0f} | {rg.get('bull', {}).get('pnl_usdt', 0):,.0f} / {rg.get('bear', {}).get('pnl_usdt', 0):,.0f} / {rg.get('sideways', {}).get('pnl_usdt', 0):,.0f} | {r['evidence']} |")
            L.append("")
    L += ["## 选参与判定（选参只用训练段；验证段确认；样本外只评估被选变体；全网格样本外结果仅供透明展示）", "", "| 因子族 | 基准 | 训练段选中 | 验证：选中 / 基准 / 确认 | 样本外：选中 / 基准 | 样本外回撤：选中 / 基准 | 0 bps 选中/基准 | 25 bps 选中/基准 | 样本外闭合 | 判定 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for fam, j in res["selection_and_verdicts"].items():
        cc = j["cost_consistency"]
        L.append(f"| {fam} | {j['baseline']} | {j['chosen_on_train']} | {j['valid']['chosen']:,.0f} / {j['valid']['baseline']:,.0f} / {'是' if j['valid']['confirmed'] else '否'} | {j['oos']['chosen']:,.0f} / {j['oos']['baseline']:,.0f} | {_pct(j['oos_mdd']['chosen'])} / {_pct(j['oos_mdd']['baseline'])} | {cc[0]['chosen']:,.0f}/{cc[0]['baseline']:,.0f} | {cc[25]['chosen']:,.0f}/{cc[25]['baseline']:,.0f} | {int(j['n_round_trips_oos'] or 0)} | {j['verdict']} |")
    L += ["", "全网格样本外（透明展示，不用于选择）：", ""]
    for fam, j in res["selection_and_verdicts"].items():
        if len(j["grid"]) > 1:
            L.append(f"- {fam}: " + "; ".join(f"{n} {v:,.0f}" for n, v in j["oos"]["all_grid_oos_for_transparency"].items()))
    L += ["", "数据不可得项（未用估计值冒充）：", ""] + [f"- {k}：{v}" for k, v in res["data_audit"]["unavailable"].items()]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    r = run_phase15(dry_run=dry)
    print(json.dumps({"n_strategies": r["n_strategies"], "n_rows": len(r["rows"]), "windows": r["windows"], "costs": r["costs"], "dry_run": dry}, ensure_ascii=False))
