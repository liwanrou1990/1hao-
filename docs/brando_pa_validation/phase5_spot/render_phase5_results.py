"""渲染第五阶段结果章节，并复制关键产物到 docs/brando_pa_validation/phase5_spot/。"""
import json
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path("/home/user/1hao-"); RD = ROOT / "reports_out/brando_pa/phase5_spot"; OUT = ROOT / "docs/brando_pa_validation/phase5_spot"; OUT.mkdir(parents=True, exist_ok=True)


def f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


pj = json.loads((RD / "phase5_results.json").read_text())
for p in RD.glob("*"):
    if p.suffix in (".json", ".md", ".png") or p.name.startswith("regime_daily") or (("holdout" in p.name or "full_1h" in p.name or "full_4h" in p.name) and p.name.endswith("_trades.csv")):
        shutil.copy(p, OUT / p.name)
L = []
L.append("## 趋势状态引擎输出\n")
for s, rs in pj.get("regime_summary", {}).items():
    L.append(f"- **{s}**：状态分布 {rs['state_counts']}；切换 {rs['transitions']} 次；各年 BULL 占比 " + "，".join(f"{k} {float(v):.0%}" for k, v in rs["bull_share_by_year"].items()))
L.append("")
order = ["full_1h", "holdout", "valid", "train", "locked_unused_1h", "full_4h"]
titles = {"full_1h": "全周期 1h：2022-04 → 2026-11", "holdout": "保留窗 1h：2025-07 → 2026-11（半锁定）", "valid": "验证窗 1h：2024-07 → 2025-06", "train": "训练窗 1h：2022-04 → 2024-06", "locked_unused_1h": "真正未使用 1h：2022-01 → 2022-03（极短）", "full_4h": "长历史 4h：2018-06 → 2026-11（跨牛熊）"}
cols = [("总收益", "total_return", True), ("年化", "cagr", True), ("最大回撤", "max_drawdown", True), ("Sharpe", "sharpe", False), ("Calmar", "calmar", False), ("交易", "n_trades", None), ("平均仓位", "avg_exposure", True), ("BULL期持仓", "bull_time_in_market_share", True), ("上涨月捕获", "up_month_capture", True), ("下跌月捕获", "down_month_capture", True), ("卖后错过合计", "missed_after_sell_sum", True), ("止损后再入", "reentries_after_stop", None), ("均持仓天", "avg_hold_days", False), ("超额 vs B&H", "excess_vs_bh", True), ("低回撤因空仓", "low_dd_due_to_idle_cash", None)]
for w in order:
    d = pj["windows"].get(w)
    if not d:
        continue
    L.append(f"## {titles[w]}\n")
    for rnd in ("round1", "round2"):
        if rnd not in d:
            continue
        L.append(f"### {'第一轮：零成本（理想研究环境）' if rnd == 'round1' else '第二轮：手续费 10 bps + 滑点 5 bps'}\n")
        L.append("| 组合 | " + " | ".join(c[0] for c in cols) + " |"); L.append("|---|" + "---|" * len(cols))
        for r in d[rnd]["rows"]:
            vals = [f(r.get(k), pct) if pct is not None else f(r.get(k)) for _, k, pct in cols]
            L.append(f"| {r['name']} | " + " | ".join(vals) + " |")
        L.append("")
        if rnd == "round1":
            for r in d[rnd]["rows"]:
                if r["name"].startswith(("7_", "8_", "9_", "10_")) and r.get("n_trades"):
                    L.append(f"- {r['name']}：年度 " + "，".join(f"{k} {f(v, True, 1)}" for k, v in (r.get('yearly') or {}).items()) + f"；退出 {json.dumps(r.get('exit_reasons'), ensure_ascii=False)}；计数 {json.dumps(r.get('counters'), ensure_ascii=False)}")
            if d[rnd].get("chart"):
                L.append(f"\n图：`{Path(d[rnd]['chart']).name}`\n")
        L.append("")
rec = pj.get("phase4_btc_valid_reconstruction")
if rec:
    ex = rec["exposure"]; sm = rec["reconstruction"]["summary"]
    L.append("## 为什么 BTC 验证窗买入持有 +73% 而第四阶段原策略只有 +7.9%（逐笔还原）\n")
    L.append(f"- 持仓时间占比 {f(ex.get('time_in_market_share'), True, 0)}，平均仓位 {f(ex.get('avg_exposure'), True, 0)}，BULL 期间持仓时间占比 {f(ex.get('bull_time_in_market_share'), True, 0)}；最长连续空仓 {f(ex.get('idle_longest_days'))} 天；上涨月捕获 {f(ex.get('up_month_capture'), True, 0)}，下跌月捕获 {f(ex.get('down_month_capture'), True, 0)}。")
    L.append(f"- 卖出 {ex.get('missed_after_sell_n')} 次，卖出后到下一次买入期间标的平均变动 {f(ex.get('missed_after_sell_mean'), True)}，合计 {f(ex.get('missed_after_sell_sum'), True)}；止损/跟踪止损后重新买入 {ex.get('reentries_after_stop')} 次；平均持仓 {f(ex.get('avg_hold_days'))} 天。")
    if sm:
        L.append(f"- 空仓段 {sm.get('flat', {}).get('segments')} 段共 {f(sm.get('flat', {}).get('days'))} 天，期间标的复合变动 {f(sm.get('flat', {}).get('asset_move_compounded'), True)}；持仓段 {sm.get('hold', {}).get('segments')} 段共 {f(sm.get('hold', {}).get('days'))} 天，期间标的复合变动 {f(sm.get('hold', {}).get('asset_move_compounded'), True)}；标的全程 {f(sm.get('asset_total_move'), True)}。退出原因 {rec.get('trades_by_exit')}；按策略 {json.dumps(rec.get('trades_by_strategy'), ensure_ascii=False, default=str)}。")
    segs = pd.DataFrame(rec["reconstruction"]["segments"])
    if not segs.empty:
        big = segs[segs["kind"] == "flat"].sort_values("asset_move", ascending=False).head(5)
        L.append("- 错过最多的空仓段：" + "；".join(f"{r['from']}→{r['to']}（{r['days']:.1f} 天，标的 {r['asset_move']:+.1%}）" for _, r in big.iterrows()))
    L.append("")
print("\n".join(L))
