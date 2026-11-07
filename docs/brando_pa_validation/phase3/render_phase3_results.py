"""渲染第三阶段结果章节并把产物复制到 docs/brando_pa_validation/phase3/。"""
import json
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path("/home/user/1hao-"); RD = ROOT / "reports_out/brando_pa/phase3"; OUT = ROOT / "docs/brando_pa_validation/phase3"; OUT.mkdir(parents=True, exist_ok=True)


def f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


pj = json.loads((RD / "phase3_results.json").read_text())
for p in RD.glob("*"):
    if p.suffix in (".json", ".md", ".png", ".csv") and (p.name.startswith(("phase3_results", "holdout_", "locked", "stop_distance", "m15_descriptive")) or p.name.endswith("_funnel.csv")):
        shutil.copy(p, OUT / p.name)
L = []
sd = pj.get("stop_diag", {})
L.append("## A. 策略 A 止损距离诊断（为什么常需要 3–4 倍 ATR）\n")
if sd:
    L.append(f"训练窗 1h 候选 {sd['n_candidates']} 个：止损/ATR 中位 {f(sd['stop_atr_all'].get('median'))}（P25–P75 {f(sd['stop_atr_all'].get('p25'))}–{f(sd['stop_atr_all'].get('p75'))}），通过过滤的中位 {f(sd['stop_atr_accepted'].get('median'))}，因“止损距离 > 4 ATR”被拒的中位 {f(sd['stop_atr_rejected_stop_distance'].get('median'))}；止损占价格中位 {f(sd['stop_pct_all'].get('median'), True)}。\n")
    dm = sd["decomposition_mean_atr"]; ds = sd["decomposition_share"]
    L.append("| 分量 | 均值（ATR） | 占比 |\n|---|---|---|")
    for k, lab in (("part_zone_height", "区域高度（止损挂在区域另一侧时）"), ("part_retest_depth", "回踩深度（止损挂在回踩极值时）"), ("part_confirm_offset", "确认偏移（确认收盘到区域边界）"), ("part_buffer", "ATR 缓冲 0.5")):
        L.append(f"| {lab} | {f(dm[k])} | {f(ds[k], True, 0)} |")
    L.append(f"| 合计（重构） | {f(dm['reconstructed_total'])} | 实际均值 {f(dm['actual_mean'])} |\n")
    L.append(f"结构点落在区域另一侧的候选占比 {f(sd.get('share_structure_on_zone_far_side'), True, 0)}；突破→回踩中位 {sd['timing'].get('bars_armed_to_retest_median')} 根、回踩→确认中位 {sd['timing'].get('bars_retest_to_confirm_median')} 根。按区域周期：" + "；".join(f"{k} 止损中位 {f(v.get('median'))} ATR（区域高度中位 {f(v.get('zone_height_atr_median'))} ATR，n={v.get('n')}）" for k, v in sd["by_zone_tfs"].items()) + "。\n")
    tr = sd.get("trades", {})
    if tr:
        L.append(f"成交后（训练窗 {tr['n']} 笔）：止损占价格中位 {f(tr['stop_pct_median'], True)}；亏损交易 MAE/止损中位 {f(tr['losers_mae_r_median'])}R、被扫损占 {f(tr['losers_stopped_share'], True, 0)}；赢家 MFE/止损中位 {f(tr['winners_mfe_r_median'])}R；MAE 在 0.5–1R 之间的亏损占 {f(tr.get('share_losers_mae_between_0_5_and_1'), True, 0)}。\n")
    L.append("诊断结论：" + "；".join(sd["verdict"]) + f"\n\n图：`{Path(sd['charts'][0]).name}`（已复制到本目录）\n")
