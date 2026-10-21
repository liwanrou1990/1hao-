"""70 组测试矩阵报告：每个品种 × 档位 输出 A 理论 / B 事后可执行上限（含约束）/ C 因果引擎 / 能力核实 / 原因 / 样本外，并给出下一轮优化建议。"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..target_model.execution import bar_liquidity
from .daily import _num, _pct

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["pct"] = _pct
_env.filters["num"] = _num


def build_matrix(pipeline, day: date, symbols: list[str] | None = None) -> list[dict]:
    rows = []
    part = float(pipeline.engine_cfg.execution.get("participation_rate", 0.1))
    for sym in symbols or pipeline.symbols():
        bars = pipeline.hindsight_bars(sym, day)
        if bars.empty:
            continue
        liq = bar_liquidity(bars, part)
        day_budget = float(np.sum(liq)) if np.isfinite(liq).all() else None
        cap = pipeline.capability(sym)
        engines = {c["tier"]: c for c in pipeline.engine_tiers(sym, day)}
        comps = {c["tier"]: c for c in pipeline.compare_tiers(sym, day)}
        for tier, e in engines.items():
            r, th, cls, opt = e["run"], e["theoretical"], e["classification"], e["optimization"]
            b = comps.get(tier, {}).get("executable")
            rows.append({"symbol": sym, "tier": tier, "capital_usd": e["capital_usd"], "target_usd": e["target_usd"],
                         "verification": cap.verification_status, "directions": "/".join(cap.directions) or "无",
                         "theoretical_usd": th.max_net_profit_usd,
                         "b_exec_usd": b.realized_net_usd if b else None, "b_exec_trades": b.n_trades if b else None, "b_exec_met": b.target_met if b else None,
                         "net_usd": r.realized_net_usd, "net_return_pct": r.realized_net_return * 100,
                         "n_buys": r.n_long + r.n_short, "n_sells": r.n_long + r.n_short, "n_long": r.n_long, "n_short": r.n_short,
                         "win_rate": r.win_rate, "trading_cost_usd": r.execution_cost_usd + r.funding_cost_usd, "max_dd_usd": r.max_drawdown_usd,
                         "avg_utilization": r.avg_deployed_usd / e["capital_usd"], "day_liquidity_budget_usd": day_budget,
                         "capacity_ratio": (day_budget / e["capital_usd"]) if day_budget else None,
                         "target_met": r.target_met, "tags": "、".join(cls["tags"]), "details": "；".join(cls["details"]),
                         "oos_days": opt.oos_stats.n_days if opt else 0, "oos_attainment": opt.oos_stats.attainment_rate if opt else None,
                         "oos_mean_daily_return": opt.oos_stats.mean_daily_return if opt else None, "oos_max_dd_pct": opt.oos_stats.max_drawdown_pct if opt else None,
                         "params": r.params.to_dict(), "pause_reasons": r.pause_reasons, "unverifiable": r.unverifiable})
    return rows


def suggestions(rows: list[dict], cost: dict) -> list[str]:
    n = len(rows)
    if not n:
        return ["无数据"]
    tag_counts: dict[str, int] = {}
    for r in rows:
        if not r["target_met"]:
            for t in (r["tags"] or "").split("、"):
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
    out = []
    if tag_counts.get("未验证"):
        out.append(f"{tag_counts['未验证']} 组因品种未经 API 核实而未纳入：放行 bopenapi.bgwapi.io 后运行 bgwq verify-matrix，确认 SPCX 等标的身份、链、合约与市场状态。")
    if tag_counts.get("成本过高"):
        out.append(f"{tag_counts['成本过高']} 组受成本门槛限制：当前假设手续费 {cost['fee_bps']} bps、半价差 {cost['spread_bps']} bps、滑点 {cost['slippage_bps']} bps；"
                   "请用 getSwapPrice 只读报价与 transactionList 实测真实价差/滑点替换假设，并优先在波动放大的时段交易（成本门槛由波动/成本比动态判断）。")
    if tag_counts.get("流动性不足") or tag_counts.get("资金容量限制"):
        out.append("大资金档位受单根 K 线可成交量限制：需要真实盘口深度/池子 TVL 数据校准参与率；可考虑跨多链/多发行商（ondo + xstocks）合约分散成交、延长分批时间窗（但会增加价格变化风险）。")
    if tag_counts.get("策略不足"):
        out.append(f"{tag_counts['策略不足']} 组理论上可达但因果引擎未达：扩大参数网格（趋势阈值、ATR 止盈倍数、并行仓位）、引入隔夜/跨日持仓规则与更长训练窗；在真实行情上重新评估是否存在可预测的日内结构。")
    b_met = sum(1 for r in rows if r.get("b_exec_met"))
    out.append(f"事后可执行上限（B，含流动性/成本约束）达标 {b_met}/{n}，因果引擎（C）达标 {sum(1 for r in rows if r['target_met'])}/{n}：两者差距即“不可预知”部分；B 未达标的档位在当前流动性/成本下客观不可实现，建议下调该档位目标或放宽周期。")
    out.append("所有结论基于当前数据源；在真实 1m/5m/15m K 线与只读可执行报价接入前，达标率不构成验证结论。")
    return out


def generate_matrix_report(pipeline, day: date, symbols: list[str] | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / day.isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_matrix(pipeline, day, symbols)
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in rows]
    cost = pipeline.cost.describe()
    sugg = suggestions(rows, cost)
    tag_counts: dict[str, int] = {}
    for r in rows:
        if not r["target_met"]:
            for t in (r["tags"] or "").split("、"):
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
    csv_path = out_dir / f"matrix_{day}_{pipeline.cost_profile_name}.csv"
    cols = [k for k in rows[0].keys() if k not in ("params", "pause_reasons")] if rows else []
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    html = _env.get_template("matrix_report.html.j2").render(
        day=day.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(), status=pipeline.status(), rows=rows,
        n_met=sum(1 for r in rows if r["target_met"]), n_b_met=sum(1 for r in rows if r.get("b_exec_met")), tag_counts=tag_counts,
        suggestions=sugg, cost=cost, cost_profile=pipeline.cost_profile_name, csv_name=csv_path.name, formal=not pipeline.plan.is_simulated,
        cfg=pipeline.engine_cfg)
    html_path = out_dir / f"matrix_report_{day}_{pipeline.cost_profile_name}.html"
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "csv": str(csv_path), "n_rows": len(rows), "n_met": sum(1 for r in rows if r["target_met"]),
            "n_b_met": sum(1 for r in rows if r.get("b_exec_met")), "tag_counts": tag_counts, "suggestions": sugg,
            "formal_validation": not pipeline.plan.is_simulated}
