"""“BTC/USDT 资金配置中心（研究账户 100,000 USDT）”：第二十阶段独立研究页面（静态 HTML，离线 plotly.js）。
另提供 build_phase19_100k()：第十九阶段研究页面按 100,000 USDT 研究本金重建（输出到第二十阶段目录，不覆盖 10,000 USDT 版本）。
不修改第十四阶段生产看板，不读取也不改动 Mac 三个模拟账户。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..pa19 import dashboard19
from ..pa19.dashboard19 import LAYOUT, _f, _fig
from . import MAC_ACCOUNTS_INITIAL_USDT, RESEARCH_INITIAL_USDT
from . import phase20 as P
from .alloc20 import MODES, WARN
from .execution20 import attach

TAG = {"hist": '<span class="tag hist">历史回测</span>', "res": '<span class="tag res">研究账户 100,000 USDT</span>', "mac": '<span class="tag mac">Mac 运行账户 10,000 USDT（未改动）</span>'}
LABEL = {"ALLOC_0_N": "0% BTC（全 USDT）", "BH_BTC": "BTC 买入持有", "B1_BTC_SMA200": "BTC SMA200", "B2_original": "原 B2（2017 起）", "A_X4": "第十九阶段 A_X4", "ABC_X2": "第十九阶段 ABC_X2", "A_X2": "第十九阶段 A_X2"}
COLORS = {"ALLOC_25_Y": "#27c281", "ALLOC_50_Y": "#f5b942", "ALLOC_75_Y": "#f08a3c", "BH_BTC": "#4c9aff", "B1_BTC_SMA200": "#9b7bff", "B2_original": "#f0556a", "ALLOC_0_N": "#8b98b5"}


def label(n: str) -> str:
    if n in LABEL:
        return LABEL[n]
    if n.startswith("ALLOC_"):
        _, w, m = n.split("_"); return f"{w}% BTC · {MODES[m]}" if w not in ("0", "100") else (f"{w}% BTC")
    return n


def _pct(v, d=1):
    return "—" if v is None else f"{v:.{d}%}"


def _r(v):
    return "—" if v is None else f"{v:.2f}"


def _curve(ctx, name, a, b=None):
    """重算净值曲线（清仓口径，研究本金）。"""
    fee, slip = P.COSTS20[15]; b = b or ctx.latest
    if name.startswith("ALLOC_"):
        from .alloc20 import AllocBacktester
        _, w, m = name.split("_"); res = AllocBacktester(ctx.btc, int(w) / 100, m, fee, slip, a, b, ctx.initial, ctx.liq_btc).run()
    elif name == "B2_original":
        res = P.phase18.run_variant(ctx.b2bars, "V0_original", 15, a, b, ctx.c, initial=ctx.initial, hook=lambda x: attach(x, ctx.liq_b2))[1]
    else:
        spec = {"name": name, "assets": ["BTCUSDT"], "mode": "buyhold"} if name == "BH_BTC" else {"name": name, "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}
        res = attach(P.HoldingBacktester({"BTCUSDT": ctx.btc}, spec, fee, slip, a, b, initial=ctx.initial, sma=200), ctx.liq_btc).run()
    s = res.nav.copy(); s["ts"] = pd.to_datetime(s["ts"], utc=True); return s.set_index("ts")["liquidation"].astype(float)


def build(res_dir: Path | None = None, out: Path | None = None, ctx=None) -> Path:
    rd = Path(res_dir or P.OUT_DIR); out = Path(out or rd); r = json.loads((rd / "phase20_results.json").read_text(encoding="utf-8")); ctx = ctx or P.Ctx()
    R = r["results"]; V = r["verdict"]["per_config"]; best = r["verdict"]["best_within_budget_by_return_over_mdd"]; init = r["research_initial_usdt"]; latest = r["latest_complete_day"]
    allocs = [n for n in P.all_names() if n.startswith("ALLOC_")]; figs = []
    # 1 全样本净值曲线
    full_names = ["ALLOC_0_N", "ALLOC_25_Y", "ALLOC_50_Y", "ALLOC_75_Y", "BH_BTC", "B1_BTC_SMA200"]; curves = {n: _curve(ctx, n, "2014-01-01") for n in full_names}
    g = go.Figure()
    for n, s in curves.items():
        g.add_trace(go.Scatter(x=s.index, y=s.values, name=label(n), line=dict(width=1.4, color=COLORS.get(n))))
    g.update_layout(title=f"研究账户净资产 2014 → {latest}<br><sup>历史回测；初始 {init:,.0f} USDT 真实重算；清仓口径；单边 15 bps + 流动性冲击；对数坐标</sup>", yaxis_type="log", height=440); figs.append(g)
    # 2 动态回撤与预警线
    g = go.Figure()
    for n in ("ALLOC_25_Y", "ALLOC_50_Y", "BH_BTC"):
        s = curves[n]; dd = s / s.cummax() - 1; g.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, name=label(n), line=dict(width=1.2, color=COLORS.get(n))))
    for th, col in zip(WARN, ("#f3d58a", "#f5b942", "#f08a3c", "#f0556a")):
        g.add_hline(y=-th * 100, line=dict(color=col, dash="dot", width=1), annotation_text=("−50% 研究预算" if th == 0.50 else ""), annotation_position="bottom left", annotation_font_size=10)
    g.update_layout(title="动态回撤（距历史最高净资产）与预警线<br><sup>虚线自上而下：35 / 40 / 45% 预警、50% 研究预算（不是保证止损）；回撤按每日清仓口径计算</sup>", yaxis_title="%", height=420); figs.append(g)
    # 3 2017 → 含原 B2
    g = go.Figure()
    for n in ("ALLOC_25_Y", "ALLOC_50_Y", "BH_BTC", "B1_BTC_SMA200", "B2_original"):
        s = _curve(ctx, n, "2017-01-01"); g.add_trace(go.Scatter(x=s.index, y=s.values, name=label(n), line=dict(width=1.4, color=COLORS.get(n))))
    g.update_layout(title=f"研究账户净资产 2017 → {latest}（含原 B2）<br><sup>历史回测；初始 {init:,.0f} USDT；B2 含 ETH，其余只做 BTC</sup>", yaxis_type="log", height=420); figs.append(g)
    # 4 滚动 4 年起点分布
    roll = r["rolling"]; g = go.Figure()
    for n in ("ALLOC_0_N", "ALLOC_25_Y", "ALLOC_25_D", "ALLOC_50_Y", "ALLOC_75_Y", "BH_BTC", "B1_BTC_SMA200", "B2_original"):
        xs = roll["runs"].get(n, [])
        if xs:
            g.add_trace(go.Box(y=[x["final_usdt"] for x in xs], name=label(n), boxpoints="all", jitter=0.4, pointpos=0, marker=dict(size=4, color=COLORS.get(n, "#8b98b5")), line=dict(width=1)))
    g.update_layout(title=f"滚动 {roll['years']} 年持有期的最终净资产（{roll['n_starts']} 个季度起点，2014 起）<br><sup>每个点是一个起点；B2 只有 2017 年以后的起点；对数坐标</sup>", yaxis_type="log", showlegend=False, height=440); figs.append(g)
    plots = "".join(f'<div class="plot">{_fig(x, i == 0)}</div>' for i, x in enumerate(figs))

    # 结论卡片
    bv = V[best]; b_full = R[f"{best}|full_2014|15"]; bh = R["BH_BTC|full_2014|15"]; b50 = R["ALLOC_50_Y|full_2014|15"]; b2 = R["B2_original|since_2017|15"]
    roll_n = lambda n: roll["runs"].get(n, []); over = lambda n: float(np.mean([x["max_drawdown"] < -0.50 for x in roll_n(n)])) if roll_n(n) else None
    inb = [n for n, v in V.items() if v["within_budget"]]; any_beats = [n for n, v in V.items() if v["within_budget"] and v["beats_bh_by_final_usdt"]]
    cards = "".join([
        dashboard19._card("50% 回撤预算内历史风险收益最好的固定配置", label(best), f'全样本 {_f(b_full["final_usdt"])} USDT · 最大回撤 {_pct(b_full["max_drawdown"])} · 年化 {_pct(b_full["cagr"])} · 收益回撤比 {b_full["return_over_mdd"]:.2f}', TAG["hist"]),
        dashboard19._card("是否有证据优于 BTC 买入持有", "没有" if not any_beats else "有：" + "、".join(label(x) for x in any_beats), f'以最终 USDT 计：{label(best)} 在 {sum(bv["windows_beating_bh"])}/3 个窗口、{_pct(bv["rolling_share_final_ge_bh"], 0)} 的滚动起点不低于买入持有', TAG["hist"]),
        dashboard19._card("BTC 买入持有（对照）", _f(bh["final_usdt"]) + " USDT", f'最大回撤 {_pct(bh["max_drawdown"])} · 收益回撤比 {bh["return_over_mdd"]:.2f} · {_pct(over("BH_BTC"), 0)} 的 4 年起点回撤超过 50%', TAG["hist"]),
        dashboard19._card("50% BTC · 年度再平衡（预算外）", _f(b50["final_usdt"]) + " USDT", f'最大回撤 {_pct(b50["max_drawdown"])} · 收益回撤比 {b50["return_over_mdd"]:.2f} · {_pct(over("ALLOC_50_Y"), 0)} 的 4 年起点回撤超过 50%', TAG["hist"]),
        dashboard19._card("原 B2（2017 起，对照）", _f(b2["final_usdt"]) + " USDT", f'最大回撤 {_pct(b2["max_drawdown"])} · 收益回撤比 {b2["return_over_mdd"]:.2f}（只有 2017 年以后数据，不能与 2014 起的全样本直接比较）', TAG["hist"]),
        dashboard19._card("回撤预算内的方案", "、".join(label(x) for x in inb), "全样本、5 个压力区间和全部滚动起点的最大回撤都不超过 50%", TAG["hist"]),
        dashboard19._card("研究账户初始本金", f"{init:,.0f} USDT", "所有结果真实重算；不是 10,000 USDT 结果 ×10", TAG["res"]),
        dashboard19._card("Mac 三个模拟账户", f"{MAC_ACCOUNTS_INITIAL_USDT:,.0f} USDT / 账户", "A_BTC_SMA200、B_modelB_state_machine、REF_ETH_SMA200：本金、账本、事件链、持仓与服务均未改动", TAG["mac"])])

    # 配置表（全样本）
    def row(n, k, extra=""):
        m = R.get(k)
        if m is None:
            return ""
        w = m["warnings"]; wh = " ".join(("●" if w[f"{int(t * 100)}%"]["hit"] else "○") for t in WARN)
        hi = ' class="hi"' if n == best else ""
        return (f'<tr{hi}><td>{label(n)}{extra}</td><td>{_f(m["final_usdt"])}</td><td>{_f(m["net_profit_usdt"])}</td><td>{_pct(m["max_drawdown"])}</td><td>{m["longest_drawdown_days"]}</td><td>{_pct(m["cagr"])}</td><td>{_pct(m["ann_vol"], 0)}</td>'
                f'<td>{_r(m["return_over_mdd"])}</td><td>{wh}</td><td>{_pct(m["avg_btc_exposure"], 0)}</td><td>{m["n_fills"]}</td><td>{_f(m["fees_usdt"])}</td><td>{_f(m["base_slippage_usdt"])}</td><td>{_f(m["impact_usdt"])}</td><td>{_f(m["total_cost_usdt"])}</td></tr>')
    hdr = "<tr><th>方案</th><th>最终净资产</th><th>净利润</th><th>最大回撤</th><th>最长回撤（天）</th><th>年化</th><th>年化波动</th><th>收益回撤比</th><th>预警 35/40/45/50</th><th>平均 BTC 仓位</th><th>成交</th><th>手续费</th><th>基础滑点</th><th>流动性冲击</th><th>总成本</th></tr>"
    others = ["BH_BTC", "B1_BTC_SMA200", "A_X4", "ABC_X2", "A_X2"]
    t_full = "".join(row(n, f"{n}|full_2014|15") for n in allocs + others)
    t_17 = "".join(row(n, f"{n}|since_2017|15") for n in allocs + others + ["B2_original"])
    tbl = (f'<h3>全样本 2014-01-01 → {latest} · 单边 15 bps · 单位 USDT {TAG["hist"]}</h3><div class="tbl"><table>{hdr}{t_full}</table></div>'
           f'<div class="small">● = 曾触及该回撤线，○ = 未触及。总成本 = 手续费 + 滑点（基础滑点 + 流动性冲击）；期末假设清仓的冲击已计入最终净资产，未计入成本列。绿色行为预算内收益回撤比最高者。</div>'
           f'<h3>2017-01-01 → {latest}（可与原 B2 比较）· 单边 15 bps {TAG["hist"]}</h3><div class="tbl"><table>{hdr}{t_17}</table></div>')
    # 分段窗口
    wl = [("train_2014_2018", "训练 2014–2018"), ("valid_2019_2021", "验证 2019–2021"), ("recheck_2022_2026", f"重复检验 2022 → {latest}")]
    key = ["ALLOC_0_N", "ALLOC_25_Y", "ALLOC_25_D", "ALLOC_50_Y", "ALLOC_75_Y", "BH_BTC", "B1_BTC_SMA200", "B2_original"]
    wr = ""
    for n in key:
        cells = ""
        for wn, _ in wl:
            for cc in (15, 25):
                m = R.get(f"{n}|{wn}|{cc}"); cells += "<td>—</td>" if m is None else f'<td>{_f(m["final_usdt"])}<br><span class="dd">{_pct(m["max_drawdown"])}</span></td>'
        wr += f"<tr><td>{label(n)}</td>{cells}</tr>"
    wtbl = (f'<h3>分段窗口：最终净资产 / 最大回撤（每段都从 {init:,.0f} USDT 重新开始）{TAG["hist"]}</h3><div class="tbl"><table><tr><th rowspan="2">方案</th>' + "".join(f'<th colspan="2">{t}</th>' for _, t in wl)
            + "</tr><tr>" + "<th>15 bps</th><th>25 bps</th>" * 3 + f"</tr>{wr}</table></div>")
    # 滚动统计
    rr = ""
    for n in key:
        xs = roll_n(n)
        if xs:
            fv = np.array([x["final_usdt"] for x in xs]); md = np.array([x["max_drawdown"] for x in xs]); bhr = {x["start"]: x["final_usdt"] for x in roll_n("BH_BTC")}
            ge = np.mean([x["final_usdt"] >= bhr[x["start"]] for x in xs if x["start"] in bhr])
            rr += f'<tr><td>{label(n)}</td><td>{len(xs)}</td><td>{_f(np.median(fv))}</td><td>{_f(fv.min())}</td><td>{_f(fv.max())}</td><td>{_pct(float(md.min()))}</td><td>{_pct(float(np.mean(md < -0.5)), 0)}</td><td>{_pct(float(ge), 0)}</td></tr>'
    rtbl = f'<h3>滚动 4 年持有期统计 {TAG["hist"]}</h3><div class="tbl"><table><tr><th>方案</th><th>起点数</th><th>中位最终净资产</th><th>最低</th><th>最高</th><th>最差回撤</th><th>回撤超过 50% 的起点</th><th>不低于买入持有的起点</th></tr>{rr}</table></div>'
    # 压力区间
    st = r["stress"]; sn = {"S1_2014_bear": "2014 熊市", "S2_2018_bear": "2018 熊市", "S3_2020_covid": "2020 疫情急跌", "S4_2022_bear": "2022 熊市", "S5_2025_2026": "2025 高点 → 此后低点"}
    sr = "".join(f'<tr><td>{sn[s]}<br><span class="dd">{st[s]["window"][0]} → {st[s]["window"][1]}</span></td>' + "".join(f'<td>{_pct(st[s][n]["max_drawdown"]) if n in st[s] else "—"}</td>' for n in key) + "</tr>" for s in st)
    stbl = f'<h3>压力区间最大回撤（事后选取的已知最坏时段，只作观察）{TAG["hist"]}</h3><div class="tbl"><table><tr><th>区间</th>' + "".join(f"<th>{label(n)}</th>" for n in key) + f"</tr>{sr}</table></div>"
    # 真实重算 vs ×10
    xr = "".join(f'<tr><td>{label(x["name"])}</td><td>{_f(x["final_100k_real"])}</td><td>{_f(x["final_10k_x10"])}</td><td>{_f(x["diff_usdt"])}</td><td>{_pct(x["diff_pct"], 2)}</td><td>{_f(x["impact_100k"])}</td><td>{_f(x["impact_10k_x10"])}</td></tr>' for x in r["x10_check"])
    xtbl = (f'<h3>真实重算 vs 10,000 USDT 结果 ×10（全样本，15 bps）{TAG["hist"]}</h3><div class="tbl"><table><tr><th>方案</th><th>100,000 真实重算</th><th>10,000 × 10</th><th>差额</th><th>差异</th><th>交易冲击（100k）</th><th>交易冲击（10k × 10）</th></tr>{xr}</table></div>'
            '<div class="small">差异来自平方根冲击随订单规模增大（规模 ×10 → 每笔冲击约 ×3.2 倍 bps）、数量精度取整，以及期末约千万 USDT 清仓的冲击。交易频繁的 SMA200 差异最大。</div>')

    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>BTC/USDT 资金配置中心（研究账户 100,000 USDT）</title>
<style>:root{{--bg:#0f1420;--panel:#171e2e;--line:#2a354d;--text:#e6ebf5;--muted:#8b98b5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden;overflow-wrap:anywhere}}
main{{max-width:1280px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:6px 0}}h2{{font-size:16px;margin:20px 0 8px}}h3{{font-size:14px;color:var(--muted);margin:16px 0 6px}}
.note{{background:rgba(245,185,66,.12);border:1px solid rgba(245,185,66,.35);color:#f3d58a;padding:8px 10px;border-radius:8px;font-size:12.5px;margin:8px 0}}.acct{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0}}.acct>div{{border-radius:10px;padding:10px 12px;font-size:13px;min-width:0}}.acct .r{{background:rgba(39,194,129,.12);border:1px solid rgba(39,194,129,.4)}}.acct .m{{background:rgba(76,154,255,.10);border:1px solid rgba(76,154,255,.4)}}.acct b{{font-size:15px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.grid>*{{min-width:0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow:hidden}}.card .k{{color:var(--muted);font-size:12px}}.card .v{{font-size:17px;font-weight:600;margin-top:2px}}.card .s{{color:var(--muted);font-size:12px;margin-top:2px}}
.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;color:#fff;vertical-align:middle}}.tag.hist{{background:#6e40c9}}.tag.res{{background:#1f7a52}}.tag.mac{{background:#0969da}}
.plot{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;min-width:0;overflow:hidden}}.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}td:first-child,th:first-child{{text-align:left}}tr.hi td{{background:rgba(39,194,129,.12)}}.dd{{color:var(--muted);font-size:11.5px}}.small{{color:var(--muted);font-size:12px;margin:4px 0}}
.foot{{color:var(--muted);font-size:12px;margin-top:20px;border-top:1px solid var(--line);padding-top:8px}}@media (max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media (max-width:600px){{main{{padding:10px}}.card .v{{font-size:15px}}h1{{font-size:17px}}.acct{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>BTC/USDT 资金配置中心 {TAG["res"]}</h1>
<div class="acct"><div class="r"><b>研究账户 · {init:,.0f} USDT</b><br>本页全部数字：第二十阶段历史回测，按 {init:,.0f} USDT 真实重算（数量精度、最小订单、手续费、滑点、平方根流动性冲击）。不是交易账户，不下单。</div>
<div class="m"><b>Mac 运行账户 · {MAC_ACCOUNTS_INITIAL_USDT:,.0f} USDT × 3</b><br>A_BTC_SMA200 / B_modelB_state_machine / REF_ETH_SMA200 仍为 10,000 USDT，账本、事件链、持仓、SMA200 参数与运行服务均未改动；其数据只在第十四阶段看板显示。</div></div>
<div class="note">固定配置：0 / 25 / 50 / 75 / 100% BTC，其余为 USDT（不计利息）；维护方式：不再平衡 / 每年 1 月第一个交易日再平衡 / 偏离目标超过 10 个百分点再平衡。最大回撤研究预算 50%（距历史最高净资产的动态回撤；不是保证止损），35 / 40 / 45% 为预警线。2014–2026 全部为已看过的研究数据，非盲测。真实交易禁用。生成于 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC；数据截至 {latest}。</div>
<h2>结论</h2><div class="grid">{cards}</div>
<h2>净资产与回撤</h2>{plots}
<h2>配置明细</h2>{tbl}{wtbl}{rtbl}{stbl}{xtbl}
<div class="foot">预登记：{r["preregistration"]}；结果文件：reports_out/brando_pa/phase20_capital100k/phase20_results.json。执行模型：BTC 数量精度 0.00001、最小订单 5 USDT（引擎实际拒绝 &lt; 10 USDT）、冲击 = σ（前 20 日）× √（订单金额 ÷ 当日成交额），上限 100 bps，2015-07-19 之前无成交量按 25 bps。数据：BTC 2014-01 → 2015-07-19 只有收盘价、2015-07-20 起 Coinbase、2017-08-17 起第十阶段数据集；B2 的 ETH 2016-05 起。</div>
</main></body></html>"""
    out.mkdir(parents=True, exist_ok=True); p = out / "dashboard20_allocation_center_100k.html"; p.write_text(page, encoding="utf-8"); return p


def build_phase19_100k(online: bool = False, ctx=None) -> Path:
    """第十九阶段“Brando长期投资决策中心”按 100,000 USDT 研究本金重建（结果读第二十阶段目录中的重算版本）。"""
    ctx = ctx or P.Ctx(); rd = P.OUT_DIR / "phase19_100k"
    hook = lambda bt, kind: attach(bt, ctx.liq_btc if kind == "btc" else ctx.liq_b2)
    return dashboard19.build(online=online, out=rd, res_dir=rd, initial=RESEARCH_INITIAL_USDT, hook=hook)


if __name__ == "__main__":
    import sys
    c = P.Ctx(); print(build(ctx=c)); print(build_phase19_100k(online="--online" in sys.argv, ctx=c))
