"""“Brando长期投资决策中心”（第十九阶段独立研究页面；静态 HTML，离线 plotly.js；不修改第十四阶段看板）。
四类标签：研究性观察 / 历史回测 / 独立前瞻观察 / 已验证的模拟策略信号（本阶段：无）。"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..config import cfg
from ..holding.engine import HoldingBacktester
from ..pa18 import phase18
from . import states19 as S
from .b2rec19 import V7Backtester
from .current19 import readings
from .engine19 import LongTermBacktester
from .phase19 import OUT_DIR

TAG = {"obs": '<span class="tag obs">研究性观察</span>', "hist": '<span class="tag hist">历史回测</span>', "fwd": '<span class="tag fwd">独立前瞻观察</span>', "sig": '<span class="tag sig">已验证的模拟策略信号：无</span>'}
LAYOUT = dict(paper_bgcolor="#171e2e", plot_bgcolor="#171e2e", font=dict(color="#e6ebf5", size=12), title_font=dict(size=14), margin=dict(l=55, r=15, t=64, b=40), legend=dict(orientation="h", y=-0.2), hovermode="x unified",
              xaxis=dict(gridcolor="#2a354d"), yaxis=dict(gridcolor="#2a354d"))


def _f(v, d=0):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{d}f}"


def _card(k, v, s="", tag=""):
    return f'<div class="card"><div class="k">{k} {tag}</div><div class="v">{v}</div><div class="s">{s}</div></div>'


def _fig(fig, first):
    fig.update_layout(**LAYOUT); return fig.to_html(full_html=False, include_plotlyjs=("inline" if first else False), config={"responsive": True, "displaylogo": False, "displayModeBar": False})


def build(online: bool = True, out: Path | None = None) -> Path:
    out = Path(out or OUT_DIR); res = json.loads((OUT_DIR / "phase19_results.json").read_text(encoding="utf-8")); curves = json.loads((OUT_DIR / "phase19_curves_15bps.json").read_text(encoding="utf-8"))
    v7 = json.loads((OUT_DIR / "b2_v7_results.json").read_text(encoding="utf-8")) if (OUT_DIR / "b2_v7_results.json").exists() else None
    R = readings(online=online); d = S.load_daily(); cand, f, w = S.candidates(d); c = cfg(); figs = []
    # 1 价格、40 周均线与历史候选
    g = go.Figure(); g.add_trace(go.Scatter(x=f["ts"], y=f["close"], name="BTC 日收盘", line=dict(color="#4c9aff", width=1.2)))
    g.add_trace(go.Scatter(x=w["week_end"], y=w["sma40"], name="40 周均线", line=dict(color="#f5b942", width=1.4)))
    for t, col, sym in (("A", "#27c281", "triangle-up"), ("B", "#9b7bff", "diamond"), ("C", "#f0556a", "star")):
        x = cand[cand["type"] == t]
        g.add_trace(go.Scatter(x=x["ts"], y=x["entry_ref"], mode="markers", name=f"历史候选 {t}", marker=dict(color=col, symbol=sym, size=8)))
    g.update_layout(title="BTC、40 周均线与历史候选买点<br><sup>历史回测；候选 ≠ 买入信号；A 趋势回调 / B 突破回踩 / C 熊市后趋势恢复</sup>", yaxis_type="log", height=440); figs.append(g)
    # 2 全样本净值（2014 →）
    g = go.Figure()
    for n in ("BH_BTC", "B1_BTC_SMA200", "A_X4", "ABC_X2", "A_X2"):
        if n in curves:
            g.add_trace(go.Scatter(x=curves[n]["ts"], y=curves[n]["liquidation"], name=n, line=dict(width=1.3)))
    g.update_layout(title="净值对比 2014 → 最新<br><sup>历史回测；清仓口径；10,000 USDT 起；单边 15 bps</sup>", yaxis_type="log", height=420); figs.append(g)
    # 3 2017 → 含 B2 与 V7
    bars = phase18.load_bars(); bb = {k: v for k, v in bars.items() if not k.startswith("_")}; a0 = "2017-01-01"; rp = c.get("trendsys", {}).get("risk", {}); g = go.Figure()
    series = {"BTC 买入持有": HoldingBacktester({"BTCUSDT": bb["BTCUSDT"]}, {"name": "BH", "assets": ["BTCUSDT"], "mode": "buyhold"}, 10.0, 5.0, a0, None).run(),
              "BTC SMA200": HoldingBacktester({"BTCUSDT": bb["BTCUSDT"]}, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, a0, None, sma=200).run(),
              "原 B2": phase18.run_variant(bars, "V0_original", 15, a0, None, c)[1],
              "B2 + V7（研究变体）": V7Backtester(bb, {"name": "V7", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, 10.0, 5.0, a0, None, risk_params=rp).run(),
              "第十九阶段 A_X4": LongTermBacktester(f, cand, "A", "X4", 10.0, 5.0, a0, None).run()}
    for n, r in series.items():
        g.add_trace(go.Scatter(x=r.nav["ts"], y=r.nav["liquidation"], name=n, line=dict(width=1.3)))
    g.update_layout(title="净值对比 2017 → 最新（含原 B2）<br><sup>历史回测；B2 含 ETH；其余只做 BTC；单边 15 bps</sup>", yaxis_type="log", height=420); figs.append(g)
    plots = "".join(f'<div class="plot">{_fig(x, i == 0)}</div>' for i, x in enumerate(figs))
    # 卡片
    wk = R["weekly"]; dl = R["daily"]; un = R["unmet_conditions"]; live = R["live"]
    ev = res["event_study"]; med_days = ev["ALL"]["until_trend_failure"]["median_days"]
    def unmet_html(k):
        xs = un.get(k, []); return "全部满足（仍需风险收益比 ≥ 1.5 才会被策略采用）" if not xs else "<br>".join("· " + html.escape(x) for x in xs)
    cards = "".join([
        _card("BTC 现价", _f(R["price"], 1) + " USDT", (f'OKX 实时 {live["ts_utc"][:16]} UTC' if live.get("price") else "最新已收盘日线 " + R["data_as_of"])),
        _card("长期趋势状态（周线）", R["weekly_state_cn"], f'截至 {R["weekly_as_of"]} 周收盘 {_f(wk["close"])} · 40 周均线 {_f(wk["sma40"])} · 8 周斜率 {wk["slope8"]:+.1%} · 趋势失效 {"是" if wk["trend_failure"] else "否"}', TAG["obs"]),
        _card("重要阻力（已确认周摆动高点）", " / ".join(_f(x) for x in R["resistance_above"]) or "上方无已确认阻力", "价格上方最近两个", TAG["obs"]),
        _card("重要支撑（已确认周摆动低点）", " / ".join(_f(x) for x in R["support_below"]) or "—", f'日线 200 日均线 {_f(dl["sma200"])}（{"上行" if dl["sma200_rising"] else "未上行"}）· ATR14 {_f(dl["atr14"])}', TAG["obs"]),
        _card("候选买入区域", "当前没有候选" if not R["candidates_today"] else " / ".join(f'{c_["type"]}: {_f(c_["zone_low"])}–{_f(c_["zone_high"])}' for c_ in R["candidates_today"]), "近 30 日候选：" + (", ".join(f'{c_["type"]} {c_["ts"][:10]}' for c_ in R["candidates_last_30d"]) or "无"), TAG["obs"]),
        _card("是否已经确认入场", "否 · WAIT", "没有任何策略通过第十九阶段判定；候选区域不是买入建议", TAG["sig"]),
        _card("A 趋势回调：未满足条件", "", unmet_html("A_趋势回调"), TAG["obs"]),
        _card("B 突破回踩：未满足条件", "", unmet_html("B_突破回踩"), TAG["obs"]),
        _card("C 熊市后趋势恢复：未满足条件", "", unmet_html("C_熊市后趋势恢复"), TAG["obs"]),
        _card("结构失效价格（若今日按 A 规则入场）", _f(dl["sma200"] - 1.25 * dl["atr14"]), "200 日均线 − 1.25 ATR；仅示意计划口径，当前无候选", TAG["obs"]),
        _card("候选分批卖出区", " / ".join(_f(x) for x in R["resistance_above"]) or "+2R / +4R", "入场上方最近两个已确认周摆动高点；无则 +2R / +4R", TAG["obs"]),
        _card("计划持有周期", f'中位数约 {_f(med_days)} 天' if med_days else "—", "历史候选持有至周线趋势失效的中位天数（样本有重叠）", TAG["hist"]),
        _card("交易成本", "单边 15 bps（手续费 10 + 滑点 5）", "往返 30 bps；风险收益比按扣费后计算；敏感性 0 / 25 / 50 bps", ""),
        _card("订单流", "无", S.ORDERFLOW_NOTE, "")])
    # 事件研究表
    rows = ""
    for t, nm in (("A", "A 趋势回调"), ("B", "B 突破回踩"), ("C", "C 熊市后趋势恢复")):
        st = ev[t]
        for h in ("30d", "90d", "180d", "365d"):
            x = st[h]; lo, hi = x["wilson95"]; win = x["win_independent"]; med = x["median_ret_independent"]
            win_s = "—" if win is None else f"{win:.0%}"; ci_s = "—" if lo is None else f"{lo:.0%} – {hi:.0%}"; med_s = "—" if med is None else f"{med:+.1%}"
            rows += f'<tr><td>{nm}</td><td>{h}</td><td>{x["n_raw"]}</td><td>{x["n_independent"]}</td><td>{win_s}</td><td>{ci_s}</td><td>{med_s}</td><td>{x["evidence"]}</td></tr>'
    ev_tbl = f'<h3>历史候选买点扣费后盈利比例 {TAG["hist"]}</h3><div class="tbl"><table><tr><th>类型</th><th>持有</th><th>原始样本</th><th>独立样本</th><th>盈利比例（独立）</th><th>Wilson 95%</th><th>中位收益</th><th>证据</th></tr>{rows}</table></div>'
    Rr = res["results"]; names = ["BH_BTC", "B1_BTC_SMA200", "B2_original", "A_X4", "ABC_X2", "A_X2", "B_X4"]; tb = ""
    for wn, title in (("valid_2019_2021", "验证 2019–2021"), ("recheck_2022_2026", "重复检验 2022 → 最新"), ("full_2014", "全样本 2014 → 最新")):
        tr = "".join(f'<tr><td>{n}</td><td>{_f(Rr[k]["net_profit_usdt"])}</td><td>{Rr[k]["max_drawdown"]:.1%}</td><td>{Rr[k]["up_capture"] or 0:.2f}</td><td>{Rr[k]["n_round_trips"]}</td><td>{_f(Rr[k]["total_cost_usdt"])}</td></tr>' for n in names for k in [f"{n}|{wn}|15"] if k in Rr)
        tb += f'<h3>{title} · 单边 15 bps · 已看过的数据 {TAG["hist"]}</h3><div class="tbl"><table><tr><th>方案</th><th>净利润</th><th>最大回撤</th><th>上涨捕获</th><th>闭合交易</th><th>总成本</th></tr>{tr}</table></div>'
    v7txt = "" if not v7 else f'<div class="note">B2 冷静期提前结束 V7（研究变体）：按第十八阶段预登记规则<b>通过</b>（2017 与 2018–2021 净收益更高、回撤不劣；2022–2026 未触发、与原 B2 相同），但全样本最大回撤 {v7["full|15"]["V7"]["max_drawdown"]:.1%} 对原 B2 {v7["full|15"]["V0"]["max_drawdown"]:.1%}，且规则是在看过 2021 年案例之后设计的 → 只具备进入<b>独立影子观察</b>的资格，原 B2 不变。{TAG["hist"]}</div>'
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Brando长期投资决策中心</title>
<style>:root{{--bg:#0f1420;--panel:#171e2e;--line:#2a354d;--text:#e6ebf5;--muted:#8b98b5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden;overflow-wrap:anywhere}}
main{{max-width:1280px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:6px 0}}h2{{font-size:16px;margin:20px 0 8px}}h3{{font-size:14px;color:var(--muted);margin:16px 0 6px}}
.note{{background:rgba(245,185,66,.12);border:1px solid rgba(245,185,66,.35);color:#f3d58a;padding:8px 10px;border-radius:8px;font-size:12.5px;margin:8px 0}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.grid>*{{min-width:0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow:hidden}}.card .k{{color:var(--muted);font-size:12px}}.card .v{{font-size:17px;font-weight:600;margin-top:2px}}.card .s{{color:var(--muted);font-size:12px;margin-top:2px}}
.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;color:#fff;vertical-align:middle}}.tag.obs{{background:#2b7a4b}}.tag.hist{{background:#6e40c9}}.tag.fwd{{background:#0969da}}.tag.sig{{background:#cf222e}}
.plot{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;min-width:0;overflow:hidden}}.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}td:first-child,th:first-child{{text-align:left}}
.foot{{color:var(--muted);font-size:12px;margin-top:20px;border-top:1px solid var(--line);padding-top:8px}}@media (max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media (max-width:600px){{main{{padding:10px}}.card .v{{font-size:15px}}h1{{font-size:17px}}}}</style></head><body><main>
<h1>Brando长期投资决策中心 {TAG["obs"]}</h1>
<div class="note">第十九阶段独立研究页面（不是第十四阶段生产看板）。标签：{TAG["obs"]} 当前价格结构与候选区域；{TAG["hist"]} 已看过的历史数据上的结果；{TAG["fwd"]} 2026-11-10 起只追加的观察日志；{TAG["sig"]}。16 个候选长期方案全部未通过预登记判定，因此本页不提供任何买入建议；没有合适机会时显示 WAIT。生成于 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC；日线截至 {R["data_as_of"]}，周线截至 {R["weekly_as_of"]}。</div>
<h2>当前读数</h2><div class="grid">{cards}</div>{v7txt}
<h2>历史分析</h2>{plots}{ev_tbl}{tb}
<div class="foot">数据：BTC 日线 2014-01 → 2015-07-19 只有收盘价（需要 OHLCV 的规则此前不可验证）、2015-07-20 起 Coinbase、2017-08-17 起第十阶段数据集；B2 的 ETH 2016-05 起 Coinbase、2017-08-17 起第十阶段数据集。成交量为交易所报告量，{S.ORDERFLOW_NOTE}。2014–2026 全部为已看过的研究数据，非盲测。独立前瞻观察日志：data_store/brando_pa/phase19_longterm/forward_observations.jsonl。真实交易禁用。</div>
</main></body></html>"""
    p = out / "dashboard19_longterm_decision_center.html"; p.write_text(page, encoding="utf-8"); return p


if __name__ == "__main__":
    import sys
    print(build(online="--offline" not in sys.argv))