L.append("## B. 原策略 A 与两种新入场模式：训练 / 验证（止损模式选择）\n")
L.append("| 策略 | 止损模式 | 训练 n | 训练 ExpR | 训练 PF | 训练 候选止损ATR中位 | 验证 n | 验证 ExpR | 验证 PF | 验证 回撤 | 验证 被扫损 | 验证 亏损中 MAE 0.5–1R 占比 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
for sk, d in pj["train_valid"].items():
    for sm, r in d.items():
        t, v = r["train"], r["valid"]
        L.append(f"| {sk} | {sm} | {t.get('n_trades', 0)} | {f(t.get('expectancy_r'), d=3)} | {f(t.get('profit_factor'))} | {f(t.get('stop_atr_median_all'))} | {v.get('n_trades', 0)} | {f(v.get('expectancy_r'), d=3)} | {f(v.get('profit_factor'))} | {f(v.get('max_drawdown'), True)} | {f(v.get('stopped_share'), True, 0)} | {f(v.get('losers_mae_between_0_5_and_1R_share'), True, 0)} |")
L.append("\n选定止损模式（按验证窗 ExpR；验证 < 10 笔退回预登记默认）：" + "；".join(f"{k}: **{v['chosen']}**{'（退回默认）' if v['fallback_to_default'] else ''}，验证 ExpR 按模式 { {m: (round(x, 3) if x is not None else None) for m, x in v['valid_expectancy_by_mode'].items()} }" for k, v in pj["selected_stop_mode"].items()) + "\n")
L.append("## C. 锁定测试（只运行一次；L2 为半锁定保留集，L1 为真正未使用集）\n")
L.append("| 集合 | 策略 | 止损 | n | ExpR | 胜率 | PF | 净收益 | 最大回撤 | Sharpe | 均值R 95%CI | 候选→通过 | 止损/ATR 中位（候选） |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for name, d in (("L2 2025-07→2026-11 (1h)", pj["holdout"]), ("L1 真正未使用", pj["locked"])):
    for sk, r in d.items():
        ci = r.get("bootstrap_mean_r_95ci") or {}
        L.append(f"| {name} | {sk} | {r.get('stop_mode', '')} | {r.get('n_trades', 0)} | {f(r.get('expectancy_r'), d=3)} | {f(r.get('win_rate'), True, 1)} | {f(r.get('profit_factor'))} | {f(r.get('net_return'), True)} | {f(r.get('max_drawdown'), True)} | {f(r.get('sharpe'))} | [{f(ci.get('lower'), d=3)}, {f(ci.get('upper'), d=3)}] | {r.get('candidates')}→{r.get('accepted')} | {f(r.get('stop_atr_median_all'))} |")
L.append("")
for sk, r in pj["holdout"].items():
    if r.get("n_trades"):
        L.append(f"- **{sk}（保留集）** 年度 " + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)}" for y, g in (r.get('by_year') or {}).items()) + "。方向 " + "；".join(f"{y} {g['n']} 笔 ExpR {f(g['expectancy_r'], d=2)}" for y, g in (r.get('by_direction') or {}).items()) + "。退出原因 " + json.dumps({k: v['n'] for k, v in (r.get('by_exit_reason') or {}).items()}, ensure_ascii=False) + f"。被拒原因 {r.get('first_fail')}")
L.append("\n## D. 保留集：标的 × 市场环境 × 方向（平均 R，BTC/ETH 分开，多空分开）\n")
L.append("| 策略 | 标的/环境/方向 | n | ExpR | 胜率 | 净盈亏 |\n|---|---|---|---|---|---|")
for sk, r in pj["holdout"].items():
    for k, v in sorted((r.get("by_symbol_regime_direction") or {}).items()):
        L.append(f"| {sk} | {k} | {v['n']} | {f(v['expectancy_r'], d=2)} | {f(v['win_rate'], True, 0)} | {f(v['net_pnl'])} |")
L.append("\n## E. 成本压力测试（保留集；手续费 10 bps / 滑点 10 + 止损滑点 20 bps / 价差 4 bps / 资金费 ×3）\n")
L.append("| 策略 | 场景 | n | ExpR | PF | 净收益 | 回撤 | 手续费 | 滑点 | 资金费 |\n|---|---|---|---|---|---|---|---|---|---|")
for sk, d in pj["stress"].items():
    base = pj["holdout"][sk]
    L.append(f"| {sk} | 基线 | {base.get('n_trades')} | {f(base.get('expectancy_r'), d=3)} | {f(base.get('profit_factor'))} | {f(base.get('net_return'), True)} | {f(base.get('max_drawdown'), True)} | {f(base.get('total_fees'))} | {f(base.get('total_slippage'))} | {f(base.get('total_funding'))} |")
    for n, r in d.items():
        L.append(f"| {sk} | {n} | {r.get('n_trades')} | {f(r.get('expectancy_r'), d=3)} | {f(r.get('profit_factor'))} | {f(r.get('net_return'), True)} | {f(r.get('max_drawdown'), True)} | {f(r.get('total_fees'))} | {f(r.get('total_slippage'))} | {f(r.get('total_funding'))} |")
m15p = RD / "m15_descriptive.json"
if m15p.exists():
    m15 = json.loads(m15p.read_text())
    L.append("\n## F. 15m 描述性对照（2025-02 → 2026-11，仅 22 个月，不参与选型、不计入判定）\n")
    L.append("| 策略 | 止损 | n | ExpR | PF | 净收益 | 回撤 | 候选→通过 | 止损/ATR 中位 |\n|---|---|---|---|---|---|---|---|---|")
    for sk, r in m15.items():
        L.append(f"| {sk} | {r.get('stop_mode')} | {r.get('n_trades', 0)} | {f(r.get('expectancy_r'), d=3)} | {f(r.get('profit_factor'))} | {f(r.get('net_return'), True)} | {f(r.get('max_drawdown'), True)} | {r.get('candidates')}→{r.get('accepted')} | {f(r.get('stop_atr_median_all'))} |")
# 成交率 / 订单事件
L.append("\n## G. 成交模型审计：订单事件（保留集）\n")
for sk in pj["holdout"]:
    p = RD / f"holdout_{sk}_order_events.csv"
    if p.exists() and p.stat().st_size > 10:
        ev = pd.read_csv(p)
        L.append(f"- {sk}：" + json.dumps(ev["event"].value_counts().to_dict(), ensure_ascii=False))
print("\n".join(L))
