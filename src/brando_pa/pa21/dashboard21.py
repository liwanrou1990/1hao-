"""“Brando 前瞻观察中心 · 25% BTC 固定观察基准”（第二十一阶段独立只读研究页面；静态 HTML，离线 plotly.js）。
只读取第二十一阶段输出目录；不读取也不改动 Mac 三个账户；不提供任何交易按钮。"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from ..pa19.dashboard19 import LAYOUT, _f, _fig
from . import ACCOUNTS, FORWARD_START, LABELS, MAC_ACCOUNTS_INITIAL_USDT, OUT_DIR, RESEARCH_INITIAL_USDT, WARN
from . import freeze21

COL = {"FIX25_Y": "#27c281", "B2_original": "#f0556a", "SMA200": "#9b7bff", "BH_BTC": "#4c9aff"}
TAG = {"fwd": '<span class="tag fwd">前瞻观察</span>', "hist": '<span class="tag hist">历史回测</span>', "stress": '<span class="tag stress">压力情景</span>', "res": '<span class="tag res">研究本金 100,000 USDT · 虚拟</span>'}


def _p(v, d=1):
    return "—" if v is None else f"{v:.{d}%}"


def _read(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _card(k, v, s="", tag=""):
    return f'<div class="card"><div class="k">{k} {tag}</div><div class="v">{v}</div><div class="s">{s}</div></div>'


def _ddbar(dd):
    pct = min(100.0, -dd / 0.6 * 100) if dd is not None else 0; col = "#27c281" if dd > -0.35 else ("#f5b942" if dd > -0.45 else "#f0556a")
    marks = "".join(f'<i style="left:{w / 0.6 * 100:.1f}%" title="{int(w * 100)}%"></i>' for w in WARN)
    return f'<div class="ddbar"><b style="width:{pct:.1f}%;background:{col}"></b>{marks}</div>'


def build(out_dir: Path | None = None, out: Path | None = None) -> Path:
    d = Path(out_dir or OUT_DIR); out = Path(out or d); st = json.loads((d / "state.json").read_text(encoding="utf-8")) if (d / "state.json").exists() else {"status": "not_run"}
    rows = [r for r in _read(d / "daily_ledger.jsonl") if r.get("kind") == "daily"]; alerts = _read(d / "alerts.jsonl"); events = _read(d / "events.jsonl")
    S = json.loads((d / "stress21.json").read_text(encoding="utf-8")) if (d / "stress21.json").exists() else None; fz = freeze21.verify(); man = json.loads(freeze21.MANIFEST.read_text(encoding="utf-8")) if freeze21.MANIFEST.exists() else {}
    n = len(rows); last = rows[-1] if rows else None; status_cn = {"observing": "观察中", "awaiting_first_close": "等待观察起点日线收盘", "frozen_rule_violation": "冻结规则校验失败（已停止记录）", "paused_data_quality": "行情质量异常（暂停记录）", "no_data": "无行情数据", "not_run": "尚未运行"}.get(st.get("status"), st.get("status"))
    nxt = f"{int((last['date'] if last else FORWARD_START)[:4]) + 1}-01-01 开盘"
    chains = st.get("chains", {}); chain_ok = all(v.get("ok") for v in chains.values()) if chains else None
    cards = "".join([
        _card("观察状态", html.escape(str(status_cn)), st.get("note", "") or f'最新已收盘日 {st.get("latest_closed_day", "—")}', TAG["fwd"]),
        _card("观察起点", FORWARD_START, "该日收盘首次决策、次日开盘首次成交；此前数据只用于指标预热"),
        _card("已观察天数", f"{n} 天", "只计 2026-11-11 之后真正新增的已收盘日线，不含任何历史数据", TAG["fwd"]),
        _card("行情", html.escape(str(st.get("feed", {}).get("source") or (last or {}).get("data_source") or "—")), "独立行情仓，只用已收盘日线；来源写入每根 K 线"),
        _card("冻结规则校验", "通过" if fz.get("ok") else "失败", f'冻结于 {str(man.get("frozen_at_utc", "—"))[:16]} UTC · 参数指纹 {str(man.get("params_sha256", ""))[:12]}'),
        _card("记录完整性", "通过" if chain_ok else ("—" if chain_ok is None else "失败"), "每日记录、警报、事件三条哈希链"),
        _card("下一次再平衡（FIX25_Y）", nxt, "只在每年 1 月第一个交易日；其余时间不交易"),
        _card("自动交易", "无", "回撤警报只记录；真实交易禁用；不下单")])
    # 账户表
    hdr = "<tr><th>账户</th><th>BTC 数量</th><th>ETH 数量</th><th>USDT 余额</th><th>组合权益</th><th>清仓净值</th><th>BTC 仓位</th><th>历史高水位</th><th>当前回撤</th><th>最大回撤</th><th>累计手续费</th><th>累计滑点与冲击</th><th>成交</th><th>起点以来</th><th>相对 BH</th><th>相对 B2</th></tr>"
    if last:
        tr = ""
        for a in ACCOUNTS:
            x = last["accounts"][a]; rel = last["relative_return"][a]
            tr += (f'<tr><td>{LABELS[a]}</td><td>{x["btc_qty"]:.5f}</td><td>{"—" if x["eth_qty"] is None else f"{x['eth_qty']:.4f}"}</td><td>{_f(x["usdt"])}</td><td>{_f(x["equity"])}</td><td>{_f(x["liquidation"])}</td><td>{_p(x["btc_weight"], 0)}</td><td>{_f(x["hwm"])}</td>'
                   f'<td>{_p(x["drawdown"])}</td><td>{_p(x["max_drawdown"])}</td><td>{_f(x["fees_cum"], 2)}</td><td>{_f(x["slippage_impact_cum"], 2)}</td><td>{x["n_fills_cum"]}</td><td>{_p(x["return"], 2)}</td><td>{"—" if a == "BH_BTC" else _p(rel["vs_BH_BTC"], 2)}</td><td>{"—" if a == "B2_original" else _p(rel["vs_B2_original"], 2)}</td></tr>')
        acct = f'<h3>截至 {last["date"]} 收盘 · BTC {_f(last["btc_close"], 1)} USDT {TAG["fwd"]}</h3><div class="tbl"><table>{hdr}{tr}</table></div>'
    else:
        tr = "".join(f'<tr><td>{LABELS[a]}</td><td>0</td><td>{"0" if a == "B2_original" else "—"}</td><td>{_f(RESEARCH_INITIAL_USDT)}</td><td>{_f(RESEARCH_INITIAL_USDT)}</td><td>{_f(RESEARCH_INITIAL_USDT)}</td><td>0%</td><td>{_f(RESEARCH_INITIAL_USDT)}</td><td>0.0%</td><td>0.0%</td><td>0</td><td>0</td><td>0</td><td>0.00%</td><td>—</td><td>—</td></tr>' for a in ACCOUNTS)
        acct = f'<h3>尚无已收盘的观察日：四个账户均为初始 100,000 USDT（虚拟）{TAG["fwd"]}</h3><div class="tbl"><table>{hdr}{tr}</table></div>'
    # 回撤监测
    mon = ""
    for a in ACCOUNTS:
        x = last["accounts"][a] if last else {"drawdown": 0.0, "max_drawdown": 0.0, "warn_level": 0}
        lv = x["warn_level"]; lvs = "未触及预警" if lv == 0 else (f"已跌破 {lv}% 预警" if lv < 50 else "已超过 50% 研究预算")
        mon += f'<div class="mon"><div class="mk">{LABELS[a]}</div>{_ddbar(x["drawdown"])}<div class="ms">当前回撤 {_p(x["drawdown"])} · 最大回撤 {_p(x["max_drawdown"])} · {lvs}</div></div>'
    al = "".join(f'<tr><td>{e["date"]}</td><td>{LABELS[e["account"]]}</td><td>{int(e["threshold"] * 100)}%</td><td>{_p(e["drawdown"])}</td><td>{"超过研究预算" if e["kind"] == "drawdown_budget_breach" else "预警"}</td><td>只记录，不交易</td></tr>' for e in alerts[-30:])
    al_html = f'<div class="tbl"><table><tr><th>日期</th><th>账户</th><th>线</th><th>当日回撤</th><th>类型</th><th>动作</th></tr>{al}</table></div>' if al else '<div class="small">前瞻期内尚无研究性风险警报。</div>'
    ev_html = "".join(f'<li>{html.escape(str(e.get("recorded_at", ""))[:19])} · {html.escape(e["kind"])}</li>' for e in events[-10:])
    # 图
    figs = []
    if n >= 2:
        g = go.Figure(); g2 = go.Figure()
        for a in ACCOUNTS:
            xs = [r["date"] for r in rows]; g.add_trace(go.Scatter(x=xs, y=[r["accounts"][a]["liquidation"] for r in rows], name=LABELS[a], line=dict(color=COL[a], width=1.5)))
            g2.add_trace(go.Scatter(x=xs, y=[r["accounts"][a]["drawdown"] * 100 for r in rows], name=LABELS[a], line=dict(color=COL[a], width=1.3)))
        for w in WARN:
            g2.add_hline(y=-w * 100, line=dict(color="#f0556a" if w == 0.5 else "#f5b942", dash="dot", width=1))
        g.update_layout(title="前瞻清仓净值（USDT，虚拟研究本金）<br><sup>前瞻观察；2026-11-11 起；统一行情、成本与清仓口径</sup>", height=380); g2.update_layout(title="前瞻动态回撤与 35/40/45/50% 线<br><sup>警报只记录，不交易</sup>", yaxis_title="%", height=340); figs = [g, g2]
    plots = "".join(f'<div class="plot">{_fig(x, i == 0)}</div>' for i, x in enumerate(figs)) or '<div class="note">前瞻观察尚未产生两个以上已收盘日，暂不绘制曲线。不会用历史数据填充前瞻图表。</div>'
    # 每日记录
    dl = "".join(f'<tr><td>{r["date"]}{" · 补记" if r.get("catch_up") else ""}</td><td>{_f(r["btc_close"], 1)}</td>' + "".join(f'<td>{_f(r["accounts"][a]["liquidation"])}<br><span class="dd">{_p(r["accounts"][a]["drawdown"])}</span></td>' for a in ACCOUNTS) + f'<td>{html.escape(str(r.get("data_source")))}</td></tr>' for r in rows[::-1][:30])
    dl_html = f'<div class="tbl"><table><tr><th>日期</th><th>BTC 收盘</th>' + "".join(f"<th>{LABELS[a]}<br>清仓净值 / 回撤</th>" for a in ACCOUNTS) + f"<th>来源</th></tr>{dl}</table></div>" if dl else '<div class="small">尚无记录。</div>'
    # 压力情景
    sh = ""
    if S:
        ex = "".join(f"<li>{html.escape(x)}</li>" for x in S["exceeds_budget_scenarios"])
        inst = S["instant"]["loss_grid"]; names = list(inst); cols = ["BTC−30%|USDT−0%", "BTC−50%|USDT−0%", "BTC−80%|USDT−0%", "BTC−90%|USDT−0%", "BTC−0%|USDT−10%", "BTC−0%|USDT−50%", "BTC−50%|USDT−10%", "BTC−80%|USDT−30%"]
        it = ""
        for nm in names:
            cells = ""
            for k in cols:
                v = inst[nm].get(k)
                cells += f'<td class="{"bad" if v is not None and v < -0.5 else ""}">{_p(v)}</td>'
            it += f"<tr><td>{html.escape(nm)}</td>{cells}</tr>"
        inst_html = f'<h3>即时冲击（美元计价，没有机会交易）{TAG["stress"]}</h3><div class="tbl"><table><tr><th>状态</th>' + "".join(f"<th>{k.replace('|', ' + ')}</th>" for k in cols) + f"</tr>{it}</table></div>"
        rc = S["rally_then_crash"]; ks = sorted({r["rally_x"] for r in rc}); ds = sorted({r["crash"] for r in rc})
        rct = "".join(f'<tr><td>年内上涨 {k} 倍</td>' + "".join((lambda r: f'<td class="{"bad" if r["exceeds_budget"] else ""}">{_p(r["max_drawdown"])}</td>')(next(r for r in rc if r["rally_x"] == k and r["crash"] == d)) for d in ds) + "</tr>" for k in ks)
        rc_html = f'<h3>年内暴涨后、1 月再平衡之前暴跌：FIX25_Y 最大回撤 {TAG["stress"]}</h3><div class="tbl"><table><tr><th>情景</th>' + "".join(f"<th>随后下跌 {d:.0%}</th>" for d in ds) + f"</tr>{rct}</table></div>"
        cy = S["consecutive_down_years"]; ns = sorted({r["years"] for r in cy}); yd = sorted({r["yearly_drop"] for r in cy})
        cyt = "".join(f'<tr><td>每年下跌 {d:.0%}</td>' + "".join((lambda r: f'<td class="{"bad" if r["exceeds_budget"] else ""}">{_p(r["max_drawdown"])}</td>')(next(r for r in cy if r["yearly_drop"] == d and r["years"] == y)) for y in ns) + "</tr>" for d in yd)
        cy_html = f'<h3>连续多年下跌（每年 1 月再平衡加仓）：FIX25_Y 最大回撤 {TAG["stress"]}</h3><div class="tbl"><table><tr><th>情景</th>' + "".join(f"<th>连续 {y} 年</th>" for y in ns) + f"</tr>{cyt}</table></div>"
        ah = "".join(f'<tr><td>{r["window"]}</td><td>{r["amplify"]}×</td><td>{_p(r["btc_mdd"])}</td><td class="{"bad" if r["exceeds_budget"] else ""}">{_p(r["max_drawdown"])}</td><td>{_p(r["max_btc_weight"], 0)}</td></tr>' for r in S["amplified_history"])
        ah_html = f'<h3>历史区间日收益放大：FIX25_Y {TAG["stress"]}</h3><div class="tbl"><table><tr><th>区间</th><th>放大</th><th>BTC 最大回撤</th><th>FIX25_Y 最大回撤</th><th>最高 BTC 仓位</th></tr>{ah}</table></div>'
        xe = "".join(f'<tr><td>{html.escape(r["scenario"])}</td><td>{_f(r["final_usdt"])}</td><td class="{"bad" if r["exceeds_budget"] else ""}">{_p(r["max_drawdown"])}</td><td>{r["n_fills"]}</td></tr>' for r in S["execution"])
        xe_html = f'<h3>执行情景（2014–2026 全样本，FIX25_Y 规则）{TAG["stress"]}</h3><div class="tbl"><table><tr><th>情景</th><th>最终净资产</th><th>最大回撤</th><th>成交</th></tr>{xe}</table></div>'
        ce = "".join(f'<tr><td>{LABELS.get(r["name"], r["name"]) if r["name"] != "ALLOC_25_Y" else LABELS["FIX25_Y"]}</td><td>{r["start"][:4]} 起</td><td>{html.escape(r["scenario"])}</td><td>{_f(r["final_usdt"])}</td><td>{_p(r["max_drawdown"])}</td><td>{r["n_fills"]}</td></tr>' for r in S["comparators_execution"]).replace("B1_BTC_SMA200", LABELS["SMA200"])
        ce_html = f'<h3>对照在高成本、低流动性下（历史数据）{TAG["stress"]}</h3><div class="tbl"><table><tr><th>方案</th><th>起点</th><th>情景</th><th>最终净资产</th><th>最大回撤</th><th>成交</th></tr>{ce}</table></div>'
        cb = "".join(f'<tr><td>{r["window"]}</td><td>{r["amplify"]}×</td><td>{r["usdt_depeg_at_trough"]:.0%}</td><td class="{"bad" if r["exceeds_budget"] else ""}">{_p(r["max_drawdown"])}</td><td>{r["trough_date"]}</td></tr>' for r in S["combined"])
        cb_html = f'<h3>组合情景：历史最坏区间 + 单边 200 bps + 成交量 1/100 + 低点 USDT 脱锚（FIX25_Y）{TAG["stress"]}</h3><div class="tbl"><table><tr><th>区间</th><th>放大</th><th>USDT 脱锚</th><th>最大回撤</th><th>低点</th></tr>{cb}</table></div>'
        hw = S["fix25_hist_max_btc_weight"]
        sh = (f'<div class="note">可能超过 50% 预算的情景（红色）：<ul>{ex}</ul>历史上 FIX25_Y 的 BTC 仓位在两次再平衡之间最高漂移到 <b>{hw["weight"]:.0%}</b>（{hw["date"]}），这是年度再平衡的主要尾部风险：年内大涨后、1 月再平衡之前的暴跌。</div>'
              + inst_html + rc_html + cy_html + ah_html + xe_html + ce_html + cb_html)
    # 待验证指标
    t0 = pd.Timestamp(FORWARD_START); sched = " / ".join(f"{k} 天（{(t0 + pd.Timedelta(days=k)).date()}）" for k in (30, 90, 180, 365))
    crit = [("最大回撤 ≤ 50%", "FIX25_Y 清仓净值相对前瞻期最高值"), ("BTC 自高点回撤 ≥ 30% 的区间，FIX25_Y 回撤 ≤ BTC 回撤 × 0.5", "风险控制是本基准的主要假设"), ("每年交易成本 ≤ 期初净值 0.2%", "手续费 + 滑点 + 冲击"),
            ("BTC 上涨区间，FIX25_Y 收益 ≥ BTC 收益 × 0.15", "理论约 0.25，扣再平衡与成本"), ("与原 B2、BH 的清仓净值、收益差、最大回撤、收益回撤比、上涨参与、时间在市", "至少一个完整牛熊周期（约 4 年）之前不下“更好 / 更差”的结论"),
            ("哈希链 100% 可验证、无冻结规则违规、数据缺口与来源切换全部有记录", "完整性")]
    ct = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in crit)
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Brando 前瞻观察中心 · 25% BTC 固定观察基准</title>
<style>:root{{--bg:#0f1420;--panel:#171e2e;--line:#2a354d;--text:#e6ebf5;--muted:#8b98b5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden;overflow-wrap:anywhere}}
main{{max-width:1280px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:6px 0}}h2{{font-size:16px;margin:20px 0 8px}}h3{{font-size:14px;color:var(--muted);margin:16px 0 6px}}ul{{margin:4px 0;padding-left:20px}}
.note{{background:rgba(245,185,66,.12);border:1px solid rgba(245,185,66,.35);color:#f3d58a;padding:8px 10px;border-radius:8px;font-size:12.5px;margin:8px 0}}.acct{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0}}.acct>div{{border-radius:10px;padding:10px 12px;font-size:13px;min-width:0}}.acct .r{{background:rgba(39,194,129,.12);border:1px solid rgba(39,194,129,.4)}}.acct .m{{background:rgba(76,154,255,.10);border:1px solid rgba(76,154,255,.4)}}.acct b{{font-size:15px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.grid>*{{min-width:0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow:hidden}}.card .k{{color:var(--muted);font-size:12px}}.card .v{{font-size:17px;font-weight:600;margin-top:2px}}.card .s{{color:var(--muted);font-size:12px;margin-top:2px}}
.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;color:#fff;vertical-align:middle}}.tag.fwd{{background:#0969da}}.tag.hist{{background:#6e40c9}}.tag.stress{{background:#b35900}}.tag.res{{background:#1f7a52}}
.mons{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.mon{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;min-width:0}}.mk{{font-size:13px;margin-bottom:6px}}.ms{{color:var(--muted);font-size:12px;margin-top:6px}}
.ddbar{{position:relative;height:12px;background:#222b3f;border-radius:6px;overflow:hidden}}.ddbar b{{position:absolute;left:0;top:0;bottom:0;border-radius:6px}}.ddbar i{{position:absolute;top:0;bottom:0;width:2px;background:#f0556a;opacity:.8}}
.plot{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;min-width:0;overflow:hidden}}.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}td:first-child,th:first-child{{text-align:left}}td.bad{{color:#ff8a9a;font-weight:600}}.dd{{color:var(--muted);font-size:11.5px}}.small{{color:var(--muted);font-size:12px;margin:4px 0}}
.foot{{color:var(--muted);font-size:12px;margin-top:20px;border-top:1px solid var(--line);padding-top:8px}}@media (max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media (max-width:600px){{main{{padding:10px}}.card .v{{font-size:15px}}h1{{font-size:17px}}.acct,.mons{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>Brando 前瞻观察中心 · 25% BTC 固定观察基准 {TAG["res"]}</h1>
<div class="acct"><div class="r"><b>研究本金 · {RESEARCH_INITIAL_USDT:,.0f} USDT（虚拟）</b><br>不是真实资产，也不是交易账户：四个观察账户按冻结规则在新增已收盘日线上记账，不下单、不自动买卖。</div>
<div class="m"><b>Mac 运行账户 · {MAC_ACCOUNTS_INITIAL_USDT:,.0f} USDT × 3（未改动）</b><br>A_BTC_SMA200 / B_modelB_state_machine / REF_ETH_SMA200 的本金、账本、事件链、持仓与服务均未改动；本页不读取其数据。</div></div>
<div class="note">固定观察基准 FIX25_Y：25% BTC + 75% USDT，每年 1 月第一个交易日再平衡，规则与参数已冻结（不再根据 2014–2026 历史结果优化）。对照：原 B2（日线研究引擎）、BTC SMA200、BTC 买入持有；统一起点 {FORWARD_START}、行情仓、成本（单边 15 bps + 平方根流动性冲击）与清仓口径。<b>只有 {FORWARD_START} 之后真正新增的行情才是前瞻观察</b>；2014–2026 仍是已看过的历史数据，不是盲测。生成于 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC。</div>
<h2>观察状态</h2><div class="grid">{cards}</div>
<h2>四个观察账户</h2>{acct}
<h2>50% 回撤预算监测</h2><div class="mons">{mon}</div><div class="small">刻度线：35 / 40 / 45 / 50%。回撤 = 清仓净值相对前瞻期最高清仓净值。警报只记录，不交易；50% 是研究预算，不是止损。</div><h3>研究性风险警报</h3>{al_html}
<h2>前瞻净值与回撤</h2>{plots}
<h2>每日记录（最近 30 天）</h2>{dl_html}
<h2>压力测试</h2>{sh or '<div class="small">尚未生成。</div>'}
<h2>需要在前瞻期验证的指标</h2><div class="small">只评已到期窗口：{sched}，之后每年。</div><div class="tbl"><table><tr><th>指标</th><th>说明</th></tr>{ct}</table></div>
<h3>运行事件（最近 10 条）</h3><ul class="small">{ev_html or "<li>无</li>"}</ul>
<div class="foot">预登记：docs/brando_pa_validation/phase21_forward/preregistration.md；冻结清单：src/brando_pa/pa21/frozen_manifest.json；记录：reports_out/brando_pa/phase21_forward/（daily_ledger.jsonl / alerts.jsonl / events.jsonl，只追加哈希链）。手动单次更新：PYTHONPATH=src python3 -m brando_pa.pa21.forward21；本页：PYTHONPATH=src python3 -m brando_pa.pa21.dashboard21。没有常驻服务、没有自动部署；真实交易禁用。</div>
</main></body></html>"""
    out.mkdir(parents=True, exist_ok=True); p = out / "dashboard21_forward_center.html"; p.write_text(page, encoding="utf-8"); return p


if __name__ == "__main__":
    print(build())
