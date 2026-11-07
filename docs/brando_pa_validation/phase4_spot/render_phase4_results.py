"""渲染第四阶段结果章节并复制产物到 docs/brando_pa_validation/phase4_spot/。"""
import json
import shutil
from pathlib import Path

ROOT = Path("/home/user/1hao-"); RD = ROOT / "reports_out/brando_pa/phase4_spot"; OUT = ROOT / "docs/brando_pa_validation/phase4_spot"; OUT.mkdir(parents=True, exist_ok=True)


def f(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else f"{x:.{d}f}"


pj = json.loads((RD / "phase4_results.json").read_text())
for p in RD.glob("*"):
    if p.suffix in (".json", ".md", ".png") or p.name.endswith(("_trades.csv", "_fills.csv")) or (p.name.endswith("_equity.csv") and "holdout" in p.name) or p.name.endswith("_trades.html") and "holdout" in p.name:
        shutil.copy(p, OUT / p.name)
L = []
order = ["holdout", "valid", "train", "locked_unused_1h", "full_4h"]
titles = {"holdout": "保留集（半锁定，只运行一次）2025-07 → 2026-11，1h", "valid": "验证窗 2024-07 → 2025-06，1h（描述）", "train": "训练窗 2022-04 → 2024-06，1h（描述）", "locked_unused_1h": "真正未使用集 2022-01 → 2022-03，1h（极短）", "full_4h": "4h 长历史 2018-01 → 2026-11（跨牛熊的描述性对照）"}
for w in order:
    modes = pj["windows"].get(w)
    if not modes:
        continue
    L.append(f"## {titles[w]}\n")
    for mode, r in modes.items():
        L.append(f"### {'零成本（理想研究模式）' if mode == 'zero_cost' else '含成本敏感性（手续费 10 bps + 滑点 5 bps）'}\n")
        cols = ["总收益", "年化", "最大回撤", "Sharpe", "交易次数", "胜率", "平均每笔 USDT", "PF", "ExpR", "持仓时间占比", "资金利用率", "超额 vs 对应买入持有"]
        L.append("| 组合 | " + " | ".join(cols) + " |"); L.append("|---|" + "---|" * len(cols))
        for k, v in r["rows"].items():
            es, ts, ut = v.get("equity", {}), v.get("trades", {}), v.get("util", {})
            L.append(f"| {k} | {f(es.get('total_return'), True)} | {f(es.get('cagr'), True)} | {f(es.get('max_drawdown'), True)} | {f(es.get('sharpe_daily'))} | {ts.get('n_trades', 0)} | {f(ts.get('win_rate'), True, 1)} | {f(ts.get('avg_pnl'))} | {f(ts.get('profit_factor'))} | {f(ts.get('expectancy_r'), d=3)} | {f(ut.get('time_in_market_share'), True, 0)} | {f(ut.get('avg_exposure'), True, 0)} | {f(v.get('excess_vs_bh'), True)} |")
        L.append("")
        for k, v in r["rows"].items():
            if k.startswith(("C_", "D_", "E_策略")) and v["trades"].get("n_trades"):
                ts = v["trades"]; ci = v.get("bootstrap_mean_r_95ci") or {}
                L.append(f"- **{k}**：按策略 " + "；".join(f"{kk} {vv['n']} 笔 / 胜率 {f(vv['win_rate'], True, 0)} / ExpR {f(vv['expectancy_r'], d=2)} / 净 {f(vv['net_pnl'])}" for kk, vv in (ts.get('by_strategy') or {}).items())
                         + "。退出 " + json.dumps(ts.get("by_exit"), ensure_ascii=False) + "。环境（入场时） " + "；".join(f"{kk} {vv['n']} 笔 ExpR {f(vv['expectancy_r'], d=2)} 净 {f(vv['net_pnl'])}" for kk, vv in (ts.get('by_regime4') or {}).items())
                         + "。年度 " + "；".join(f"{kk} {f(vv, True, 1)}" for kk, vv in v["yearly"].items()) + f"。买入止损距离/ATR 中位 {f(v.get('stop_atr_median'))}；均值 R 95% CI [{f(ci.get('lower'), d=3)}, {f(ci.get('upper'), d=3)}]；最长连亏 {ts.get('max_consecutive_losses')}；买入被拒原因 {json.dumps(v.get('buy_rejected_reasons'), ensure_ascii=False)}")
        if mode == "zero_cost":
            regs = {k: v.get("regime") for k, v in r["rows"].items() if v.get("regime")}
            if regs:
                keys = sorted({kk for v in regs.values() for kk in v})
                L.append("\n环境分段累计收益（按 BTC 日线环境切分同一时间轴；策略与基准同一口径）：\n")
                L.append("| 组合 | " + " | ".join(keys) + " |"); L.append("|---|" + "---|" * len(keys))
                for k, v in regs.items():
                    L.append(f"| {k} | " + " | ".join(f"{f(v.get(kk, {}).get('cum_return'), True, 1)}（{v.get(kk, {}).get('bars', 0)} 根）" for kk in keys) + " |")
        if r.get("chart"):
            L.append(f"\n图：`{Path(r['chart']).name}`\n")
        L.append("")
ls = pj.get("latest_signal", {})
L.append("## 最新信号（最后一根已收盘 1h K 线）\n")
for s, d in ls.items():
    L.append(f"- **{s}** 数据截至 {str(d.get('data_last_close'))[:16]} UTC，收盘 {d.get('price')} → **{d.get('decision')}** {d.get('strategy') or ''}；日线 {d.get('regime4')}，4h {d.get('trend_4h')}，本地阻力 {d.get('local_res')}，本地支撑 {d.get('local_sup')}；{d.get('reasons')}")
print("\n".join(L))
