"""渲染第二阶段结果章节（只读产物）。输出 Markdown 到 stdout；同时把关键 JSON/CSV 复制到 docs/brando_pa_validation/phase2/。"""
import json
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path("/home/user/1hao-")
RD = ROOT / "reports_out/brando_pa"
OUT = ROOT / "docs/brando_pa_validation/phase2"
OUT.mkdir(parents=True, exist_ok=True)


def f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


L = []
# ---------- 原系统 vs 优化后 ----------
base = {n: load(ROOT / f"docs/brando_pa_validation/baseline_phase1/backtest_{n}_compare.json") for n in ("m15_2025_2026", "h1_2022_2026", "h4_2018_2026")}
exps = {n: load(RD / "phase2/experiments" / n / "experiments.json") for n in ("h1", "m15", "h4")}
L.append("## A. 原系统（第一阶段基准）与优化后系统并排对比\n")
L.append("“优化后”不是单一配置，而是第二阶段实验矩阵里每个维度的最优与最差变体；所有变体都在同一数据、同一成本模型、同一保守成交规则下运行。基准 = 第一阶段 all_filters / 单策略 filters。\n")
L.append("| 周期 | 组合 | 基准：交易/PF/ExpR/净收益/回撤 | 第二阶段 market+single（应与基准一致）| 第二阶段最佳变体（按 ExpR，≥20 笔） | 第二阶段最差变体 |")
L.append("|---|---|---|---|---|---|")
for tf, bn, en in (("15m", "m15_2025_2026", "m15"), ("1h", "h1_2022_2026", "h1"), ("4h", "h4_2018_2026", "h4")):
    b = base.get(bn); e = exps.get(en)
    if not b or not e:
        continue
    for sk, bkey in (("ALL", "all_filters"), ("A_breakout_retest", "A_breakout_retest_filters"), ("B_reclaim", "B_reclaim_filters"), ("C_rejection", "C_rejection_filters")):
        br = b["variants"].get(bkey, {})
        same = e["variants"].get(f"{sk}__entry_market__mtf_single", {})
        cands = {k: v for k, v in e["variants"].items() if k.startswith(sk + "__") and (v.get("n_trades") or 0) >= 20 and v.get("expectancy_r") is not None}
        best = max(cands.items(), key=lambda kv: kv[1]["expectancy_r"]) if cands else (None, {})
        worst = min(cands.items(), key=lambda kv: kv[1]["expectancy_r"]) if cands else (None, {})
        fmt = lambda r: f"{r.get('n_trades', 0)} / {f(r.get('profit_factor'))} / {f(r.get('expectancy_r'), d=3)} / {f(r.get('total_net_return'), True)} / {f(r.get('max_drawdown'), True)}" if r else "—"
        L.append(f"| {tf} | {sk} | {fmt(br)} | {fmt(same)} | {best[0].split('__', 1)[1] if best[0] else '—'}：{fmt(best[1])} | {worst[0].split('__', 1)[1] if worst[0] else '—'}：{fmt(worst[1])} |")
L.append("")

