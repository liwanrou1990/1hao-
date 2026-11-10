"""第十六阶段独立研究看板：“BTC矿工成本与长期投资周期”（静态 HTML，离线 plotly.js 内嵌；不修改第十四阶段看板）。
每个价格都带标签：历史结果（回测）/ 当前研究估计 / 前瞻信号（本阶段无）。未通过验证 → 只显示“研究性观察”，不显示可执行买入。"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import data16
from .current16 import readings
from .phase16 import OUT_DIR

TAG = {"hist": '<span class="tag hist">历史结果（回测）</span>', "est": '<span class="tag est">当前研究估计</span>', "fwd": '<span class="tag fwd">前瞻信号：无</span>', "obs": '<span class="tag obs">研究性观察</span>'}
LAYOUT = dict(paper_bgcolor="#171e2e", plot_bgcolor="#171e2e", font=dict(color="#e6ebf5", size=12), title_font=dict(size=14), margin=dict(l=55, r=15, t=64, b=40), legend=dict(orientation="h", y=-0.18), hovermode="x unified",
              xaxis=dict(gridcolor="#2a354d", zerolinecolor="#2a354d"), yaxis=dict(gridcolor="#2a354d", zerolinecolor="#2a354d"))


def _fmt(v, d=0):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{d}f}"


def _card(k, v, s="", tag=""):
    return f'<div class="card"><div class="k">{k} {tag}</div><div class="v">{v}</div><div class="s">{s}</div></div>'


def _fig_html(fig, first):
    fig.update_layout(**LAYOUT)
    return fig.to_html(full_html=False, include_plotlyjs=("inline" if first else False), config={"responsive": True, "displaylogo": False, "displayModeBar": False})


def build(res: dict | None = None, online: bool = True, out: Path | None = None) -> Path:
    out = Path(out or OUT_DIR); res = res or json.loads((OUT_DIR / "phase16_results.json").read_text(encoding="utf-8"))
    curves = json.loads((OUT_DIR / "phase16_curves_10bps.json").read_text(encoding="utf-8")) if (OUT_DIR / "phase16_curves_10bps.json").exists() else {}
    R = readings(online=online); d = data16.load_btc_daily(); cost = data16.miner_cost(); f = data16.signal_frame(d)
    f = f[f.index >= "2014-01-01"]; cost = cost[cost.index >= "2014-01-01"]
    figs = []
    # 1 价格与成本区间
    g = go.Figure(); g.add_trace(go.Scatter(x=cost.index, y=cost["cost_high"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    g.add_trace(go.Scatter(x=cost.index, y=cost["cost_low"], fill="tonexty", fillcolor="rgba(39,194,129,0.18)", line=dict(width=0), name="现金成本区间（低–高，研究估计）"))
    g.add_trace(go.Scatter(x=cost.index, y=cost["cost_mid"], line=dict(color="#27c281", width=1.5), name="现金成本中值（研究估计）"))
    g.add_trace(go.Scatter(x=cost.index, y=cost["cost_allin_mid"], line=dict(color="#f5b942", width=1, dash="dot"), name="综合成本中值（仅展示）"))
    g.add_trace(go.Scatter(x=f.index, y=f["close"], line=dict(color="#4c9aff", width=1.4), name="BTC 收盘价"))
    for h in data16.HALVINGS[1:]:
        g.add_vline(x=h, line=dict(color="#8b98b5", dash="dash", width=1))
    g.update_layout(title="BTC 价格与矿工现金成本区间<br><sup>对数坐标；虚线 = 减半；成本为研究估计</sup>", yaxis_type="log", height=420); figs.append(g)
    # 2 成本比
    g = go.Figure(); g.add_trace(go.Scatter(x=f.index, y=f["r"], line=dict(color="#4c9aff", width=1.2), name="价格 / 成本中值"))
    for lv, col in ((0.8, "#27c281"), (0.9, "#27c281"), (1.0, "#27c281"), (1.1, "#8b98b5"), (2.0, "#f0556a"), (2.5, "#f0556a"), (3.0, "#f0556a"), (3.5, "#f0556a")):
        g.add_hline(y=lv, line=dict(color=col, dash="dot", width=1))
    g.update_layout(title="价格 / 成本中值<br><sup>绿线 = 建仓层 0.8–1.0；红线 = 退出层 2.0–3.5</sup>", yaxis_type="log", height=360); figs.append(g)
    # 3 历史分布
    rr = f["r"].dropna(); g = go.Figure(); g.add_trace(go.Histogram(x=np.log10(rr), nbinsx=60, marker_color="#4c9aff", name="2014 年以来日分布"))
    g.add_vline(x=float(np.log10(R["ratio"]["price_over_mid"])), line=dict(color="#f5b942", width=2)); pct = float((rr <= R["ratio"]["price_over_mid"]).mean())
    g.update_layout(title=f"成本比历史分布<br><sup>log10；黄线 = 当前；历史上 {pct:.0%} 的日子低于当前</sup>", height=320, xaxis_title="log10(价格 / 成本中值)"); figs.append(g)
    # 4 净值对比（全样本，10 bps）
    g = go.Figure(); pick = ["BH_BTC", "B1_BTC_SMA200", "A0_X2", "C_X3", "D_X2", "D_X4", "A_X4"]
    for n in pick:
        c = curves.get(f"full|{n}")
        if c:
            g.add_trace(go.Scatter(x=c["ts"], y=c["liquidation"], name=n, line=dict(width=1.3)))
    b2 = curves.get("valid|B2_modelB_state_machine")
    g.update_layout(title="历史净值（2014-01 → 最新）<br><sup>假设清仓口径；10,000 USDT 起；单边 10 bps；历史结果</sup>", yaxis_type="log", height=420); figs.append(g)
    # 5 回撤
    g = go.Figure()
    for n in pick:
        c = curves.get(f"full|{n}")
        if c:
            g.add_trace(go.Scatter(x=c["ts"], y=[x * 100 for x in c["drawdown"]], name=n, line=dict(width=1)))
    g.update_layout(title="历史回撤（%）", height=320); figs.append(g)
    # 6 后段检验（2022 →）含 B2
    g = go.Figure()
    for n in ["BH_BTC", "B1_BTC_SMA200", "B2_modelB_state_machine", "D_X2", "D_X3", "A0_X2"]:
        c = curves.get(f"test|{n}")
        if c:
            g.add_trace(go.Scatter(x=c["ts"], y=c["liquidation"], name=n, line=dict(width=1.3)))
    g.update_layout(title="后段检验 2022-01 → 最新<br><sup>非盲测；A/A0/B/C 全程未触发，一直持有 USDT</sup>", height=380); figs.append(g)
    plots = "".join(f'<div class="plot">{_fig_html(x, i == 0)}</div>' for i, x in enumerate(figs))

    cb = R["cost_band"]; ra = R["ratio"]; wk = R["weekly"]; es = R["entry_status"]; inv = R["invalidation"]; val = R["valuation"]; hv = R["halving"]; live = R["live"]
    cards = "".join([
        _card("BTC 现价", _fmt(R["price_used"], 1) + " USDT", f'{"OKX 实时 " + live["ts_utc"][:16] + " UTC" if live.get("price") else "最新已收盘日线 " + R["data_as_of_daily_close"]}'),
        _card("矿工现金成本区间", f'{_fmt(cb["low"])} – {_fmt(cb["high"])}', f'中值 {_fmt(cb["mid"])} · 综合成本中值 {_fmt(cb["allin_mid"])} · 可信度 {cb["reliability"]} · 链上截至 {R["onchain_and_cost_as_of"]}', TAG["est"]),
        _card("价格 / 成本", f'{ra["price_over_mid"]:.2f}×（中值）', f'相对低端 {ra["price_over_low"]:.2f}× · 相对高端 {ra["price_over_high"]:.2f}×', TAG["est"]),
        _card("减半周期阶段", f'{hv["phase"]:.2f}', f'距 {hv["last"]} 已 {hv["days_since"]} 天（背景标签，非规律）'),
        _card("周线趋势", "40 周 SMA 上方" if wk["above_40w"] else "40 周 SMA 下方", f'周收盘 {_fmt(wk["w_close"])}（{wk["week_close_date"]}）· 20 周 {_fmt(wk["sma20w"])}{"↑" if wk["sma20w_rising"] else "↓"} · 40 周 {_fmt(wk["sma40w"])}'),
        _card("估值背景", f'MVRV {val["mvrv"]:.2f}', f'估计实现价格 {_fmt(val["realized_price"])} · Puell {val["puell"]:.2f}', TAG["est"]),
        _card("候选长期建仓区域", " / ".join(_fmt(x["price"]) for x in R["candidate_entry_zone"]["levels"]), "成本比 1.1 / 1.0 / 0.9 / 0.8 对应价格（随成本移动）", TAG["est"]),
        _card("买入确认状态", "未触发 · 继续观察" if not (es["B_would_buy"] or es["C_would_buy"] or es["A_A0_D_triggered_now"]) else "研究规则条件出现（仍不可执行）", f'成本区触发 {"是" if es["A_A0_D_triggered_now"] else "否"} · 近 540 天武装 {"是" if es["armed_B_C_last_540d"] else "否"} · 周线条件 {"是" if es["B_weekly_condition"] else "否"} · 支撑 / 回踩确认 {"是" if es["C_confirmation_today"] else "否"}', TAG["obs"]),
        _card("结构失效位置", (_fmt(inv["last_confirmed_daily_swing_low"]) + ("" if inv["applicable"] else "（不适用：已高于现价，结构已失效）")) if inv["last_confirmed_daily_swing_low"] else "—", f'周线趋势失效参考：40 周 SMA {_fmt(inv["weekly_trend_failure_level_sma40w"])}', TAG["est"]),
        _card("候选牛市退出区域", " / ".join(_fmt(x["price"]) for x in R["candidate_exit_zones"]["by_cost_ratio"]), "成本比 2.0 / 2.5 / 3.0 / 3.5；MVRV 2.5 / 3.0 / 3.5 / 4.0 → " + " / ".join(_fmt(x["price"]) for x in R["candidate_exit_zones"]["by_mvrv"]), TAG["est"]),
        _card("交易成本", "单边 10 / 25 / 50 bps", "往返 20 / 50 / 100 bps；买卖两侧都扣；期末按假设清仓再扣一次"),
        _card("风险收益比", "不适用" if R["reward_risk_to_first_cost_exit"]["10"] is None else f'{R["reward_risk_to_first_cost_exit"]["10"]:.2f}', "至第一个成本退出层 / 至结构失效位；结构失效位高于现价时不计算", TAG["est"]),
        _card("前瞻信号", "无", "第十六阶段 25 个组合均未通过预登记判定 → 只记录研究性观察", TAG["fwd"])])
    # 表格
    Rw = {(x["name"], x["window"], x["cost_one_side_bps"]): x for x in res["rows"]}
    def trow(n, w):
        x = Rw.get((n, w, 10));
        return "" if not x else f'<tr><td>{n}</td><td>{_fmt(x["liquidation_net_profit_usdt"])}</td><td>{x["max_drawdown"]:.1%}</td><td>{x["n_fills"]}</td><td>{x.get("n_campaigns_closed", 0)}</td><td>{x["time_flat"]:.0%}</td></tr>'
    names = ["BH_BTC", "B1_BTC_SMA200", "B2_modelB_state_machine", "A_X4", "A0_X2", "A0_X4", "B_X4", "C_X3", "D_X1", "D_X2", "D_X4"]
    tables = ""
    for w, title in (("valid", "验证 2018–2021"), ("test", "后段检验 2022 → 最新（非盲测）"), ("full", "全样本 2014 → 最新")):
        tables += f'<h3>{title} · 单边 10 bps {TAG["hist"]}</h3><div class="tbl"><table><tr><th>策略</th><th>假设清仓净收益</th><th>最大回撤</th><th>成交</th><th>闭合战役</th><th>空仓</th></tr>{"".join(trow(n, w) for n in names)}</table></div>'
    eps = res["episodes"]["1.0"]
    ep_rows = "".join(f'<tr><td>{e["first_date"]}</td><td>{_fmt(e["first_price"])}</td><td>{_fmt(e["first_cost_mid"])}</td><td>{e["min_r"]:.2f}</td><td>{e["min_date"]}</td><td>{e["days_to_min"]}</td><td>{e["further_drawdown"]:.1%}</td><td>{e["reliability"]}</td></tr>' for e in eps)
    ep_tbl = f'<h3>历史成本区事件（价格 ≤ 成本中值）{TAG["hist"]}</h3><div class="tbl"><table><tr><th>首次进入</th><th>价格</th><th>成本中值</th><th>最低成本比</th><th>此后最低收盘日</th><th>继续下跌天数</th><th>继续下跌幅度</th><th>成本可信度</th></tr>{ep_rows}</table></div>'
    fills = pd.read_csv(OUT_DIR / "phase16_fills_full_10bps.csv") if (OUT_DIR / "phase16_fills_full_10bps.csv").exists() else pd.DataFrame()
    fl = fills[fills["strategy"].isin(["A0_X2", "D_X4"])] if len(fills) else fills
    fl_rows = "".join(f'<tr><td>{r.strategy}</td><td>{r.ts}</td><td>{"买入" if r.side == "BUY" else "卖出"}</td><td>{_fmt(r.fill_price, 2)}</td><td>{_fmt(r.fee, 2)}</td><td>{html.escape(str(r.rule))}</td></tr>' for r in fl.itertuples())
    fl_tbl = f'<h3>交易明细示例（A0_X2、D_X4；全部明细见 phase16_fills_full_10bps.csv）{TAG["hist"]}</h3><div class="tbl"><table><tr><th>策略</th><th>成交日</th><th>方向</th><th>成交价</th><th>手续费</th><th>依据</th></tr>{fl_rows}</table></div>'
    rel = res["cost_model_reliability"]
    from ..pa15 import factors15
    p15 = factors15.btc_factors()["miner_cost_mid"].dropna(); p15_ratio = R["price_used"] / float(p15.iloc[-1])
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>BTC矿工成本与长期投资周期</title>
<style>:root{{--bg:#0f1420;--panel:#171e2e;--line:#2a354d;--text:#e6ebf5;--muted:#8b98b5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden;overflow-wrap:anywhere}}
main{{max-width:1280px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:6px 0}}h2{{font-size:16px;margin:20px 0 8px}}h3{{font-size:14px;color:var(--muted);margin:16px 0 6px}}
.note{{background:rgba(245,185,66,.12);border:1px solid rgba(245,185,66,.35);color:#f3d58a;padding:8px 10px;border-radius:8px;font-size:12.5px;margin:8px 0}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.grid>*{{min-width:0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow:hidden}}.card .k{{color:var(--muted);font-size:12px}}.card .v{{font-size:18px;font-weight:600;margin-top:2px}}.card .s{{color:var(--muted);font-size:12px;margin-top:2px}}
.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;color:#fff;vertical-align:middle}}.tag.hist{{background:#6e40c9}}.tag.est{{background:#8b6d00}}.tag.fwd{{background:#cf222e}}.tag.obs{{background:#2b7a4b}}
.plot{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;min-width:0;overflow:hidden}}.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}td:first-child,th:first-child{{text-align:left}}td:last-child{{white-space:normal;text-align:left;min-width:200px}}
.foot{{color:var(--muted);font-size:12px;margin-top:20px;border-top:1px solid var(--line);padding-top:8px}}
@media (max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media (max-width:600px){{main{{padding:10px}}.card .v{{font-size:15px}}h1{{font-size:17px}}}}</style></head><body><main>
<h1>BTC矿工成本与长期投资周期 <span class="tag obs">研究性观察</span></h1>
<div class="note">第十六阶段独立研究页面（不是第十四阶段生产看板）。本页没有任何可执行买卖信号：预登记的 25 个建仓 × 退出组合在验证与后段检验中均未同时胜过 BTC 买入持有与 BTC SMA200，判定为“无新增优势”。价格标签：{TAG["hist"]} = 历史回测结果；{TAG["est"]} = 基于当前数据的模型估计；{TAG["fwd"]} = 本阶段无前瞻信号。生成于 {res["generated_at_utc"][:16]} UTC；日线截至 {R["data_as_of_daily_close"]}，链上与成本截至 {R["onchain_and_cost_as_of"]}。</div>
<h2>当前读数</h2><div class="grid">{cards}</div>
<div class="note">矿工成本是价格无关的“矿机代际”模型估计（算力 × 机队效率 × 电价 ÷ 日产出），没有真实披露数据或剑桥 CBECI 可供校准；与第十五阶段独立模型的对数相关 {rel["corrected"]["vs_phase15_log_corr"]:.3f}、中位数比 {rel["corrected"]["vs_phase15_median_ratio"]:.2f}。两种模型对当前价格 / 成本的判断差异很大（本模型约 {ra["price_over_mid"]:.2f}×，第十五阶段锚点模型约 {p15_ratio:.2f}×），说明“现在离成本有多远”本身高度依赖假设。预登记原版成本模型因 4 个单日跳变未通过可靠性检查，现用的是事后修正版。</div>
<h2>历史分析</h2>{plots}{ep_tbl}{tables}{fl_tbl}
<div class="foot">数据：BTC 日线 2014-01 → 2015-07-19 CoinMetrics 收盘价（无开高低量）、2015-07-20 → 2017-08-16 Coinbase、此后第十阶段数据集；算力 / 难度 / 矿工收入 blockchain.info；发行 / 手续费 / MVRV CoinMetrics 社区层（滞后 1 日）；矿机规格为公开资料整理；FRED 宏观数据只作背景。不可得：矿企真实现金成本披露、剑桥 CBECI、矿工链上资金流、持有者分层成本。真实交易禁用。</div>
</main></body></html>"""
    p = out / "dashboard16_btc_miner_cycle.html"; p.write_text(page, encoding="utf-8"); return p


if __name__ == "__main__":
    import sys
    print(build(online="--offline" not in sys.argv))
