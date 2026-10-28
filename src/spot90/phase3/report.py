"""第三阶段 Markdown 报告（docs/16）：BTC / ETH × 7/14/30/90 天 真实样本外成绩（选择期 / 留出期分列）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..config import PROJECT_ROOT, report_dir
from .analysis import BONF, CONFIGS, N_COMPARISONS
from .labels import HORIZONS

NAMES = {"E_baserate": "E 无条件频率", "E_trendvol": "E 趋势×波动频率", "A_lightgbm": "A 技术 LGBM", "A_catboost": "A 技术 CatB",
         "B_lightgbm": "B +交易所流量 LGBM", "B_catboost": "B +交易所流量 CatB", "C_lightgbm": "C +MVRV/活跃度 LGBM", "C_catboost": "C +MVRV/活跃度 CatB"}


def pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x*100:.{d}f}%"


def num(x, d=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def ci(c: dict | None, key="ci950", scale=1.0, d=4, as_pct=False):
    if not c or key not in c:
        return "—" if not c or c.get("mean") is None else (pct(c["mean"]) if as_pct else num(c["mean"], d)) + "（样本不足）"
    lo, hi = c[key]
    f = (lambda v: pct(v)) if as_pct else (lambda v: f"{v*scale:+.{d}f}")
    return f"{f(c['mean'])} [{f(lo)}, {f(hi)}]"


def build(diag: dict, out_path: Path | None = None) -> Path:
    A = json.loads((report_dir() / "phase3" / "analysis.json").read_text())
    B = json.loads((report_dir() / "phase3" / "buyposition.json").read_text())
    bk = f"ci{int(round(BONF*1000))}"
    L: list[str] = []
    L.append("# 16 · 第三阶段：多周期（7 / 14 / 30 / 90 天）市场预测有效性研究 —— BTC、ETH 真实样本外成绩\n")
    L.append(f"> Purged Walk-Forward：2020-01-01 起 14 折、每折 180 天，训练集只含“标签在测试起点前 H+10 天已完全实现”的决策日；**选择期** 2020-01-01 → 2025-06-02（折 0-10，用于一切模型/特征选择），**留出期** 2025-06-03 → 2026-07-15（折 11-13，未参与第三阶段任何选择；但其价格已出现在第一、二阶段报告中，不能视为完全盲测）。重叠窗口：块自助（块长 H+10 天）、分批持仓权益。多重比较：{N_COMPARISONS} 次 ML-vs-基准比较，Bonferroni 水平 {BONF*100:.2f}%。全部成本：手续费 10 bps/边 + 市价滑点 5 bps。D 组（衍生品）因无真实历史数据未运行。\n")
    # 诊断
    L.append("## 1. 对前两阶段失败原因的检查\n")
    L.append("| 检查项 | 结论 | 处理 |\n|---|---|---|")
    for row in diag:
        L.append(f"| {row[0]} | {row[1]} | {row[2]} |")
    L.append("")
    # 每周期
    for sym in ("BTCUSDT", "ETHUSDT"):
        L.append(f"## 2.{1 if sym=='BTCUSDT' else 2} {sym[:3]}：各周期样本外成绩\n")
        for H in HORIZONS:
            h = A["horizons"].get(str(H)) or A["horizons"].get(H)
            if not h:
                continue
            L.append(f"### {sym[:3]} · {H} 天\n")
            L.append("| 配置 | 期间 | n | 上涨 AUC 原始/校准 | Brier 校准 | LogLoss | ECE | 浮亏>10% AUC | >15% AUC | >20% AUC | 90% 区间覆盖 | Brier 差 vs E趋势波动（95% CI） | Bonferroni CI |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for cfg in CONFIGS:
                c = h["configs"].get(cfg)
                if not c:
                    continue
                for period in ("selection", "holdout"):
                    e = c.get(f"{sym}|{period}", {})
                    if "profit_cal" not in e:
                        continue
                    pr, pc = e["profit"], e["profit_cal"]
                    pv = h["paired_vs_E_trendvol"].get(cfg, {}).get(f"{sym}|{period}", {}).get("brier_profit")
                    L.append(f"| {NAMES[cfg]} | {'选择期' if period=='selection' else '留出期'} | {e['n']} | {num(pr['auc'])} / {num(pc['auc'])} | {num(pc['brier'],4)} | {num(pc['logloss'])} | {num(pc['ece'])} | "
                             f"{num(e.get('mae10_cal',{}).get('auc'))} | {num(e.get('mae15_cal',{}).get('auc'))} | {num(e.get('mae20_cal',{}).get('auc'))} | {pct(e.get('coverage_90'),0)} | {ci(pv)} | {ci(pv, bk)} |")
            L.append("")
            L.append(f"策略（校准后上涨概率 ≥ 50% 买入持有 {H} 天，否则现金）vs 每天买入：\n")
            L.append("| 配置 | 期间 | 买入日占比 | 成交后胜率 | 均值收益 | 平均最大浮亏 | 浮亏>15% | 全部日期均值 | 每天买入均值 | 差值（95% CI） | 分批组合年化收益 / 波动 / Sharpe / 最大回撤 | 每天买入年化 / Sharpe / 最大回撤 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
            for cfg in CONFIGS:
                c = h["configs"].get(cfg)
                if not c:
                    continue
                for period in ("selection", "holdout"):
                    s = c.get(f"{sym}|{period}", {}).get("strategy")
                    if not s:
                        continue
                    eq = s["equity"].get(sym, {}); st, bh = eq.get("strategy", {}), eq.get("buyhold", {})
                    L.append(f"| {NAMES[cfg]} | {'选择期' if period=='selection' else '留出期'} | {pct(s['share_buy'],0)} | {pct(s['positions_win_rate'])} | {pct(s['positions_mean_ret'])} | {pct(s['positions_mean_mae'])} | {pct(s['positions_p_mae15'],0)} | {pct(s['all_days_mean_ret'])} | {pct(s['buyhold_mean_ret'])} | {ci(s['diff_vs_buyhold'], as_pct=True)} | "
                             f"{pct(st.get('ann_return'))} / {pct(st.get('ann_vol'))} / {num(st.get('sharpe'),2)} / {pct(st.get('max_drawdown'))} | {pct(bh.get('ann_return'))} / {num(bh.get('sharpe'),2)} / {pct(bh.get('max_drawdown'))} |")
            L.append("")
    # 选择 → 留出
    L.append("## 3. 预先登记的选择流程：选择期最优配置在留出期的表现\n")
    L.append("| 标的 | 周期 | 选择期最优配置 | 选择期 Brier（配置 / E） | 选择期 AUC（配置 / E） | 选择期 Brier 差 95% CI | 留出期 Brier（配置 / E） | 留出期 AUC（配置 / E） | 留出期 Brier 差 | 通过(95%) | 通过(Bonferroni) |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for sym in ("BTCUSDT", "ETHUSDT"):
        for H in HORIZONS:
            h = A["horizons"].get(str(H)) or A["horizons"].get(H)
            s = (h or {}).get("selection", {}).get(sym)
            if not s:
                continue
            L.append(f"| {sym[:3]} | {H} | {NAMES[s['best_config']]} | {num(s['selection_brier'],4)} / {num(s['E_selection_brier'],4)} | {num(s['selection_auc'])} / {num(s['E_selection_auc'])} | {ci(s['paired_selection'])} | "
                     f"{num(s['holdout_brier'],4)} / {num(s['E_holdout_brier'],4)} | {num(s['holdout_auc'])} / {num(s['E_holdout_auc'])} | {ci(s['paired_holdout'])} | {'是' if s['validated_95'] else '否'} | {'是' if s['validated_bonferroni'] else '否'} |")
    L.append("")
    # 买入位置
    L.append("## 4. 以 90 天持有为目标的买入位置：同决策日、同资金口径\n")
    for sym in ("BTCUSDT", "ETHUSDT"):
        L.append(f"### {sym[:3]}（选择期 n={B['n'].get(sym+'|selection')}，留出期 n={B['n'].get(sym+'|holdout')}）\n")
        L.append("| 规则 | 期间 | 下单日占比 | 成交数 | 下单成交率 | 成交后胜率 | 均值 | 中位 | 平均最大浮亏 | 浮亏>15% | 未成交错过的 90 天收益 | 全部日期均值 | 与 R1 差（95% CI） | 分批组合年化 / Sharpe / 最大回撤 / 平均敞口 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for rule, per in B["rules"][sym].items():
            for period in ("selection", "holdout"):
                r = per[period]; eq = r["equity"]
                L.append(f"| {rule} | {'选择期' if period=='selection' else '留出期'} | {pct(r['share_ordered'],0)} | {r['n_filled']} | {pct(r['fill_rate_of_orders'],0)} | {pct(r['positions_win_rate'])} | {pct(r['positions_mean_ret'])} | {pct(r['positions_median_ret'])} | {pct(r['positions_mean_mae'])} | {pct(r['positions_p_mae15'],0)} | {pct(r['missed_when_unfilled'])} | **{pct(r['all_days_mean_ret'])}** | {ci(r['diff_vs_R1'], as_pct=True)} | {pct(eq['ann_return'])} / {num(eq['sharpe'],2)} / {pct(eq['max_drawdown'])} / {pct(eq['avg_exposure'],0)} |")
        L.append("")
    out_path = out_path or (PROJECT_ROOT / "docs" / "16_spot90_多周期预测有效性报告.md")
    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path
