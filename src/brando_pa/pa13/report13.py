"""第十三阶段每日 / 每周中文报告模板（从真实影子记录生成；未发生的日期与未到期窗口只写“未到期 / 无数据”，不填数）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_text

from .audit13 import shadow_audit


def _row(n, x):
    lt = x["latest"]; return f"| {n} | {lt['liquidation_usdt'] if lt['liquidation_usdt'] is None else f'{lt['liquidation_usdt']:,.2f}'} | {lt['cash_usdt'] if lt['cash_usdt'] is None else f'{lt['cash_usdt']:,.0f}'} | {lt['exposure'] if lt['exposure'] is None else f'{lt['exposure']:.0%}'} | {x['n_signals']} / {x['n_fills']} | {x['drawdown']['max_drawdown'] if x['drawdown']['max_drawdown'] is None else f'{x['drawdown']['max_drawdown']:.2%}'} | {x['diff_vs_btc_buyhold_usdt'] if x['diff_vs_btc_buyhold_usdt'] is None else f'{x['diff_vs_btc_buyhold_usdt']:+,.2f}'} |"


def daily_report13(log_dir: Path, day: str | None = None, now: pd.Timestamp | None = None, out_dir: Path | None = None) -> str:
    now = now or pd.Timestamp.now(tz="UTC"); day = day or str((now - pd.Timedelta(days=1)).date()); out_dir = Path(out_dir or (Path(log_dir) / "reports")); out_dir.mkdir(parents=True, exist_ok=True)
    a = shadow_audit(Path(log_dir), now=now, write=False)
    L = [f"# 第十三阶段前瞻日报 {day} (UTC) · 类别：前瞻模拟（独立影子账户）· 生成 {str(now)[:19]} UTC", "", "> 历史回测数字不在本报告内；真实账户：无（真实交易禁用）。未到期窗口不填数。", ""]
    if "status" in a:
        L += [a["status"], "", "## 待办", "- 影子服务尚未产生记录：按 docs/33/34 与本阶段部署步骤启动后再生成。"]; p = out_dir / f"daily_{day}.md"; atomic_write_text(p, "\n".join(L)); return str(p)
    L += ["## 各影子账户（cost 账本，单侧 15 bps）", "", "| 账户 | 清仓净值 USDT | 现金 | 暴露 | 信号 / 成交 | 最大回撤 | vs BTC 持有 |", "|---|---|---|---|---|---|---|"] + [_row(n, x) for n, x in a["accounts"].items()]
    c = a["service"]["continuity"]; L += ["", "## 服务与数据", f"- 覆盖率 {c.get('coverage')}，最长中断 {c.get('longest_gap_hours')} h，行情正常占比 {c.get('data_ok_share')}，主机 {c.get('hosts')}", f"- 中断（> 3 周期）：{a['service']['gaps_gt_3_intervals'] or '无'}", f"- 审计：时间顺序 {'通过' if all(x['timeline_ok'] for x in a['accounts'].values()) else '有违规'}；账本 {'一致' if all(x['ledger_ok'] for x in a['accounts'].values()) else '有问题'}；未来日期 {'有！' if a['future_dates_found'] else '无'}"]
    L += ["", "## 最近决策", "- 见各账户 events.jsonl 最后一条 signal（signal_ts / decision_time / signal / reason / target_weights）；页面 dashboard_phase13.html 同步显示。", "", "## 今日观察（人工填写，不得写未发生的事）", "- 主要行情：", "- 信号 / 成交：", "- 异常：", ""]
    p = out_dir / f"daily_{day}.md"; atomic_write_text(p, "\n".join(L)); return str(p)


def weekly_report13(log_dir: Path, week_end: str | None = None, now: pd.Timestamp | None = None, out_dir: Path | None = None) -> str:
    now = now or pd.Timestamp.now(tz="UTC"); we = pd.Timestamp(week_end, tz="UTC") if week_end else now.normalize(); out_dir = Path(out_dir or (Path(log_dir) / "reports")); out_dir.mkdir(parents=True, exist_ok=True)
    a = shadow_audit(Path(log_dir), now=now, write=False)
    L = [f"# 第十三阶段前瞻周报 · 截至 {we.date()} (UTC) · 类别：前瞻模拟（独立影子账户）· 生成 {str(now)[:19]} UTC", "", "> 历史回测与前瞻严格分区；真实账户：无。n < 10 笔闭合交易时收益指标仅描述，不作统计结论。", ""]
    if "status" in a:
        L += [a["status"]]; p = out_dir / f"weekly_{we.date()}.md"; atomic_write_text(p, "\n".join(L)); return str(p)
    L += ["## 本周净值与风险", "", "| 账户 | 清仓净值 USDT | 现金 | 暴露 | 信号 / 成交 | 最大回撤 | vs BTC 持有 |", "|---|---|---|---|---|---|---|"] + [_row(n, x) for n, x in a["accounts"].items()]
    L += ["", "## 窗口绩效（只写已到期）", "", "| 账户 | 已运行(日) | 30 天 | 90 天 | 180 天 | 365 天 |", "|---|---|---|---|---|---|"]
    for n, x in a["accounts"].items():
        L.append(f"| {n} | {x['elapsed_days']} | " + " | ".join((x["horizons"][f"{h}d"]["status"] if x["horizons"][f"{h}d"]["status"] != "已到期" else f"{x['horizons'][f'{h}d']['net_profit_usdt']:+,.0f}") for h in (30, 90, 180, 365)) + " |")
    L += ["", "## 行为观察（评价规则 §2）"] + [f"- {n}: 主升段暴露 {x['behaviour'].get('avg_exposure_in_btc_30d_up_ge20pct')}，主跌段暴露 {x['behaviour'].get('avg_exposure_in_btc_30d_down_ge20pct')}，最长空仓 {x['behaviour'].get('longest_flat_days')} 日，退出后 10 日内更高价买回 {x['behaviour'].get('sell_then_rebuy_higher_within_10d')} 次" for n, x in a["accounts"].items()]
    c = a["service"]["continuity"]; L += ["", "## 服务连续性", f"- 覆盖率 {c.get('coverage')}，最长中断 {c.get('longest_gap_hours')} h，轮询 {c.get('cycles_observed')}/{c.get('cycles_expected')}", "", "## 本周结论（人工，须引用上表数字；n 不足时写“统计证据不足”）", "- ", ""]
    p = out_dir / f"weekly_{we.date()}.md"; atomic_write_text(p, "\n".join(L)); return str(p)
