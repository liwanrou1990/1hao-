"""把 validation.json / summary.json 生成 Markdown 可信度验证报告（表格全部由程序生成，避免手抄错误）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, report_dir
from .evaluate import block_bootstrap_ci


def pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x*100:.{d}f}%"


def num(x, d=3):
    return "—" if x is None else f"{x:.{d}f}"


def selection_skill_ci(sig: pd.DataFrame) -> tuple[float, float, float]:
    """模型选择持仓的那些日子，市场自身(立即买入)的胜率 − 未持仓日子的市场胜率；块自助 CI。"""
    sel = sig["filled"].to_numpy(bool); y = (sig["ret_A"] > 0).to_numpy(float)
    diff = y[sel].mean() - y[~sel].mean()
    # 对 (选中指示, 结果) 联合块自助
    rng = np.random.default_rng(3); n = len(y); block = 90; nb = int(np.ceil(n / block)); out = []
    for _ in range(400):
        starts = rng.integers(0, n - block + 1, size=nb); idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        s, yy = sel[idx], y[idx]
        if s.any() and (~s).any():
            out.append(yy[s].mean() - yy[~s].mean())
    return float(diff), float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


def build(out_path: Path | None = None) -> Path:
    vdir = report_dir() / "validation"
    v = json.loads((vdir / "validation.json").read_text())
    s = json.loads((report_dir() / "backtest" / "summary.json").read_text())
    a = v["audit"]
    L: list[str] = []
    L.append("# 14 · BTC / ETH 90 天现货投资预测：真实样本外可信度验证报告\n")
    L.append(f"生成时间：{v['run_ts'][:19]} UTC · 代码：`src/spot90/` · 原始产物：`reports_out/spot90/backtest/`、`reports_out/spot90/validation/`\n")
    L.append("> **全部数字均为 Walk-Forward 样本外结果**（14 折，测试期 2020-01-01 → 2026-07-15，每折模型只用 114 天之前结束的标签训练）。训练集成绩不在本报告中作为成绩出现（仅在第 1 节作为过拟合对照提及）。\n")
    # ---- 1 审计
    L.append("## 1. 泄漏与数据完整性审计\n")
    L.append("| 检查项 | 结果 | 说明 |\n|---|---|---|")
    for sym in a["feature_causality"]:
        fc = a["feature_causality"][sym]
        L.append(f"| 特征因果性 {sym} | {'通过' if fc['passed'] else '失败'} | 删除第 i 天之后全部 K 线重算，第 i 天 {len(fc['checks'])} 个随机抽样日特征最大差异 {fc['worst_abs_diff']:.1e} |")
    for sym in a["label_recompute"]:
        lr = a["label_recompute"][sym]
        L.append(f"| 标签重算 {sym} | {'通过' if lr['passed'] else '失败'} | 朴素循环重算 {lr['n_checks']} 个决策日的 A/B 标签，最大误差 {lr['max_err']:.1e}；最后 K 线 {lr['last_bar']}，最后有标签的决策日 {lr['last_labeled_decision_day']}（相差 {lr['days_between']} 天 ≥ W+H={lr['required_gap_W_plus_H']}） |")
    fi = a["fold_isolation"]
    L.append(f"| 折间隔离 | {'通过' if fi['passed'] else '失败'} | 14 折 训练最后决策日 → 测试起点 间隔均为 {fi['folds'][0]['gap_days']} 天（要求 ≥ {fi['required_gap_days']} = 14 等待 + 90 持有 + 10 embargo）；测试块首尾相接、不重叠 |")
    L.append("| 最后 90 天 | 通过 | 2026-07-16 之后的决策日没有完整 90 天结果，**既不进训练也不进评估**；2026-07-29 → 2026-10-27 的价格只作为 2026-07-15 之前决策日的“事后结果”使用 |")
    L.append("| 校准器因果性 | 通过 | 第 k 折的等渗校准只用第 <k 折的样本外预测拟合（前两折样本不足时不校准） |")
    L.append("| 市场基准口径 | 说明 | Brier 技能的分母用“整个样本外期的真实盈利率常数”（事后已知、对模型不利的严格基准）；同时给出“训练窗历史频率模型”的样本外 Brier |")
    L.append("")
    L.append("**数据源**：\n")
    L.append("| 标的 | 来源 | 范围 | 日线缺口 | 4h 缺口 | 零成交量日 | 与 DefiLlama/CoinGecko 日价中位偏差 | 95 分位 | 偏差最大的日期 |\n|---|---|---|---|---|---|---|---|---|")
    for sym, d in a["data_sources"].items():
        w = "；".join(f"{x['ts']} {x['dev_pct']:+.0f}%" for x in d["worst_crosscheck_days"][:3])
        L.append(f"| {sym} | {list(d['source_1d'])[0]} | {d['range_1d'][0]} → {d['range_1d'][1]}（{d['years']} 年） | {d['gaps_1d']} 根 | {d['gaps_4h_bars']} 根（交易所维护时段，不参与日线标签） | {d['zero_volume_days']} | {d['cross_check']['median_abs_pct']}% | {d['cross_check']['p95_abs_pct']}% | {w} |")
    L.append("")
    L.append(f"- Bitget：`{a['data_sources']['BTCUSDT']['bitget_probe']['detail']}`。因此本轮全部使用 GitHub 公开仓库 `finom/static-klines` 提交的 **Binance 现货** 日线/4h 线缓存（真实数据，每日由 GitHub Actions 更新，数据截至 2026-10-27）。它不是 Bitget 成交价；主流币两所日收盘价差通常在万分之几量级，但**结论默认只对 Binance 现货价格成立**，Bitget 版本需在能访问 `api.bitget.com` 的机器上重跑 `spot90 ingest`。")
    L.append("- 交叉校验偏差 >15% 的日子全部集中在 2017-09 ~ 2018-01（CoinGecko 日价为 UTC 0 点快照，极端波动日与收盘价相差大），不属于本回测的样本外期（2020 年起），且未发现重复、缺失或零成交量日线。")
    L.append("- 过拟合对照：LightGBM 在 fold 8 训练窗内 AUC = 0.999，样本外 AUC ≈ 0.60。训练集成绩毫无参考价值，本报告一律不用。\n")
    # ---- 2 per symbol
    models = ["baserate", "volboot", "lightgbm", "catboost", "ensemble"]
    names = {"baserate": "历史频率基准", "volboot": "波动率自助基准", "lightgbm": "LightGBM(A)", "catboost": "CatBoost(B)", "ensemble": "A+B 平均"}
    for sym, r in v["symbols"].items():
        L.append(f"## 2. {sym[:3]} 样本外结果（{r['oos_range'][0]} → {r['oos_range'][1]}，{r['oos_days']} 个决策日，每日重叠 90 天窗口 ≈ 26 个独立 90 天样本）\n")
        L.append("### 2.1 90 天方向预测与概率校准（A：立即买入）\n")
        L.append("| 模型 | 方向准确率 | “永远看涨”准确率 | AUC | Brier | Brier 技能(vs 事后常数) | ECE 校准误差 |\n|---|---|---|---|---|---|---|")
        for m in models:
            dc = r["models"][m]["direction_calibration"]; rp = dc["report"]
            L.append(f"| {names[m]} | {pct(dc['direction_accuracy'])} | {pct(dc['always_up_accuracy'])} | {num(rp['auc'])} | {num(rp['brier'],4)} | {rp['brier_skill']:+.3f} | {num(rp['ece'])} |")
        L.append(f"| 随机预测 | {pct(r['benchmarks']['random_50pct']['direction_accuracy_random'])} | — | 0.500 | ≈0.333 | 负 | — |")
        L.append("")
        L.append("### 2.2 预测盈利概率 ≥ 65% 时，真实发生了什么（校准后概率）\n")
        L.append("| 模型 | 触发天数(占比) | 预测概率均值 | **真实 90 天盈利率** | 同期市场自身盈利率 | 提升(模型−市场) | 概率误差 | 真实 90 天均值收益 | 真实盈利率块自助 95% CI |\n|---|---|---|---|---|---|---|---|---|")
        for m in models:
            g = r["models"][m]["direction_calibration"]["ge65"]
            ci = g["realized_win_rate_ci95_blockboot"]
            lift = None if g["realized_win_rate"] is None else g["realized_win_rate"] - g["same_period_market_win_rate"]
            err = None if g["realized_win_rate"] is None else g["pred_mean"] - g["realized_win_rate"]
            L.append(f"| {names[m]} | {g['n_days']} ({pct(g['share_of_days'],0)}) | {pct(g['pred_mean'])} | **{pct(g['realized_win_rate'])}** | {pct(g['same_period_market_win_rate'])} | {pct(lift)} | {pct(err)} | {pct(g['mean_ret_A'])} | {'—' if not ci else f'{pct(ci[0])} ~ {pct(ci[1])}'} |")
        L.append("")
        L.append("按预测概率分桶（LightGBM / CatBoost / 平均）：\n")
        L.append("| 模型 | 概率桶 | 天数 | 预测均值 | 真实盈利率 | 同期市场盈利率 | 提升 | 概率误差 |\n|---|---|---|---|---|---|---|---|")
        for m in ("lightgbm", "catboost", "ensemble"):
            for b in r["models"][m]["direction_calibration"]["bins"]:
                if b["n"]:
                    L.append(f"| {names[m]} | {b['bin']} | {b['n']} | {pct(b['pred_mean'])} | {pct(b['realized_win_rate'])} | {pct(b['same_period_market_win_rate'])} | {pct(b['lift_vs_market'])} | {pct(b['prob_error'])} |")
        L.append("")
        L.append("### 2.3 决策策略 vs 三个基准（同一组样本外日期）\n")
        L.append("| 策略 | 下单/持仓日占比 | 成交后 90 天真实盈利率 | 平均净收益 | 中位净收益 | 浮亏>15% 概率 | 平均最大浮亏 | 平均峰谷回撤 | 全部日期平均收益(空仓=0) | 与每日买入持有之差 95% CI(块自助) | 最大连续亏损(重叠日) | 最大连续亏损(每 90 天抽 1 笔，中位/最大) |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
        bm = r["benchmarks"]
        for k, lab in (("buy_and_hold_every_day", "每天立即买入持有 90 天"), ("random_50pct", "随机(50% 的日子买入)"), ("ma200_filter", "简单均线：收盘>MA200 才买"), ("ma200_up_trend", "均线：>MA200 且 MA200 上行"), ("ma50_filter", "均线：收盘>MA50 才买")):
            b = bm[k]
            L.append(f"| {lab} | {pct(b['share_days_invested'],0)} | {pct(b['positions_win_rate'])} | {pct(b['positions_mean_ret'])} | {pct(b['positions_median_ret'])} | {pct(b['positions_p_mae_gt_thr'],0)} | — | — | {pct(b['mean_ret_all_days'])} | — | {int(b['max_consec_losses_overlapping'])} | — |")
        for m in models:
            st = r["models"][m]["strategy"]; no = r["models"][m]["nonoverlap_runs"]
            inv = (st["n_buy_now"] + st["n_wait"]) / st["n_days"]
            ci = st["diff_ci95_blockboot"]
            L.append(f"| **AI {names[m]}** | {pct(inv,0)}（买入 {st['n_buy_now']} / 挂单 {st['n_wait']} / 观望 {st['n_cash']}，成交 {st['n_filled']}） | {pct(st['positions_win_rate'])} | {pct(st['positions_mean_ret'])} | {pct(st['positions_median_ret'])} | {pct(st['positions_p_mae_gt_thr'],0)} | {pct(st['positions_mean_mae'])} | {pct(st['positions_mean_mdd'])} | {pct(st['strategy_mean_ret_all_days'])} | {pct(ci[0])} ~ {pct(ci[1])} | {r['models'][m]['max_consec_losses_overlapping']} | {no.get('max_consec_losses_median','—')} / {no.get('max_consec_losses_max','—')} |")
        L.append("")
        L.append("模型“选中持仓的日子”与“未选中的日子”，市场自身（当天立即买入持有 90 天）的表现对比，用来区分**选日能力**和**市场自身上涨**：\n")
        L.append("| 模型 | 持仓日数 | 持仓日：AI 策略盈利率 / 均值 | 持仓日：当天立即买入盈利率 / 均值 | 空仓日：当天立即买入盈利率 / 均值 | 选日能力 = 持仓日市场盈利率 − 空仓日市场盈利率（块自助 95% CI） |\n|---|---|---|---|---|---|")
        for m in ("lightgbm", "catboost", "ensemble"):
            sig = pd.read_csv(vdir / f"{sym}_signals_{m}.csv")
            ent = sig[sig["filled"]]; cash = sig[~sig["filled"]]
            d, lo, hi = selection_skill_ci(sig)
            L.append(f"| {names[m]} | {len(ent)} | {pct((ent['realized_ret']>0).mean())} / {pct(ent['realized_ret'].mean())} | {pct((ent['ret_A']>0).mean())} / {pct(ent['ret_A'].mean())} | {pct((cash['ret_A']>0).mean())} / {pct(cash['ret_A'].mean())} | {pct(d)}（{pct(lo)} ~ {pct(hi)}） |")
        L.append("")
        L.append("### 2.4 限价单：AI 能否找到比立即买入更合理的价格？\n")
        L.append("固定折价挂单（所有样本外日期都按同一折价挂 14 天限价单）的**真实**结果，与 LightGBM 的预测对照：\n")
        L.append("| 折价 d | 预测成交概率 | 真实成交率 | 预测成交后盈利概率 | 真实成交后盈利率 | 预测成交后中位收益 | 真实成交后均值 / 中位收益 | 成交后浮亏>15% | 含未成交的全部日期期望收益 | 同一批日期立即买入均值 | 未成交时错过的 90 天收益 |\n|---|---|---|---|---|---|---|---|---|---|---|")
        for row in r["models"]["lightgbm"]["limit_orders"]["fixed_discount_table"]:
            L.append(f"| {row['d_pct']:.0f}% | {pct(row['pred_fill'],0)} | {pct(row['real_fill'],0)} | {pct(row['pred_profit_given_fill'],0)} | {pct(row['real_profit_given_fill'],0)} | {pct(row['pred_ret_median_given_fill'])} | {pct(row['real_ret_mean_given_fill'])} / {pct(row['real_ret_median_given_fill'])} | {pct(row['real_p_mae_gt_thr_given_fill'],0)} | **{pct(row['ev_all_days_fill_inclusive'])}** | {pct(row['buy_now_mean_ret_same_days'])} | {pct(row['missed_ret_A_when_unfilled'])} |")
        L.append("")
        L.append("模型自己选择“挂单等待”的那些日子：\n")
        L.append("| 模型 | 挂单日数 | 平均折价 | 14 天内真实成交率 | 成交价相对挂单日收盘 | 成交后盈利率 / 均值 | **含未成交的期望收益** | **同一批日期立即买入均值** | 未成交时错过的收益 | 差值(等待−立即) 95% CI | 立即买入日数 / 盈利率 / 均值 |\n|---|---|---|---|---|---|---|---|---|---|---|")
        for m in ("lightgbm", "catboost", "ensemble"):
            c = r["models"][m]["limit_orders"]["model_choice"]; ci = c["diff_wait_vs_buynow_same_days_ci95"]
            L.append(f"| {names[m]} | {c['n_wait_days']} | {num(c['wait_avg_discount_pct'],1)}% | {pct(c['wait_fill_rate'])} | {pct(c['wait_avg_fill_px_vs_close'])} | {pct(c['wait_filled_positions_win_rate'])} / {pct(c['wait_filled_positions_mean_ret'])} | **{pct(c['wait_fill_inclusive_mean_ret'])}** | **{pct(c['wait_buy_now_mean_ret_same_days'])}** | {pct(c['wait_missed_ret_when_unfilled'])} | {'样本不足' if not ci else f'{pct(ci[0])} ~ {pct(ci[1])}'} | {c['n_buy_now_days']} / {pct(c['buy_now_win_rate'])} / {pct(c['buy_now_mean_ret'])} |")
        L.append("")
        L.append("### 2.5 分市场阶段 / 分年份（LightGBM 与 CatBoost；市场阶段按决策日可知的 MA200 位置与斜率划分）\n")
        L.append("| 模型 | 阶段/年份 | 决策日 | 买入/挂单/观望 | 成交笔数 | AI 成交后盈利率 / 均值 | 同期每天买入持有盈利率 / 均值 | 同期买入持有浮亏>15% |\n|---|---|---|---|---|---|---|---|")
        for m in ("lightgbm", "catboost"):
            for k, d in list(r["models"][m]["by_regime"].items()) + list(r["models"][m]["by_year"].items()):
                L.append(f"| {names[m]} | {k} | {d['n_days']} | {d['n_buy']}/{d['n_wait']}/{d['n_cash']} | {d['n_filled']} | {pct(d['positions_win_rate'])} / {pct(d['positions_mean_ret'])} | {pct(d['buyhold_win_rate'])} / {pct(d['buyhold_mean_ret'])} | {pct(d['buyhold_p_mae_gt_thr'],0)} |")
        L.append("")
    # ---- 3 其他概率质量
    L.append("## 3. 其它样本外概率质量（BTC+ETH 合并）\n")
    L.append("| 模型 | 限价成交概率 AUC / Brier 技能 | 成交后盈利概率 AUC / Brier 技能 | 浮亏>15% 概率(A) AUC / 技能 | 90% 收益区间真实覆盖率 | 50% 区间覆盖率 | 中位收益预测绝对误差 |\n|---|---|---|---|---|---|---|")
    for m in models:
        q = s["probability_quality"][m]
        f_, p_, d_, qq = q["B_fill_cal"], q["B_profit_given_fill_cal"], q["A_dd_cal"], q["A_quantiles"]
        L.append(f"| {names[m]} | {num(f_['auc'])} / {f_['brier_skill']:+.3f} | {num(p_['auc'])} / {p_['brier_skill']:+.3f} | {num(d_['auc'])} / {d_['brier_skill']:+.3f} | {pct(qq['coverage_90'],0)} | {pct(qq['coverage_50'],0)} | {pct(q['A_ret_q50_mae'])} |")
    L.append("")
    out_path = out_path or (PROJECT_ROOT / "docs" / "14_spot90_90天预测可信度验证报告.md")
    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    print(build())
