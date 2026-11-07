"""渲染第六阶段结果章节（从 phase6_results.json 自动生成，不手填数字）。"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path("/home/user/1hao-"); RD = ROOT / "reports_out/brando_pa/phase6_spot"
pj = json.loads((RD / "phase6_results.json").read_text())


def f(x, pct=False, d=1):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    return f"{x*100:.{d}f}%" if pct else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


L = []
# ---- 审计 ----
L.append("## 1. SMA200 基准审计（§一）\n")
L.append(f"审计窗口 {pj['audit_window'][0]} → {pj['audit_window'][1]}（第五阶段结果所用数据的截止时刻），10,000 USDT。三套实现：第五阶段 `sma200_benchmark`、本阶段纯 numpy 日线级独立复算、本阶段三层引擎（策略 A + 无熔断）。\n")
L.append("| 标的 | 第五阶段 | 独立复算 | 三层引擎 | 逐根最大相对差 | 买入次数 | 复现 | 首个 SMA200 日 | 首笔成交 | 成交均在日线收盘后首根 1h 开盘 | 成交价 = 该根开盘 | 现金最小 | 持仓最小 | 敞口最大 | 截断一致 | 若误用未收盘日线 | 含成本(10+5bps) 三套一致 | 手续费核对 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for s, a in pj["audit"].items():
    ft = a.get("fill_timing", {}); wc = a["with_cost_10_5"]
    L.append(f"| {s} | {f(a['phase5_benchmark']['total_return'], True)} | {f(a['independent_reimpl']['total_return'], True)} | {f(a['trendsys_A_none']['total_return'], True)} | {a['max_abs_rel_diff_phase5_vs_independent']:.1e} | {a['phase5_benchmark']['n_buys']} | {f(a.get('reproduced'))} | {a['warmup']['first_sma_day']} | {str(a['warmup']['first_fill'])[:10]} | {f(ft.get('all_fills_at_first_bar_after_daily_close'))} | {f(ft.get('fill_price_equals_next_open'))} | {a['ledger']['cash_min']:.1f} | {a['ledger']['qty_min']:.0f} | {f(a['ledger']['exposure_max'], True, 2)} | {f(a['lookahead']['truncation_identical'])} | {f(a['lookahead']['if_used_unclosed_daily_bar_total_return'], True, 0)} | {f(wc['phase5_benchmark']['total_return'], True)} / {f(wc['independent_reimpl']['total_return'], True)} / {f(wc['trendsys_A_none']['total_return'], True)} | {f(wc['fees']['ok'])}（{wc['fees']['fees_paid']:.0f} USDT） |")
L.append("")
L.append("持仓段内回撤（第五阶段基准成交记录）：")
for s, a in pj["audit"].items():
    h = a.get("hold_segments", {})
    L.append(f"- {s}：{h.get('n')} 段持仓，平均 {f(h.get('avg_days'))} 天；段内最大回撤最差 {f(h.get('worst_max_dd_in_hold'), True)}、中位 {f(h.get('median_max_dd_in_hold'), True)}；相对买入价的最大不利偏移最差 {f(h.get('worst_mae_vs_entry'), True)}；{f(h.get('share_segments_negative'), True, 0)} 的持仓段以亏损结束（典型的趋势跟随分布：多数小亏、少数大赚）。")
L.append("")
# ---- 结果表 ----
names = {"A_sma200": "A 原始SMA200", "B_sma200_confirm3": "B 3日确认", "original_permanent": "原规则(永久)", "state_machine": "状态机", "none": "无熔断"}


def nice(n):
    for k, v in names.items():
        n = n.replace(k, v)
    return n


cols = [("总收益", "total_return", True), ("年化", "cagr", True), ("最大回撤", "max_drawdown", True), ("Sharpe", "sharpe", False), ("Calmar", "calmar", False), ("卖出", "n_trades", None), ("平均仓位", "avg_exposure", True), ("持仓时间", "time_in_market", True), ("上涨月捕获", "up_month_capture", True), ("下跌月捕获", "down_month_capture", True), ("熔断", "n_halts", None), ("禁买占比", ("halt_episodes", "share"), True), ("最长禁买天", ("halt_episodes", "longest_days"), False), ("超额 vs B&H", "excess_vs_bh", True)]
order = ["full_1h", "holdout", "valid", "train", "locked_unused_1h", "full_4h"]
L.append("## 2. 配置矩阵结果（§三、§四、§六、§七）\n")
L.append("窗口标签：" + "；".join(f"{w} = {pj['window_labels'].get(w)}" for w in order) + f"。数据截止：1h {pj['data_end']['1h']['BTCUSDT']}，4h {pj['data_end']['4h']['BTCUSDT']}（UTC）。\n")
for w in order:
    d = pj["windows"].get(w)
    if not d:
        continue
    L.append(f"### {w}（{d['label']}）· {d['period'][0]} → {d['period'][1] or '最新'} · 执行 {d['exec_tf']}\n")
    for rnd in ("round1", "round2"):
        L.append(f"**{'零成本' if rnd == 'round1' else '手续费 10 bps + 滑点 5 bps'}**\n")
        L.append("| 组合 | " + " | ".join(c[0] for c in cols) + " |"); L.append("|---|" + "---|" * len(cols))
        for r in d[rnd]["rows"]:
            vals = []
            for _, k, pct in cols:
                v = (r.get(k[0]) or {}).get(k[1]) if isinstance(k, tuple) else r.get(k)
                vals.append(str(v) if pct is None or v is None or isinstance(v, bool) else f(v, pct, 2 if pct else 2))
            L.append(f"| {nice(r['name'])} | " + " | ".join(vals) + " |")
        L.append("")
    if d.get("chart"):
        L.append(f"图：`{Path(d['round1']['chart']).name}`；状态机时间线：`{Path(d['state_chart']).name}`\n") if d.get("state_chart") else None
# ---- 敏感性 ----
L.append("### 事后敏感性（只报告，不用于选择）：策略 B 的确认日数 ±1（full_1h 零成本、无熔断）\n")
L.append("| 组合 | 总收益 | 最大回撤 | 卖出 | 上涨月捕获 |"); L.append("|---|---|---|---|---|")
for k, v in pj["sensitivity"].items():
    L.append(f"| {v['name']} | {f(v['total_return'], True)} | {f(v['max_drawdown'], True)} | {v['n_trades']} | {f(v['up_month_capture'], True, 0)} |")
L.append("")
# ---- 状态机细节 ----
L.append("## 3. 熔断状态机：full_1h 零成本的状态轨迹（§四）\n")
for a in ("BTC", "ETH", "EW"):
    p = RD / f"full_1h_round1_{a}_A_sma200_state_machine_transitions.csv"
    if p.exists():
        t = pd.read_csv(p); h = t[t["to"].isin(["EMERGENCY_HALT", "COOLDOWN", "RECOVERY"]) | ((t["from"] == "RECOVERY") & (t["to"] == "NORMAL"))]
        row = [r for r in pj["windows"]["full_1h"]["round1"]["rows"] if r["name"] == f"{a}|A_sma200|state_machine"][0]
        L.append(f"- **{a} · 策略 A**：{len(t)} 次状态切换，其中 EMERGENCY_HALT {int((t['to'] == 'EMERGENCY_HALT').sum())} 次；状态时间占比 {row['risk_state_share']}；退出原因 {row['exit_reasons']}；最长禁买 {row['halt_episodes']['longest_days']:.0f} 天（原规则 {[r for r in pj['windows']['full_1h']['round1']['rows'] if r['name'] == f'{a}|A_sma200|original_permanent'][0]['halt_episodes']['longest_days']:.0f} 天且期末仍在禁买）。")
        for _, r in h.iterrows():
            L.append(f"  - {str(r['ts'])[:10]} {r['from']} → {r['to']}（权益 {r['equity']:.0f}；{r['why']}）")
L.append("")
# ---- 交易频率 ----
L.append("## 4. 交易频率与卖出信号（§五）\n")
L.append("### 4.1 本阶段策略（full_1h 零成本，单标的）\n")
L.append("| 组合 | 卖出次数 | 每年 | 持仓中位天 | <7天占比 | 卖出盈利占比 | 再买占比 | 再买中位间隔天 | 再买价更高占比 | 保护性 | 来回磨损 | 中性 |"); L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for a in ("BTC", "ETH"):
    for st in ("A_sma200", "B_sma200_confirm3"):
        for rk in ("none", "state_machine"):
            row = [r for r in pj["windows"]["full_1h"]["round1"]["rows"] if r["name"] == f"{a}|{st}|{rk}"][0]; ss = row["sell_summary"]; bc = ss.get("by_category", {})
            L.append(f"| {nice(row['name'])} | {ss.get('n_sells')} | {f(ss.get('per_year'))} | {f(ss.get('median_hold_days'))} | {f(ss.get('share_hold_lt_7d'), True, 0)} | {f(ss.get('win_share'), True, 0)} | {f(ss.get('rebuy_share'), True, 0)} | {f(ss.get('median_days_to_rebuy'))} | {f(ss.get('rebuy_higher_share'), True, 0)} | {bc.get('protective', {}).get('n', 0)} | {bc.get('whipsaw', {}).get('n', 0)} | {bc.get('neutral', {}).get('n', 0)} |")
L.append("")
L.append("### 4.2 第五阶段模型 B 战术袖子 / 原 Brando（full_1h，历史产物只读）\n")
L.append("| 产物 | 平仓笔数 | 每年 | 持仓中位天 | <1天占比 | <7天占比 | 胜率 | 净盈亏 USDT | 手续费 USDT | 退出原因 | 卖后再买占比 | 再买中位间隔天 | 再买价更高占比 | 保护性 / 来回磨损 / 中性 |"); L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in pj["phase5_tactical_frequency"].items():
    bc = v["by_category"]
    L.append(f"| {k.replace('B_core_satellite', '模型B战术袖子').replace('BRANDO_original', '原Brando').replace('round1', '零成本').replace('round2', '含成本')} | {v['n_trades']} | {f(v['per_year'], d=0)} | {f(v['median_hold_days'], d=2)} | {f(v['share_hold_lt_1d'], True, 0)} | {f(v['share_hold_lt_7d'], True, 0)} | {f(v['win_share'], True, 0)} | {v['net_pnl_sum']:.0f} | {f(v.get('fees_sum'), d=0)} | {v['by_exit']} | {f(v['rebuy_share'], True, 0)} | {f(v['median_days_to_rebuy'], d=2)} | {f(v['rebuy_higher_share'], True, 0)} | {bc['protective']} / {bc['whipsaw']} / {bc['neutral']} |")
L.append("")
# ---- 验收 ----
L.append("## 5. 预登记验收判据逐项结果（§九）\n")
acc = pj["acceptance"]
L.append("| 判据 | 结果 | 关键数字 |"); L.append("|---|---|---|")
def _brief(det):
    out = []
    for a, dd in det.items():
        if isinstance(dd, dict) and dd and all(isinstance(x, dict) for x in dd.values()):
            out.append(f"**{a}** → " + "；".join(_brief({aa: x}) for aa, x in dd.items()))
        elif isinstance(dd, dict):
            out.append(f"{a}: " + ", ".join(f"{kk}={f(vv, kk.startswith(('ret', 'dd', 'up_capture')), 1) if isinstance(vv, float) else vv}" for kk, vv in dd.items() if kk not in ("windows",) and not isinstance(vv, dict)))
    return "；".join(out)


for k, v in acc["criteria"].items():
    L.append(f"| {k} | {'通过' if v['pass'] else '未通过'}{'（' + v['note'] + '）' if v.get('note') else ''} | {_brief(v.get('detail', {}))[:900]} |")
L.append("")
L.append(f"**最终选择**：趋势层 `{acc['final']['trend_layer']}`；受保护默认的风险层 `{acc['final']['risk_layer_default']}`（通过判据：{f(acc['final']['risk_layer_default_passed_criteria'])}）；收益最高变体：{acc['final']['highest_return_variant']}。{acc['final']['statement']}\n")
print("\n".join(L))
