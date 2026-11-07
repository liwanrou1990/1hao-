"""渲染第七阶段结果章节（从 phase7_results.json 自动生成）。"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path("/home/user/1hao-"); RD = ROOT / "reports_out/brando_pa/phase7_usdt"
pj = json.loads((RD / "phase7_results.json").read_text())
rc = pj["risk_constraint"]


def u(x):
    return "—" if x is None else f"{x:,.0f}"


def p(x, d=1):
    return "—" if x is None else f"{x*100:.{d}f}%"


def nm(n):
    return n.replace("A_equal_weight", "模型A等权").replace("B_full_single", "模型B满额").replace("C_cash_reserve", "模型C现金储备").replace("original_permanent", "原规则").replace("state_machine", "状态机").replace("|none", "|无熔断")


COLS = [("初始", "initial_usdt", u), ("期末权益", "final_equity_usdt", u), ("期末清仓", "liquidation_usdt", u), ("净利润", "net_profit_usdt", u), ("可实现利润", "realizable_profit_usdt", u), ("收益率", "total_return", p), ("最大回撤", "max_drawdown", p), ("回撤USDT", "max_drawdown_usdt", u), ("交易", "n_trades", lambda x: "—" if x is None else str(int(x))),
        ("均仓位", "avg_exposure", p), ("BTC贡献", "contrib_btc_usdt", u), ("ETH贡献", "contrib_eth_usdt", u), ("成本", "cost_usdt", u), ("vs BTC SMA200", "excess_vs_btc_sma200_usdt", u), ("约束", "meets_risk_constraint", lambda x: "是" if x else "否"), ("独立验证", "independently_validated", lambda x: "是" if x else "否"),
        ("最长空仓天", "longest_all_cash_days", lambda x: "—" if x is None else f"{x:.0f}"), ("最大连亏", "max_consecutive_losses", lambda x: "—" if x is None else str(int(x))), ("上涨月捕获", "up_month_capture", p), ("熔断", "n_halts", lambda x: "—" if x is None else str(int(x))), ("最长禁买天", "longest_no_buy_days", lambda x: "—" if x is None else f"{x:.0f}")]


def table(rows):
    L = ["| 策略 | " + " | ".join(c[0] for c in COLS) + " |", "|---|" + "---|" * len(COLS)]
    for r in rows:
        L.append(f"| {nm(r['name'])} | " + " | ".join(f_(r.get(k)) for _, k, f_ in COLS) + " |")
    return "\n".join(L)


order = ["full_1h", "holdout", "valid", "train", "locked_unused_1h", "full_4h"]
L = [f"所有金额为 USDT；风险约束占位：最大回撤 ≤ {rc['max_drawdown']:.0%}（approved_by_user = {rc['approved_by_user']}，未经批准，不得据此宣称适合真实交易）。数据截止：1h {pj['data_end']['1h']['BTCUSDT']}，4h {pj['data_end']['4h']['BTCUSDT']}（UTC）。\n"]
for w in order:
    d = pj["windows"].get(w)
    if not d:
        continue
    L.append(f"## {w}（{d['label']}）· {d['period'][0]} → {d['period'][1] or '最新'} · 执行 {d['exec_tf']}\n")
    for rnd in ("round1", "round2"):
        L.append(f"### {'零成本（理想研究环境，不是可实际获得的收益）' if rnd == 'round1' else '手续费 10 bps + 滑点 5 bps（压力假设）'}\n")
        L.append("**满足风险约束占位（按期末清仓 USDT 排序）**\n"); L.append(table(d[rnd]["leaderboard_ok"])); L.append("")
        L.append("**不满足风险约束占位（单独展示，不隐藏）**\n"); L.append(table(d[rnd]["leaderboard_fail"]) if d[rnd]["leaderboard_fail"] else "（无）"); L.append("")
        rm = d[rnd]["regime_months"]
        cats = ["both_up", "both_down", "btc_up_eth_down", "eth_up_btc_down", "eth_up_btc_flat", "btc_up_eth_flat", "other"]
        L.append("**月度环境诊断（各类月份 USDT 盈亏之和；月数 / BTC、ETH 平均月收益）**\n")
        L.append("| 曲线 | " + " | ".join(f"{c_}（{rm['n_months'].get(c_, 0)} 月；BTC {p(rm['avg_btc_ret'].get(c_), 0)} / ETH {p(rm['avg_eth_ret'].get(c_), 0)}）" for c_ in cats if rm["n_months"].get(c_)) + " |")
        L.append("|---|" + "---|" * len([c_ for c_ in cats if rm["n_months"].get(c_)]))
        for name, v in rm["pnl_usdt"].items():
            L.append(f"| {nm(name)} | " + " | ".join(u(v.get(c_)) for c_ in cats if rm["n_months"].get(c_)) + " |")
        L.append("")
    L.append(f"图：`{Path(d['round1']['chart']).name}`" + (f"；仓位图 `{Path(d['position_chart']).name}`；贡献图 `{Path(d['contrib_chart']).name}`" if d.get("position_chart") else "") + "\n")
print("\n".join(L))
