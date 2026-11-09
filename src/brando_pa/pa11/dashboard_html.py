"""第十一阶段只读静态研究页面（Plotly HTML，本地打开即可；不连接任何账户，不写任何账本）。
内容：价格 + 趋势结构 + 当前支撑/阻力区 + 候选买入区 / T1 / T2 / 失效价 + 历史买卖点（当时已确认信号 vs 事后观察分开标注）、净值 / 回撤、交易记录、与买入持有比较、成本与持仓时间、最新 BUY/WAIT/HOLD/EXIT 建议与理由、数据来源与更新时间。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..holding.data import load_dataset
from .engine11 import Config, MarketContext, PriceActionBacktester
from .macro import build_events

SYMS = ["BTCUSDT", "ETHUSDT"]


def _latest_state(bars, ctx: MarketContext, cost=25) -> dict:
    """用主配置（V4 退出 B）在全样本跑一次，取最后一日决策 / 区域 / 计划。"""
    r = PriceActionBacktester(bars, Config(strategies=("A", "B"), exit_mode="B", name="V4_pa_combined"), 20.0, 5.0, "2018-01-01", str(ctx.idx[-1].date()), events=build_events(), ctx=ctx).run()
    i = ctx.n - 1; out = {"as_of": str(ctx.idx[i].date()), "per_symbol": {}}
    for s in SYMS:
        dec = r.decisions[r.decisions.symbol == s].tail(1); zones = ctx.zones[s][i]; cl = ctx.close[s][i]
        sup = [z for z in zones if z.high < cl][-3:]; res = [z for z in zones if z.low > cl][:3]
        out["per_symbol"][s] = {"close": cl, "weekly_trend": ctx.wt[s][i]["trend"], "sma200_filter": ctx.ds[s][i]["sma_filter_ok"], "action": dec.iloc[0]["action"] if len(dec) else "—", "reason": dec.iloc[0]["reason"] if len(dec) else "—",
                                "supports": [[z.low, z.high, z.strength] for z in sup], "resistances": [[z.low, z.high, z.strength] for z in res], "atr": ctx.ds[s][i]["atr"]}
    return out, r


def build_dashboard(results_dir: Path | None = None, out: Path | None = None, embed: bool = True) -> str:
    results_dir = Path(results_dir or "reports_out/brando_pa/phase11_pa"); out = Path(out or (results_dir / "dashboard_phase11.html"))
    res = json.loads((results_dir / "phase11_results.json").read_text()); rows = pd.DataFrame(res["rows"]); curves = json.loads((results_dir / "curves.json").read_text()) if (results_dir / "curves.json").exists() else {}
    trades = pd.read_csv(results_dir / "trades.csv") if (results_dir / "trades.csv").exists() else pd.DataFrame()
    bars = load_dataset(); ctx = MarketContext(bars); latest, main_res = _latest_state(bars, ctx)
    manifest = json.loads(Path("data_store/brando_pa/phase10_holding/data_manifest.json").read_text())
    figs = []
    # 1) 价格 + 区域 + 买卖点（主配置 V4 退出 B 全样本 25 bps）
    for s in SYMS:
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b[b["ts"] >= "2018-01-01"]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03, subplot_titles=(f"{s} 日线收盘（对数）· 当前支撑/阻力区 · 历史买卖点", "成交量"))
        fig.add_trace(go.Scatter(x=b["ts"], y=b["close"], name="收盘", line=dict(width=1)), row=1, col=1)
        sma = b["close"].rolling(200).mean(); fig.add_trace(go.Scatter(x=b["ts"], y=sma, name="SMA200", line=dict(width=1, dash="dot")), row=1, col=1)
        st = latest["per_symbol"][s]
        for lo, hi, k in st["supports"]:
            fig.add_hrect(y0=lo, y1=hi, fillcolor="green", opacity=0.12, line_width=0, annotation_text=f"支撑 强度{k}", annotation_position="left", row=1, col=1)
        for lo, hi, k in st["resistances"]:
            fig.add_hrect(y0=lo, y1=hi, fillcolor="red", opacity=0.10, line_width=0, annotation_text=f"阻力 强度{k}", annotation_position="left", row=1, col=1)
        t = main_res.trades[main_res.trades.symbol == s] if len(main_res.trades) else pd.DataFrame()
        if len(t):
            ent = t.drop_duplicates("entry_ts"); fig.add_trace(go.Scatter(x=ent["entry_ts"], y=ent["entry_price"], mode="markers", name="买入（当时已确认信号，次日开盘成交）", marker=dict(symbol="triangle-up", size=11, color="green")), row=1, col=1)
            fig.add_trace(go.Scatter(x=t["exit_ts"], y=t["exit_price"], mode="markers", name="卖出 / 减仓（规则触发）", marker=dict(symbol="triangle-down", size=11, color="red"), text=t["reason"], hovertemplate="%{x|%Y-%m-%d} %{y:.0f}<br>%{text}"), row=1, col=1)
            fig.add_trace(go.Scatter(x=ent["entry_ts"], y=ent["planned_t1"], mode="markers", name="入场时计划 T1（事前）", marker=dict(symbol="line-ew", size=14, color="orange", line=dict(width=2))), row=1, col=1)
            fig.add_trace(go.Scatter(x=ent["entry_ts"], y=ent["planned_invalidation"], mode="markers", name="入场时失效价（事前）", marker=dict(symbol="x", size=9, color="black")), row=1, col=1)
        sg = main_res.signals[(main_res.signals.symbol == s) & (~main_res.signals.accepted)] if len(main_res.signals) else pd.DataFrame()
        if len(sg) and "entry_ref" in sg:
            sg = sg.dropna(subset=["entry_ref"]); fig.add_trace(go.Scatter(x=sg["ts"], y=sg["entry_ref"], mode="markers", name="候选信号被拒（盈亏比/宏观/回撤约束）", marker=dict(symbol="circle-open", size=8, color="gray")), row=1, col=1)
        fig.add_trace(go.Bar(x=b["ts"], y=b["volume"], name="成交量", marker_color="lightgray"), row=2, col=1)
        fig.update_yaxes(type="log", row=1, col=1); fig.update_layout(height=620, legend=dict(orientation="h"), margin=dict(l=40, r=20, t=60, b=30)); figs.append(fig)
    # 2) 净值与回撤（全样本 25 bps：主配置各组 + 买入持有）
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04, subplot_titles=("清仓口径净值（USDT，全样本，单侧 25 bps）", "回撤"))
    for key, cv in curves.items():
        w, cost, g, ex = key.split("|")
        if w != "full" or ex != "exitB":
            continue
        df = pd.DataFrame(cv); df["ts"] = pd.to_datetime(df["ts"]); fig.add_trace(go.Scatter(x=df["ts"], y=df["liquidation"], name=f"{g} {ex}", line=dict(width=1.2)), row=1, col=1); fig.add_trace(go.Scatter(x=df["ts"], y=df["drawdown"], name=f"{g} 回撤", line=dict(width=0.8), showlegend=False), row=2, col=1)
    for s, nm in (("BTCUSDT", "BTC 买入持有"), ("ETHUSDT", "ETH 买入持有")):
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b[b["ts"] >= "2018-01-01"]; q = 9990 / (b["open"].iloc[1] * 1.0005 * 1.002); eq = 10 + q * b["close"] * 0.9995 * 0.998
        fig.add_trace(go.Scatter(x=b["ts"], y=eq, name=nm, line=dict(width=1, dash="dash")), row=1, col=1); fig.add_trace(go.Scatter(x=b["ts"], y=eq / eq.cummax() - 1, name=nm + " 回撤", line=dict(width=0.8, dash="dash"), showlegend=False), row=2, col=1)
    fig.update_yaxes(type="log", row=1, col=1); fig.update_layout(height=560, legend=dict(orientation="h"), margin=dict(l=40, r=20, t=60, b=30)); figs.append(fig)
    # 表格
    def tbl(df, cols, fmt=None):
        fmt = fmt or {}
        head = "".join(f"<th>{c}</th>" for c in cols); body = ""
        for _, r in df.iterrows():
            body += "<tr>" + "".join(f"<td>{(fmt.get(c, lambda v: v))(r.get(c))}</td>" for c in cols) + "</tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    money = lambda v: "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:,.0f}"; pct = lambda v: "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v*100:.1f}%"
    sel = rows[(rows.window == "full") & (rows.cost_bps == 25)].sort_values("liquidation_net_profit_usdt", ascending=False)
    t_full = tbl(sel, ["group", "exit_mode", "final_equity", "liquidation_usdt", "liquidation_net_profit_usdt", "cagr_liquidation", "max_drawdown", "longest_drawdown_days", "n_fills", "total_cost_usdt", "time_flat", "top5_share_of_closed_pnl"], {"final_equity": money, "liquidation_usdt": money, "liquidation_net_profit_usdt": money, "cagr_liquidation": pct, "max_drawdown": pct, "total_cost_usdt": money, "time_flat": pct, "top5_share_of_closed_pnl": pct})
    sel2 = rows[(rows.window == "test_seen") & (rows.cost_bps == 25)].sort_values("liquidation_net_profit_usdt", ascending=False)
    t_test = tbl(sel2, ["group", "exit_mode", "final_equity", "liquidation_net_profit_usdt", "max_drawdown", "n_fills", "time_flat"], {"final_equity": money, "liquidation_net_profit_usdt": money, "max_drawdown": pct, "time_flat": pct})
    tr = trades[(trades.window == "full") & (trades.cost_bps == 25) & (trades.group == "V4_pa_combined") & (trades.exit_mode == "B")] if len(trades) else pd.DataFrame()
    t_trades = tbl(tr, ["symbol", "strategy", "entry_ts", "exit_ts", "entry_price", "exit_price", "hold_days", "net_pnl", "reason", "planned_t1", "planned_invalidation", "planned_rr", "r_multiple"], {"entry_price": money, "exit_price": money, "net_pnl": money, "planned_t1": money, "planned_invalidation": money, "planned_rr": lambda v: f"{v:.2f}", "r_multiple": lambda v: f"{v:.2f}", "entry_ts": lambda v: str(v)[:10], "exit_ts": lambda v: str(v)[:10]}) if len(tr) else "<p>无交易</p>"
    cur = "".join(f"<div class='card'><h3>{s} · 收盘 {v['close']:,.0f} · 周线 {v['weekly_trend']} · SMA200 过滤 {'通过' if v['sma200_filter'] else '未通过'}</h3><p class='act {v['action']}'>{v['action']}</p><p>{v['reason']}</p><p>支撑区：{', '.join(f'[{lo:,.0f}, {hi:,.0f}] 强度 {k}' for lo, hi, k in v['supports']) or '无'}<br>阻力区：{', '.join(f'[{lo:,.0f}, {hi:,.0f}] 强度 {k}' for lo, hi, k in v['resistances']) or '无'}<br>ATR14 {v['atr']:,.0f}</p></div>" for s, v in latest["per_symbol"].items())
    html = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'><title>Brando Phase 11 研究页面（只读）</title><style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:20px;color:#222}table{border-collapse:collapse;font-size:12px;margin:8px 0}th,td{border:1px solid #ddd;padding:3px 6px;text-align:right}th{background:#f5f5f5}td:first-child,th:first-child{text-align:left}.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0;background:#fafafa}.act{font-size:22px;font-weight:700}.BUY{color:#1a7f37}.WAIT{color:#9a6700}.HOLD{color:#0969da}.EXIT,.REDUCE{color:#cf222e}.note{background:#fff8c5;padding:8px;border-radius:6px}</style></head><body>",
            f"<h1>Brando Price Action · 第十一阶段研究页面（只读，历史回测）</h1><p class='note'>类别：历史回测（日收盘决策、次日开盘成交）；<b>不是</b>前瞻模拟；不连接任何账户；真实交易禁用。成本一律为<b>单侧</b> bps。2024–2026 段已在 Phase 10 被查看，不是盲测。生成时间 {res['generated_at_utc'][:19]} UTC；数据截至 {res['latest_complete_day']}；数据来源：Binance 现货日线静态镜像 + OKX 最新日（{manifest['symbols']['BTCUSDT']['validation']['sources']}），OKX 交叉比对中位偏差 {manifest['symbols']['BTCUSDT']['cross_check_okx']['rel_close_diff_median']*1e4:.1f} bps。订单流覆盖：{res['order_flow_coverage']}。宏观事件：{res['macro_events_note']}。</p>",
            f"<h2>最新建议（主配置 V4 = 趋势回调 + 突破回踩，退出 B，1% 风险 / 70% 配置 / 30% 回撤约束；截至 {latest['as_of']}）</h2>", cur,
            "<h2>价格、结构区域与历史买卖点</h2><p>绿色三角 = 当时已确认的买入信号（次日开盘成交）；红色三角 = 规则触发的卖出/减仓；橙色横线 = 入场时事前计划的 T1；黑叉 = 入场时的失效价；灰圈 = 被盈亏比/宏观/回撤约束拒绝的候选。区域为<b>最新一日</b>的支撑/阻力（事后视角，仅用于观察当前位置）。</p>"]
    for f in figs[:2]:
        html.append(f.to_html(full_html=False, include_plotlyjs=("cdn" if not embed and len(html) < 6 else (True if embed and len(html) < 6 else False))))
    html.append("<h2>净值与回撤（与买入持有比较）</h2>"); html.append(figs[2].to_html(full_html=False, include_plotlyjs=False))
    html.append("<h2>全样本比较（单侧 25 bps，按清仓净收益排序）</h2>" + t_full + "<h2>2024–2026（已被查看的历史测试段）</h2>" + t_test)
    html.append("<h2>主配置交易记录（V4 退出 B，全样本 25 bps）</h2>" + t_trades)
    if res.get("sensitivity"):
        se = pd.DataFrame(res["sensitivity"]); html.append("<h2>风险预算 / 配置 / 回撤约束 / 退出敏感性（V4，25 bps）</h2>" + tbl(se, ["exit_mode", "risk_pct", "max_alloc", "dd_limit", "liquidation_net_profit_usdt", "max_drawdown", "longest_drawdown_days", "n_fills", "time_flat", "total_cost_usdt"], {"risk_pct": pct, "max_alloc": pct, "dd_limit": lambda v: "无" if v is None or (isinstance(v, float) and pd.isna(v)) else pct(v), "liquidation_net_profit_usdt": money, "max_drawdown": pct, "time_flat": pct, "total_cost_usdt": money}))
    html.append(f"<p class='note'>预登记：{res['preregistration']}；全部参数固定，未按结果调整。页面只读。</p></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(html), encoding="utf-8"); return str(out)