# ---------- 入场模式 / 多周期 / 退出 ----------
L.append("## B. 市价与限价入场、多周期模式、退出方案对比\n")
for en, title in (("h1", "1h（2022-04 → 2026-11）"), ("m15", "15m（2025-02 → 2026-11）"), ("h4", "4h（2018-06 → 2026-11，仅基线/对齐/区域限价三种）")):
    e = exps.get(en)
    if not e:
        L.append(f"### {title}\n\n未完成。\n"); continue
    L.append(f"### {title}\n")
    L.append("| 变体 | 交易 | 胜率 | PF | ExpR | 净收益 | 回撤 | Sharpe | 连亏 | 手续费 | 滑点 | 资金费 | 候选→通过 | 限价 挂/成/撤 | 未成交机会PnL |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, r in e["variants"].items():
        lim = f"{r.get('limit_orders')}/{r.get('limit_filled')}/{r.get('limit_cancelled')}" if r.get("limit_orders") is not None else "—"
        L.append(f"| {k} | {r['n_trades']} | {f(r['win_rate'], True, 1)} | {f(r['profit_factor'])} | {f(r['expectancy_r'], d=3)} | {f(r['total_net_return'], True)} | {f(r['max_drawdown'], True)} | {f(r['sharpe'])} | {r['max_consecutive_losses'] or '—'} | {f(r['total_fees'])} | {f(r['total_slippage'])} | {f(r['total_funding'])} | {r.get('candidates')}→{r.get('accepted')} | {lim} | {f(r.get('missed_opportunity_net_pnl_sum'))} |")
    L.append("")
    shutil.copy(RD / "phase2/experiments" / en / "experiments.json", OUT / f"experiments_{en}.json")
    shutil.copy(RD / "phase2/experiments" / en / "experiments.md", OUT / f"experiments_{en}.md")

# ---------- 环境 ----------
L.append("## C. 不同市场环境下的收益与回撤（入场时日线环境四分类；基线 market+single）\n")
for en in ("h1", "m15", "h4"):
    e = exps.get(en)
    if not e:
        continue
    L.append(f"### {en}\n")
    L.append("| 策略 | 环境 | 交易 | 胜率 | ExpR | 净盈亏 | PF |")
    L.append("|---|---|---|---|---|---|---|")
    for sk in ("A_breakout_retest", "B_reclaim", "C_rejection", "ALL"):
        r = e["variants"].get(f"{sk}__entry_market__mtf_single", {})
        for reg, g in (r.get("by_regime4") or {}).items():
            L.append(f"| {sk} | {reg} | {g['n']} | {f(g['win_rate'], True, 0)} | {f(g['expectancy_r'], d=2)} | {f(g['net_pnl'])} | {f(g['pf'])} |")
    L.append("")
    r = e["variants"].get("ALL__entry_market__mtf_single", {})
    L.append("ALL 按方向：" + "；".join(f"{k} {g['n']} 笔 / ExpR {f(g['expectancy_r'], d=2)} / 净 {f(g['net_pnl'])}" for k, g in (r.get("by_direction") or {}).items())
             + "。按标的：" + "；".join(f"{k} {g['n']} 笔 / ExpR {f(g['expectancy_r'], d=2)} / 净 {f(g['net_pnl'])}" for k, g in (r.get("by_symbol") or {}).items())
             + "。按年：" + "；".join(f"{k} {g['n']} 笔 / ExpR {f(g['expectancy_r'], d=2)}" for k, g in (r.get("by_year") or {}).items()) + "\n")

# ---------- 漏斗 ----------
L.append("## D. 候选信号拒绝原因分析（漏斗）\n")
for en in ("h1", "m15", "h4"):
    e = exps.get(en)
    if not e:
        continue
    fp = RD / "phase2/experiments" / en / "ALL__entry_market__mtf_single_funnel.csv"
    if not fp.exists():
        continue
    df = pd.read_csv(fp)
    shutil.copy(fp, OUT / f"funnel_{en}_ALL_market_single.csv")
    L.append(f"### {en}（ALL, market, single）\n")
    cols = ["symbol", "direction", "strategy", "setup", "candidate", "after_trend", "after_price", "after_cost", "after_rr", "after_risk", "order", "fill", "cancelled_at_fill", "avg_stop_distance_pct", "avg_stop_distance_atr", "median_cost_to_risk", "median_cost_share_of_reward", "avg_net_rr_valid"]
    cols = [c for c in cols if c in df.columns]
    L.append("| " + " | ".join(cols) + " |"); L.append("|" + "---|" * len(cols))
    for _, r in df.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                vals.append(f"{v:.2%}" if ("pct" in c or "share" in c or "cost_to_risk" in c) else f"{v:.2f}")
            else:
                vals.append("—" if pd.isna(v) else str(v))
        L.append("| " + " | ".join(vals) + " |")
    L.append("")
    tot = df[["candidate", "after_trend", "after_price", "after_cost", "after_rr", "after_risk", "order", "fill"]].sum()
    L.append("合计：" + " → ".join(f"{k} {int(v)}" for k, v in tot.items()) + "\n")
    L.append("主要拒绝原因：" + "；".join(f"{r['symbol']}/{r['direction']}/{r['strategy']}: {r['top_reject_reasons']}" for _, r in df.iterrows()) + "\n")
    lp = RD / "phase2/experiments" / en / "ALL__entry_limit__mtf_single_limit_orders.csv"
    if lp.exists():
        ll = pd.read_csv(lp); canc = ll[ll["status"].str.startswith("cancel")]
        L.append(f"限价模式（ALL, limit, single）：挂单 {len(ll)}，成交 {(ll['status'] == 'filled').sum()}，未成交 {len(canc)}（{canc['status'].value_counts().to_dict()}）；未成交单若按市价入场的假设净盈亏合计 {canc['hypothetical_market_net_pnl'].sum():.2f} USDT，平均 {canc['hypothetical_market_r'].mean():.2f}R。\n")
ca = load(RD / "phase2/cost_to_risk_audit.json")
if ca:
    L.append("cost_to_risk 语义审计：" + ca["perp_15m_tight_stop"]["formula"] + "；" + ca["perp_15m_tight_stop"]["denominator"] + "；" + ca["perp_15m_tight_stop"]["semantic_check"] + " 样例：15m 止损 0.5% → 比值 " + f"{ca['perp_15m_tight_stop']['example']['ratio']:.0%}" + f"（成本 {ca['perp_15m_tight_stop']['example']['cost_bps']:.0f} bps）；1h 止损 1.5% → {ca['perp_1h_wider_stop']['example']['ratio']:.0%}（其中资金费按最长持仓 96 小时估算占 {ca['perp_1h_wider_stop']['example']['components']['funding_est']:.0f}/{ca['perp_1h_wider_stop']['example']['cost_per_unit']:.0f}）。" + ca["perp_15m_tight_stop"]["caveat"] + "\n")
    shutil.copy(RD / "phase2/cost_to_risk_audit.json", OUT / "cost_to_risk_audit.json")

# ---------- 费用敏感性（实验） ----------
L.append("## E. 各项费用对收益的影响\n")
for en, title in (("h1_cost_sens", "1h 资金费=样本均值 0.00004/8h"), ("h1_cost_sens_slip2", "1h 滑点 10 bps、止损滑点 20 bps"), ("h1_cost_sens_fee10", "1h taker 10 bps"), ("m15_cost_sens", "15m 资金费=样本均值 0.00004/8h")):
    e = load(RD / "phase2/experiments" / en / "experiments.json")
    if not e:
        L.append(f"- {title}：未完成"); continue
    for k, r in e["variants"].items():
        L.append(f"- {title} · {k.split('__set')[0]}：{r['n_trades']} 笔，PF {f(r['profit_factor'])}，ExpR {f(r['expectancy_r'], d=3)}，净收益 {f(r['total_net_return'], True)}，手续费 {f(r['total_fees'])} 滑点 {f(r['total_slippage'])} 资金费 {f(r['total_funding'])}")
    shutil.copy(RD / "phase2/experiments" / en / "experiments.json", OUT / f"experiments_{en}.json")
L.append("")

# ---------- Walk-Forward ----------
L.append("## F. 三套策略在 BTC/ETH 上的真实样本外表现（Walk-Forward 第二阶段：含隔离 7 天、退出参数入网格、Bootstrap、扰动）\n")
for n, title in (("h1_p2", "1h（12/6/6 月）"), ("m15_p2", "15m（8/3/3 月）"), ("h4_p2", "4h（12/6/6 月，仅分析）"), ("h1_p2_rg", "1h + 训练窗环境启停"), ("m15_p2_rg", "15m + 训练窗环境启停")):
    wj = load(RD / "walkforward" / n / "walkforward.json")
    if not wj:
        L.append(f"### {title}\n\n未完成。\n"); continue
    shutil.copy(RD / "walkforward" / n / "walkforward.json", OUT / f"walkforward_{n}.json")
    for p in (RD / "walkforward" / n).glob("*_oos_trades.csv"):
        shutil.copy(p, OUT / f"walkforward_{n}_{p.name}")
    L.append(f"### {title}\n")
    L.append("| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in wj["results"].items():
        o = v["oos_stats"]; d = v["default_full_stats"]; ci = (o.get("bootstrap_mean_r_95ci") or {}); ps = (v.get("perturbation") or {}).get("summary") or {}
        L.append(f"| {k} | {len(v['folds'])} | {o.get('n_trades', 0)} | {f(o.get('net_return'), True)} | {f(o.get('expectancy_r'), d=3)} | {f(o.get('win_rate'), True, 1)} | {f(o.get('profit_factor'))} | {f(o.get('max_drawdown'), True)} | {f(o.get('sharpe'))} | {f(o.get('sortino'))} | {o.get('max_consecutive_losses', '—')} | [{f(ci.get('lower'), d=3)}, {f(ci.get('upper'), d=3)}] | {f(o.get('fold_nonneg_share'))} | {d.get('n_trades')} / {f(d.get('expectancy_r'), d=3)} / {f(d.get('profit_factor'))} | {f(ps.get('expectancy_r_min'), d=3)}~{f(ps.get('expectancy_r_max'), d=3)}（{f(ps.get('share_positive'), True, 0)}） |")
    L.append("")
    for k, v in wj["results"].items():
        o = v["oos_stats"]
        if not o.get("n_trades"):
            continue
        L.append(f"- **{k}** 年度：" + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)} 净 {f(g['net_pnl'])}" for y, g in (o.get("by_year") or {}).items())
                 + "。方向：" + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)}" for y, g in (o.get("by_direction") or {}).items())
                 + "。环境：" + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)}" for y, g in (o.get("by_regime4") or {}).items())
                 + "。标的：" + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)}" for y, g in (o.get("by_symbol") or {}).items()))
        cs = o.get("cost_sensitivity") or {}
        if cs:
            L.append(f"  - 费用敏感性（按交易记录重算）：基准 ExpR {f(cs.get('base', {}).get('expectancy_r'), d=3)}；手续费×2 {f(cs.get('fees_x2', {}).get('expectancy_r'), d=3)}；滑点×2 {f(cs.get('slip_x2', {}).get('expectancy_r'), d=3)}；滑点×3 {f(cs.get('slip_x3', {}).get('expectancy_r'), d=3)}；资金费×3 {f(cs.get('funding_x3', {}).get('expectancy_r'), d=3)}；全部×2 {f(cs.get('all_x2', {}).get('expectancy_r'), d=3)}；盈亏平衡费用倍数 {f(cs.get('breakeven_fee_multiplier'))}")
        if v.get("folds"):
            L.append("  - 各折测试：" + "；".join(f"{x['test'][0][:7]}→{x['test'][1][:7]} {x['test_stats'].get('n_trades')} 笔 ExpR {f(x['test_stats'].get('expectancy_r'), d=2)}{'(默认)' if x['fallback_to_default'] else ''}" + (f" 环境 {x['chosen_params'].get('regime_policy.' + k)}" if 'regime_policy.' + k in x['chosen_params'] else '') for x in v["folds"]))
    L.append("")

