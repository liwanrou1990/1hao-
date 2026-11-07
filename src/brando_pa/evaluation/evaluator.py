"""Strategy Evaluator（第二阶段 §九）：对 资产 × 周期 × 方向 × 策略 的样本外结果按预先定义的门槛打分。

状态：
  qualified_paper   可进入模拟盘候选池：全部门槛通过 + 均值 R 置信下限 > 0 + 交易数 ≥ paper_min_trades + 周期政策允许
  research_only     通过基础门槛但置信区间含 0 / 样本不足 / 周期政策为 analysis_only：继续研究，不进模拟盘
  unqualified       样本外亏损或未达门槛
门槛来自 config.evaluator，全部样本外（Walk-Forward 测试窗拼接），不看训练窗。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg, report_dir


def evaluate_one(stats: dict, ev: dict, tf: str, label: str) -> dict:
    reasons = []; n = stats.get("n_trades", 0)
    if n == 0:
        return {"label": label, "status": "unqualified", "reasons": ["样本外无交易"], "stats": {}}
    exp = stats.get("expectancy_r"); pf = stats.get("profit_factor"); dd = stats.get("max_drawdown"); ci = stats.get("bootstrap_mean_r_95ci", {}) or {}
    fold_share = stats.get("fold_nonneg_share")
    checks = {"min_trades": n >= int(ev["min_trades"]), "expectancy_r": exp is not None and exp > float(ev["min_expectancy_r"]), "profit_factor": pf is not None and pf >= float(ev["min_profit_factor"]),
              "max_drawdown": dd is not None and dd >= -float(ev["max_drawdown"]), "fold_stability": fold_share is None or fold_share >= float(ev["min_fold_nonneg_share"]),
              "ci_lower": ci.get("lower") is not None and ci["lower"] >= float(ev["bootstrap_ci_lower_r_min"])}
    for k, ok in checks.items():
        if not ok:
            reasons.append({"min_trades": f"样本外交易 {n} < {ev['min_trades']}", "expectancy_r": f"扣费后期望 {exp} ≤ {ev['min_expectancy_r']}", "profit_factor": f"PF {pf} < {ev['min_profit_factor']}",
                            "max_drawdown": f"最大回撤 {dd} 超过 {ev['max_drawdown']}", "fold_stability": f"测试折非负占比 {fold_share} < {ev['min_fold_nonneg_share']}", "ci_lower": f"均值 R 置信下限 {ci.get('lower')} < {ev['bootstrap_ci_lower_r_min']}"}[k])
    base_ok = all(checks.values())
    policy = (ev.get("tf_policy") or {}).get(tf)
    status = "unqualified"
    if base_ok:
        status = "research_only"
        paper_ok = (ci.get("lower") is not None and ci["lower"] > 0) if ev.get("paper_requires_ci_lower_gt0", True) else True
        if paper_ok and n >= int(ev["paper_min_trades"]) and policy != "analysis_only":
            status = "qualified_paper"
        else:
            if not paper_ok:
                reasons.append(f"均值 R 95% 置信下限 {ci.get('lower')} 未 > 0（统计上不能与零区分）")
            if n < int(ev["paper_min_trades"]):
                reasons.append(f"交易数 {n} < 模拟盘最低 {ev['paper_min_trades']}")
            if policy == "analysis_only":
                reasons.append(f"周期政策：{tf} 仅用于趋势/结构分析，不直接产生模拟盘开仓")
    return {"label": label, "status": status, "checks": checks, "reasons": reasons,
            "stats": {k: stats.get(k) for k in ("n_trades", "expectancy_r", "profit_factor", "net_return", "max_drawdown", "win_rate", "sharpe", "sortino", "max_consecutive_losses", "fold_nonneg_share")} | {"ci_mean_r": [ci.get("lower"), ci.get("upper")]}}


def evaluate_walkforward_dir(wf_json_paths: dict, c: dict | None = None) -> dict:
    """wf_json_paths: {run_name: (exec_tf, path)} 或 {exec_tf: path}。"""
    c = c or cfg(); ev = c["evaluator"]
    rows = []
    for name, item in wf_json_paths.items():
        tf, p = item if isinstance(item, tuple) else (name, item)
        if not Path(p).exists():
            continue
        wj = json.loads(Path(p).read_text())
        variant = (("+环境启停" if wj.get("regime_gating") else "") + ("+" + ",".join(wj.get("overrides", [])) if wj.get("overrides") else "")) or "基线"
        for strat, r in wj["results"].items():
            rows.append({"run": name, "variant": variant, "exec_tf": tf, "strategy": strat, "symbol": "ALL", "direction": "ALL"} | evaluate_one(r["oos_stats"], ev, tf, f"{name}/{strat}/ALL/ALL"))
            for key, st in (r.get("subsets") or {}).items():
                if "/" in key:
                    sym, d = key.split("/")
                    rows.append({"run": name, "variant": variant, "exec_tf": tf, "strategy": strat, "symbol": sym, "direction": d} | evaluate_one(st, ev, tf, f"{name}/{strat}/{sym}/{d}"))
                elif key.startswith("direction="):
                    rows.append({"run": name, "variant": variant, "exec_tf": tf, "strategy": strat, "symbol": "ALL", "direction": key.split("=")[1]} | evaluate_one(st, ev, tf, f"{name}/{strat}/ALL/{key.split('=')[1]}"))
                elif key.startswith("symbol="):
                    rows.append({"run": name, "variant": variant, "exec_tf": tf, "strategy": strat, "symbol": key.split("=")[1], "direction": "ALL"} | evaluate_one(st, ev, tf, f"{name}/{strat}/{key.split('=')[1]}/ALL"))
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "thresholds": ev, "rows": rows,
           "qualified_paper": [r["label"] for r in rows if r["status"] == "qualified_paper"], "research_only": [r["label"] for r in rows if r["status"] == "research_only"],
           "unqualified": [r["label"] for r in rows if r["status"] == "unqualified"]}
    od = report_dir(c) / "phase2"; od.mkdir(parents=True, exist_ok=True)
    atomic_write_json(od / "strategy_evaluation.json", out, indent=2, default=str)
    atomic_write_text(od / "strategy_evaluation.md", evaluation_markdown(out))
    out["paths"] = {"json": str(od / "strategy_evaluation.json"), "md": str(od / "strategy_evaluation.md")}
    return out


def _f(x, pct=False, d=2):
    if x is None:
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


def evaluation_markdown(out: dict) -> str:
    L = [f"# Strategy Evaluator 结果（{out['generated_at'][:16]} UTC）", "", f"门槛（全部样本外）：{json.dumps(out['thresholds'], ensure_ascii=False)}", "",
         "| 运行 | 周期 | 策略 | 标的 | 方向 | 状态 | OOS 交易 | ExpR | PF | 净收益 | 最大回撤 | 均值R 95%CI | 折非负占比 | 未达标原因 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["rows"]:
        s = r["stats"]; ci = s.get("ci_mean_r", [None, None])
        L.append(f"| {r.get('run', '')}（{r.get('variant', '')}） | {r['exec_tf']} | {r['strategy']} | {r['symbol']} | {r['direction']} | **{r['status']}** | {s.get('n_trades', 0)} | {_f(s.get('expectancy_r'), d=3)} | {_f(s.get('profit_factor'))} | {_f(s.get('net_return'), True)} | {_f(s.get('max_drawdown'), True)} | [{_f(ci[0], d=3)}, {_f(ci[1], d=3)}] | {_f(s.get('fold_nonneg_share'))} | {'；'.join(r['reasons'])} |")
    L += ["", f"**合格（模拟盘候选）**：{out['qualified_paper'] or '无'}", f"**仅研究**：{out['research_only'] or '无'}", f"**不合格**：{len(out['unqualified'])} 项", ""]
    return "\n".join(L) + "\n"
