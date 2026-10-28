"""第二阶段 Markdown 报告：链上数据加入前后的样本外对照（表格由程序生成）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, report_dir
from ..data.onchain import load_status
from .audit import onchain_causality_check


def pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x*100:.{d}f}%"


def num(x, d=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def ci_str(c: dict | None, key="ci950") -> str:
    if not c or key not in c:
        return "—"
    lo, hi = c[key]
    return f"{c['mean']:+.4f} [{lo:+.4f}, {hi:+.4f}]"


def ci_pct(c: dict | None, key="ci950") -> str:
    if not c or key not in c:
        return "—"
    lo, hi = c[key]
    return f"{c['mean']*100:+.1f}% [{lo*100:+.1f}%, {hi*100:+.1f}%]"


def build(out_path: Path | None = None) -> Path:
    r = json.loads((report_dir() / "phase2" / "phase2_summary.json").read_text())
    st = load_status() or {}
    oc_audit = onchain_causality_check()
    E = r["experiments"]; P = r["paired_vs_A_tech"]
    names = {"A_tech": "A 技术指标", "B_pit": "B_pit 链上(PIT)", "B_full": "B_full +交易所流量/TVL(非PIT)", "D_pit": "D_pit +宏观", "onchain_pit_only": "仅链上(PIT)"}
    L: list[str] = []
    L.append("# 15 · 第二阶段：链上资金流数据能否提升 BTC / ETH 90 天预测？（真实样本外对照）\n")
    L.append(f"生成时间：{r['run_ts'][:19]} UTC · 实验数 {r['n_experiments']}（Bonferroni 调整后置信水平 {r['bonferroni_level']*100:.1f}%）· 总耗时 {r['seconds_total']}s · 产物 `reports_out/spot90/phase2/`\n")
    L.append("> 同一 14 折 Walk-Forward（测试期 2020-01-01 → 2026-07-15，训练/测试间隔 114 天），同一校准流程，同一决策规则。全部为样本外数字。\n")
    # 1 数据源
    L.append("## 1. 链上 / 资金面 / 宏观数据：实际可用性\n")
    L.append("| 来源 | 状态 | 指标 | 历史区间 | 频率 | 发布滞后处理 | Point-in-Time |\n|---|---|---|---|---|---|---|")
    for k, v in st.get("sources", {}).items():
        if v.get("ok"):
            L.append(f"| {k} | 可用 | {v['source']} | {v['first']} → {v['last']} | {v['cadence']} | 滞后 {v['availability_lag_days']} 天 | {'是' if v['pit_safe'] else '否（表级）'} |")
    for k, v in st.get("blocked", {}).items():
        L.append(f"| {k} | **不可达/未接入** | {v['metrics']} | — | — | — | {v['why']} |")
    L.append("")
    L.append("- Coin Metrics 社区归档（GitHub `coinmetrics/data`）**数据截至 2026-05-23**，之后链上特征为缺失（模型按缺失处理，不填充）；因此最后一折（2026-05-29 → 07-15）链上特征基本缺失，且**当前生产预测无法获得最新链上值**。")
    L.append("- 不可用且未伪造：SOPR、NUPL、长短期持有者、巨鲸余额、矿工流、ETH 质押全网数据（只有 Lido 代理）、稳定币交易所净流、资金费率/未平仓量/清算、ETF 资金流、日频宏观。模型 C（衍生品）因此**未运行**；模型 D 只含月度宏观。")
    L.append("- 区分口径：`FlowInEx/FlowOutEx/SplyEx` 是交易所地址的链上转账/储备（非 PIT）；`volume_reported_spot_usd_1d` 是交易所报告的现货成交额；两者分组不同，不混用。")
    L.append(f"- 链上特征截断式因果审计：{'通过' if oc_audit['passed'] else '失败'}（把原始表截断到决策日−{oc_audit['lag_days']} 天重算，{len(oc_audit['checks'])} 个抽样点最大差异 {oc_audit['worst_abs_diff']:.1e}）。\n")
    L.append("**特征组**：\n")
    L.append("| 组 | PIT 安全 | 特征数 | 特征 |\n|---|---|---|---|")
    for g, v in r["groups"].items():
        L.append(f"| {g} | {'是' if v['pit_safe'] else '否'} | {v['n_features']} | {', '.join(v['cols'])} |")
    L.append("")
    # 2 主对照
    for sym in ("BTCUSDT", "ETHUSDT", "pooled"):
        title = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "pooled": "BTC+ETH 合并"}[sym]
        L.append(f"## 2.{ {'BTCUSDT':1,'ETHUSDT':2,'pooled':3}[sym] } {title}：链上数据加入前后（主实验组）\n")
        L.append("| 实验 | 模型 | 90 天盈利概率 AUC | Brier | Brier 技能 | LogLoss | ECE | 浮亏>10% AUC | 浮亏>15% AUC | 浮亏>20% AUC | 90% 区间覆盖 | 成交概率 AUC | 成交后盈利 AUC | Brier 差 vs A（95% CI） | 差 vs A（Bonferroni CI） |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for ename in ("A_tech", "B_pit", "B_full", "D_pit", "onchain_pit_only"):
            if ename not in E:
                continue
            for m in ("lightgbm", "catboost"):
                if m not in E[ename]["models"]:
                    continue
                x = E[ename]["models"][m][sym]; a = x["A_profit_cal"]
                comp = P.get(f"{ename}|{m}", {}).get(sym, {}).get("brier_diff")
                bk = [k for k in (comp or {}) if k.startswith("ci") and k != "ci950"]
                L.append(f"| {names[ename]} | {m} | {num(a.get('auc'))} | {num(a.get('brier'),4)} | {a.get('brier_skill', float('nan')):+.3f} | {num(a.get('log_loss'))} | {num(a.get('ece'))} | "
                         f"{num(x.get('A_mae10_A_cal',{}).get('auc'))} | {num(x['A_dd15_cal'].get('auc'))} | {num(x.get('A_mae20_A_cal',{}).get('auc'))} | {pct(x['A_quantiles'].get('coverage_90'),0)} | "
                         f"{num(x['B_fill_cal'].get('auc'))} | {num(x['B_profit_given_fill_cal'].get('auc'))} | {ci_str(comp)} | {ci_str(comp, bk[0]) if bk else '—'} |")
        L.append("")
        L.append("决策策略结果（同一规则，校准后概率；含手续费/滑点/未成交机会成本）：\n")
        L.append("| 实验 | 模型 | 买入/挂单/观望 | 成交 | 成交后盈利率 | 均值 | 浮亏>15% | 均值/标准差 | 收益/平均浮亏 | 全部日期均值 | 买入持有均值 | 等待−同窗口立即买入 | 全部日期收益差 vs A 实验（95% CI） |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for ename in ("A_tech", "B_pit", "B_full", "D_pit"):
            if ename not in E:
                continue
            for m in ("lightgbm", "catboost"):
                if m not in E[ename]["models"]:
                    continue
                d = E[ename]["models"][m]["decision"][sym]
                comp = P.get(f"{ename}|{m}", {}).get(sym, {}).get("strategy_ret_all_days_diff")
                L.append(f"| {names[ename]} | {m} | {d['n_buy_now']}/{d['n_wait']}/{d['n_cash']} | {d['n_filled']} | {pct(d['positions_win_rate'])} | {pct(d['positions_mean_ret'])} | {pct(d['positions_p_mae_gt_thr'],0)} | "
                         f"{num(d.get('positions_sharpe_like'),2)} | {num(d.get('positions_ret_over_mae'),2)} | {pct(d['strategy_mean_ret_all_days'])} | {pct(d['buyhold_mean_ret'])} | {pct(d.get('wait_vs_A_same_window_mean_diff'))} | {ci_pct(comp)} |")
        L.append("")
    # 3 消融
    L.append("## 3. 特征消融（LightGBM，合并样本；每行 vs A_tech 的按日配对 Brier 差，负值 = 链上特征有帮助）\n")
    L.append("| 实验 | 说明 | 特征数 | AUC 合并 | AUC BTC | AUC ETH | Brier 合并 | Brier 差 vs A（95% CI） | Bonferroni CI | LogLoss 差（95% CI） | 浮亏>15% AUC |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for ename, e in E.items():
        m = e["models"].get("lightgbm")
        if not m:
            continue
        c = P.get(f"{ename}|lightgbm", {})
        comp = c.get("pooled", {}).get("brier_diff"); bk = [k for k in (comp or {}) if k.startswith("ci") and k != "ci950"]
        L.append(f"| {ename} | {e['desc']} | {e['n_features']} | {num(m['pooled']['A_profit_cal'].get('auc'))} | {num(m['BTCUSDT']['A_profit_cal'].get('auc'))} | {num(m['ETHUSDT']['A_profit_cal'].get('auc'))} | "
                 f"{num(m['pooled']['A_profit_cal'].get('brier'),4)} | {ci_str(comp)} | {ci_str(comp, bk[0]) if bk else '—'} | {ci_str(c.get('pooled', {}).get('logloss_diff'))} | {num(m['pooled']['A_dd15_cal'].get('auc'))} |")
    L.append("")
    # 4 重要性
    L.append("## 4. 哪些链上指标被模型使用（B_pit LightGBM 跨折平均增益，前 20）\n")
    imp = E.get("B_pit", {}).get("models", {}).get("lightgbm", {}).get("importance_top20", {})
    if imp:
        L.append("| 特征 | 平均增益 | 组 |\n|---|---|---|")
        grp = {c: g for g, v in r["groups"].items() for c in v["cols"]}
        for k, v in imp.items():
            L.append(f"| {k} | {v} | {grp.get(k, '技术指标')} |")
    L.append("\n> 增益只说明模型“用了”哪些特征，不说明样本外有预测价值；预测价值看第 3 节消融的配对检验。\n")
    # 5 稳健性
    if "robustness_B_pit_lag2" in r:
        L.append("## 5. 稳健性：链上可用性滞后 1 天 → 2 天（B_pit LightGBM）\n")
        L.append("| 口径 | 滞后 1 天 AUC / Brier | 滞后 2 天 AUC / Brier |\n|---|---|---|")
        for sym in ("pooled", "BTCUSDT", "ETHUSDT"):
            a1 = E["B_pit"]["models"]["lightgbm"][sym]["A_profit_cal"]; a2 = r["robustness_B_pit_lag2"][sym]["A_profit_cal"]
            L.append(f"| {sym} | {num(a1.get('auc'))} / {num(a1.get('brier'),4)} | {num(a2.get('auc'))} / {num(a2.get('brier'),4)} |")
        L.append("")
    out_path = out_path or (PROJECT_ROOT / "docs" / "15_spot90_链上数据对照实验报告.md")
    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    print(build())