# ---------- 评估器 ----------
L.append("## G. 自动筛选结果（Strategy Evaluator）\n")
ev = load(RD / "phase2/strategy_evaluation.json")
if ev:
    shutil.copy(RD / "phase2/strategy_evaluation.json", OUT / "strategy_evaluation.json")
    L.append(f"门槛（全部样本外）：{json.dumps(ev['thresholds'], ensure_ascii=False)}\n")
    L.append("| 周期 | 策略 | 标的 | 方向 | 状态 | OOS 交易 | ExpR | PF | 净收益 | 回撤 | 均值R 95%CI | 原因 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in ev["rows"]:
        s = r["stats"]; ci = s.get("ci_mean_r", [None, None])
        L.append(f"| {r['exec_tf']} | {r['strategy']} | {r['symbol']} | {r['direction']} | **{r['status']}** | {s.get('n_trades', 0)} | {f(s.get('expectancy_r'), d=3)} | {f(s.get('profit_factor'))} | {f(s.get('net_return'), True)} | {f(s.get('max_drawdown'), True)} | [{f(ci[0], d=3)}, {f(ci[1], d=3)}] | {'；'.join(r['reasons'])} |")
    L.append(f"\n合格（模拟盘候选）：{ev['qualified_paper'] or '无'}；仅研究：{len(ev['research_only'])} 项；不合格：{len(ev['unqualified'])} 项\n")
else:
    L.append("未完成。\n")

# ---------- 审计 ----------
aj = load(RD / "audit/data_quality.json")
if aj:
    shutil.copy(RD / "audit/data_quality.json", OUT / "data_quality.json"); shutil.copy(RD / "audit/data_quality.md", OUT / "data_quality.md")
print("\n".join(L))
