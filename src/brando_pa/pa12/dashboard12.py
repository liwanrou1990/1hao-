"""第十二阶段只读页面：沿用 Phase 11 页面结构（Plotly 静态 HTML），增加核心 / 机会仓位、BTC/ETH 当前趋势、候选回调区域、买盘确认状态、结构失效位置、下一目标区、
总 USDT 净资产、累计手续费、最大回撤、原 B2 与买入持有对照；明确区分 历史回测 / 前瞻模拟（云端副本，非 Mac 权威） / 真实账户（无，禁用）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..config import cfg
from ..holding.data import load_dataset
from ..pa11.engine11 import MarketContext
from ..pa11.signals import VolumeAndOrderFlowConfirmation
from ..pa11.zones import SupportResistanceZones
from .engine12 import Cfg12, CompositeBacktester

SYMS = ["BTCUSDT", "ETHUSDT"]
MAIN = Cfg12(core="B2", core_frac=0.7, pa_add=("A",), exit_mode="structure_reduce", risk_pct=0.01, name="F_B2_pullback_structure_cf0.7_r0.01")


def build_dashboard12(results_dir: Path | None = None, out: Path | None = None, embed: bool = True) -> str:
    results_dir = Path(results_dir or "reports_out/brando_pa/phase12_core_pa"); out = Path(out or (results_dir / "dashboard_phase12.html"))
    res = json.loads((results_dir / "phase12_results.json").read_text()); rows = pd.DataFrame(res["rows"]); curves = json.loads((results_dir / "curves.json").read_text()) if (results_dir / "curves.json").exists() else {}
    bars = load_dataset(); ctx = MarketContext(bars); rp = cfg().get("trendsys", {}).get("risk", {}); latest = str(ctx.idx[-1].date())
    main = CompositeBacktester(bars, MAIN, 20.0, 5.0, "2018-01-01", latest, ctx=ctx, risk_params=rp).run(); b2 = CompositeBacktester(bars, Cfg12(core="B2", name="A_B2"), 20.0, 5.0, "2018-01-01", latest, ctx=ctx, risk_params=rp).run()
    i = ctx.n - 1; cards = []
    for s in SYMS:
        cl = ctx.close[s][i]; zones = ctx.zones[s][i]; st = ctx.st[s][i]; ds = ctx.ds[s][i]; wt = ctx.wt[s][i]; a = ctx.det_res[s]["A"][i]; bconf = VolumeAndOrderFlowConfirmation.bar(ctx.d[s], i)
        sup = SupportResistanceZones.nearest_support(zones, cl); res_ = SupportResistanceZones.resistances_above(zones, cl)
        last_nav = main.nav.iloc[-1]; dec = main.decisions[main.decisions.symbol == s].tail(1)
        cards.append(f"<div class='card'><h3>{s} · 收盘 {cl:,.0f} · 周线 {wt['trend']} · 日线 {'close > SMA200' if ds['above_sma200'] else 'close ≤ SMA200'}{'（SMA200 上行）' if ds['sma200_rising'] else ''} · 熔断状态 {last_nav['risk_state']}</h3>"
                     f"<p>持仓 {last_nav['holdings'][s]:.6f} {s[:3]}（主配置 F：core_frac 0.7）· 组合核心暴露 {last_nav['core_exposure']:.0%} · 机会暴露 {last_nav['opp_exposure']:.0%}</p>"
                     f"<p>候选回调区域（最近已确认支撑）：{f'[{sup.low:,.0f}, {sup.high:,.0f}] 强度 {sup.strength}' if sup else '无'} · 买盘确认状态：{'已触发' if a['signal'] else a['reason'][:60]} · 量比 {bconf['vol_rel20']:.2f}，收盘位置 {bconf['close_pos']:.2f}</p>"
                     f"<p>结构失效位置：最近已确认摆动低点 {st['last_low']:,.0f} · 下一目标区：{f'[{res_[0].low:,.0f}, {res_[0].high:,.0f}] 强度 {res_[0].strength}' if res_ else '上方无已确认阻力（创新高区，动态退出）'}</p>"
                     f"<p class='act {dec.iloc[0]['action'] if len(dec) else 'WAIT'}'>{dec.iloc[0]['action'] if len(dec) else '—'}</p><p>{dec.iloc[0]['reason'] if len(dec) else ''}</p></div>")
    f = main.final; fb = b2.final
    summary = (f"<div class='card'><h3>主配置 F（B2 核心 0.7 + 回调加仓 + 结构退出，1% 风险，单侧 25 bps）· 历史回测 2018-01-01 → {latest}</h3>"
               f"<p>当前总 USDT 净资产 {f['equity']:,.0f}（假设清仓 {f['liquidation_usdt']:,.0f}）· 累计手续费 {f['fees_usdt']:,.0f} + 滑点 {f['slippage_usdt']:,.0f} · 最大回撤 {main.nav.drawdown.min():.1%} · 成交 {f['n_fills']} · 机会加仓 {f['n_opp_adds']}</p>"
               f"<p>对照：原 B2 净资产 {fb['equity']:,.0f}（回撤 {b2.nav.drawdown.min():.1%}）；BTC 买入持有 / ETH 买入持有见下表。</p></div>")
    # 净值图：主配置 vs B2 vs 买入持有 + 核心/机会暴露
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35], vertical_spacing=0.04, subplot_titles=("清仓口径净值（USDT，全样本，单侧 25 bps）", "主配置暴露：核心 / 机会"))
    for key, cv in curves.items():
        w, cost, g = key.split("|")
        if w != "full" or g not in ("A_B2", "B_SMA200_btc", "G_BUYHOLD_BTC", "G_BUYHOLD_ETH", MAIN.name, "E_B2_structure_reduce"):
            continue
        df = pd.DataFrame(cv); df["ts"] = pd.to_datetime(df["ts"]); fig.add_trace(go.Scatter(x=df["ts"], y=df["liquidation"], name=g, line=dict(width=1.2)), row=1, col=1)
    nav = main.nav; fig.add_trace(go.Scatter(x=nav["ts"], y=nav["core_exposure"], name="核心暴露", fill="tozeroy", line=dict(width=0.5)), row=2, col=1); fig.add_trace(go.Scatter(x=nav["ts"], y=nav["core_exposure"] + nav["opp_exposure"], name="核心+机会暴露", line=dict(width=0.8)), row=2, col=1)
    fig.update_yaxes(type="log", row=1, col=1); fig.update_layout(height=620, legend=dict(orientation="h"), margin=dict(l=40, r=20, t=60, b=30))
    # 价格 + 加仓点
    figs2 = []
    for s in SYMS:
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); b = b[b["ts"] >= "2018-01-01"]; fg = go.Figure(); fg.add_trace(go.Scatter(x=b["ts"], y=b["close"], name="收盘", line=dict(width=1))); fg.add_trace(go.Scatter(x=b["ts"], y=b["close"].rolling(200).mean(), name="SMA200", line=dict(width=1, dash="dot")))
        t = main.trades[main.trades.symbol == s] if len(main.trades) else pd.DataFrame(); ad = main.adds[main.adds.symbol == s] if len(main.adds) else pd.DataFrame()
        if len(ad):
            fg.add_trace(go.Scatter(x=pd.to_datetime(ad["actual_entry_ts"]), y=ad["actual_entry_price"], mode="markers", name="机会加仓（当时已确认信号，次日开盘成交）", marker=dict(symbol="triangle-up", size=11, color="green"))); fg.add_trace(go.Scatter(x=pd.to_datetime(ad["actual_entry_ts"]), y=ad["invalidation"], mode="markers", name="加仓时失效价（事前）", marker=dict(symbol="x", size=8, color="black")))
        if len(t):
            core_e = t[(t.book == "core") & t.entry_ts.notna()].drop_duplicates("entry_ts"); fg.add_trace(go.Scatter(x=core_e["entry_ts"], y=core_e["entry_price"], mode="markers", name="核心入场", marker=dict(symbol="circle", size=7, color="blue"))); fg.add_trace(go.Scatter(x=t["exit_ts"], y=t["exit_price"], mode="markers", name="卖出 / 减仓", marker=dict(symbol="triangle-down", size=9, color="red"), text=t["reason"], hovertemplate="%{x|%Y-%m-%d} %{y:.0f}<br>%{text}"))
        fg.update_yaxes(type="log"); fg.update_layout(title=f"{s} · 主配置 F 的核心入场 / 机会加仓 / 退出（历史回测）", height=440, legend=dict(orientation="h"), margin=dict(l=40, r=20, t=60, b=30)); figs2.append(fg)
    def tbl(df, cols, fmt=None):
        fmt = fmt or {}; head = "".join(f"<th>{c}</th>" for c in cols); body = "".join("<tr>" + "".join(f"<td>{(fmt.get(c, lambda v: v))(r.get(c))}</td>" for c in cols) + "</tr>" for _, r in df.iterrows()); return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    money = lambda v: "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:,.0f}"; pct = lambda v: "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v*100:.1f}%"
    sel = rows[(rows.window == "full") & (rows.cost_bps == 25)].sort_values("liquidation_net_profit_usdt", ascending=False)
    t_full = tbl(sel, ["group", "final_equity", "liquidation_net_profit_usdt", "max_drawdown", "longest_drawdown_days", "n_fills", "total_cost_usdt", "avg_core_exposure", "avg_opp_exposure", "time_flat", "upside_participation_exposure_when_btc_above_sma200", "bull_month_capture_vs_btc_bh", "top5_share_of_closed_pnl"], {"final_equity": money, "liquidation_net_profit_usdt": money, "max_drawdown": pct, "total_cost_usdt": money, "avg_core_exposure": pct, "avg_opp_exposure": pct, "time_flat": pct, "upside_participation_exposure_when_btc_above_sma200": pct, "bull_month_capture_vs_btc_bh": pct, "top5_share_of_closed_pnl": pct})
    sel2 = rows[(rows.window == "test_seen") & (rows.cost_bps == 25)].sort_values("liquidation_net_profit_usdt", ascending=False)
    t_test = tbl(sel2, ["group", "final_equity", "liquidation_net_profit_usdt", "max_drawdown", "n_fills", "time_flat"], {"final_equity": money, "liquidation_net_profit_usdt": money, "max_drawdown": pct, "time_flat": pct})
    fwd = Path("reports_out/brando_pa/phase8_forward/leaderboard.json"); fwd_html = "<p>无云端副本</p>"
    if fwd.exists():
        lb = json.loads(fwd.read_text()); fwd_html = tbl(pd.DataFrame(lb["cost"]), ["account", "book_equity_usdt", "est_liquidation_net_profit_usdt", "max_drawdown", "as_of_utc"], {"book_equity_usdt": money, "est_liquidation_net_profit_usdt": money, "max_drawdown": pct}) + f"<p>来源：云容器内的前瞻记录副本（{lb['generated_at_utc'][:16]} UTC），与 Mac 运行账户在 2026-11-08 06:02 UTC 后分叉；Mac 记录为权威，本页不读取 Mac。</p>"
    html = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'><title>Brando Phase 12 研究页面（只读）</title><style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:20px;color:#222}table{border-collapse:collapse;font-size:12px;margin:8px 0}th,td{border:1px solid #ddd;padding:3px 6px;text-align:right}th{background:#f5f5f5}td:first-child,th:first-child{text-align:left}.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0;background:#fafafa}.act{font-size:20px;font-weight:700}.BUY,.ADD{color:#1a7f37}.WAIT{color:#9a6700}.HOLD{color:#0969da}.EXIT,.REDUCE{color:#cf222e}.note{background:#fff8c5;padding:8px;border-radius:6px}.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;color:#fff;margin-right:6px}.bt{background:#6e40c9}.fw{background:#0969da}.rl{background:#cf222e}</style></head><body>",
            f"<h1>Brando Price Action · 第十二阶段研究页面（只读）</h1><p><span class='tag bt'>历史回测</span>本页主体；<span class='tag fw'>前瞻模拟</span>仅底部云端副本表；<span class='tag rl'>真实账户</span>无（真实交易禁用、无实现）。</p>",
            f"<p class='note'>类别：历史回测 · 开发性分析（2018–2026 已被多阶段使用，非盲测）；日收盘决策、次日开盘成交；成本一律为<b>单侧</b> bps。生成 {res['generated_at_utc'][:19]} UTC；数据截至 {res['latest_complete_day']}；预登记 {res['preregistration']}。</p>",
            "<h2><span class='tag bt'>历史回测</span>当前状态（截至最新已收盘日）</h2>", summary, *cards,
            "<h2>净值与暴露</h2>", fig.to_html(full_html=False, include_plotlyjs=(True if embed else "cdn")),
            "<h2>价格与买卖点（蓝圈 = 核心入场，绿三角 = 机会加仓，黑叉 = 加仓时失效价，红三角 = 卖出/减仓；均为当时已确认信号）</h2>", *[g.to_html(full_html=False, include_plotlyjs=False) for g in figs2],
            "<h2>全样本比较（单侧 25 bps）</h2>", t_full, "<h2>2024–2026（已被查看的历史测试段）</h2>", t_test,
            "<h2><span class='tag fw'>前瞻模拟</span>Mac 三账户（云端副本，非权威）</h2>", fwd_html,
            f"<p class='note'>只读页面；不写任何账户；真实交易禁用。</p></body></html>"]
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(html), encoding="utf-8"); return str(out)
