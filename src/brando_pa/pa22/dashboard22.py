"""“真实买盘监测”中文只读页面（第二十二阶段独立页面；不改动第十四阶段生产看板；不提供任何交易控件）。"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..config import own_data_dir
from ..pa19.dashboard19 import LAYOUT, _f, _fig
from . import CB_PRODUCT, DATA_SUBDIR, OUT_DIR, SYMS
from .validate22 import HORIZONS

TAG = {"trade": '<span class="tag tr">真实成交 · 逐笔主动方向</span>', "book": '<span class="tag bk">挂单 · 可撤销，不计为买盘</span>', "hist": '<span class="tag hi">历史验证</span>', "fwd": '<span class="tag fw">前瞻</span>'}
LV = {"买盘确认": "up", "观察中": "warn", "证据不足": ""}


def _p(v, d=1):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{d}%}"


def _m(v):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v / 1e9:,.2f} B" if abs(v) >= 1e9 else f"{v / 1e6:,.1f} M")


def _ck(b):
    return "✓" if b else "✗"


def _card(k, v, s="", tag="", cls=""):
    return f'<div class="card"><div class="k">{k} {tag}</div><div class="v {cls}">{v}</div><div class="s">{s}</div></div>'


def _sum(d: pd.DataFrame) -> dict:
    b, s_ = d["buy_usdt"].sum(), d["sell_usdt"].sum(); return {"buy": b, "sell": s_, "net": b - s_, "imb": (b - s_) / (b + s_) if b + s_ else None, "lb": d["large_buy_usdt"].sum(), "ls": d["large_sell_usdt"].sum()}


def build(out_dir: Path | None = None, data_dir: Path | None = None) -> Path:
    od = Path(out_dir or OUT_DIR); dd = Path(data_dir or own_data_dir() / DATA_SUBDIR); R = json.loads((od / "phase22_results.json").read_text(encoding="utf-8"))
    cst = json.loads((od / "collector_status.json").read_text(encoding="utf-8")) if (od / "collector_status.json").exists() else {}; figs = []; sections = []
    # 1 当前证据等级
    cards = ""
    for s in SYMS:
        c = R["current"][s]; lvl = c.get("forward_level", "证据不足")
        chk = f'位置 L {_ck(c.get("L"))} · 近 6 天卖压吸收 A {_ck(c.get("recentA"))} · 主动买入增强 B {_ck(c.get("B"))} · 放量企稳 V {_ck(c.get("V"))} · 突破回踩 R {_ck(c.get("R"))} · 数据质量 Q {_ck(c.get("Q"))}'
        cards += _card(f'{s} 证据等级（截至 {str(c.get("ts"))[:10]} UTC 收盘）', lvl, chk + (f'<br>{html.escape(c["why"])}' if c.get("why") else ""), TAG["trade"], LV.get(lvl, ""))
        cards += _card(f"{s} 订单流读数", f'5 天净主动 {_p(c.get("net5_share"))}', f'3 天失衡 {_p(c.get("imb3"))}（前 10 天 {_p(c.get("imb_prior10"))}）· 当日成交额 / 20 天中位 {c.get("tot_vs_med20") or 0:.2f} 倍 · 收盘位置 {c.get("close_pos") or 0:.2f}<br>区域 {c.get("zone") or "无"} · Coinbase 3 天失衡 {_p(c.get("coinbase_imb3"))} · 跨交易所同号 {c.get("cross_exchange_agree")}', TAG["trade"])
    sections.append(f'<h2>当前证据等级</h2><div class="grid">{cards}</div><div class="small">只有真实订单流与价格结构同时满足预先固定的条件才显示“买盘确认”；实时（前瞻）还要求 OKX 与 Coinbase 方向一致。区域强度不参与判断。</div>')
    # 2 最新数据时间与质量
    rows = ""
    for k, v in (cst.get("streams") or {}).items():
        ex, sym = k.split(":"); lab = f"{ex} {CB_PRODUCT[sym] if ex == 'coinbase' else sym}"
        rows += f'<tr><td>{lab}</td><td>{str(v.get("last_trade_utc"))[:19]}</td><td>{v.get("trades_collected")}</td><td>{v.get("gaps_backfilled")}</td><td>{v.get("gaps_unresolved")}</td><td>{v.get("missing_ids")}</td><td>{(v.get("latency_p50_ms") or 0) / 1000:.1f} / {(v.get("latency_p95_ms") or 0) / 1000:.1f} 秒</td></tr>'
    rs = cst.get("resources", {})
    cont = "".join(f'<tr><td>OKX 归档 {s}</td><td>{R["daily_range"][s][0]} → {R["daily_range"][s][1]}</td><td>{v.get("files_ok")}</td><td>{len(v.get("files_missing", []))} / {len(v.get("files_error", []))}</td><td>{v.get("internal_missing_ids")}</td><td>{v.get("n_cross_file_breaks")}</td><td>{(v.get("rows") or 0):,}</td></tr>' for s, v in R["archive_continuity"].items())
    rub = " · ".join(f'{s}：{v.get("days")} 天，失衡度相关系数 {v.get("corr", float("nan")):.2f}，同号比例 {_p(v.get("same_sign_share"), 0)}' if "days" in v else f'{s}：{v.get("error")}' for s, v in R["rubik_crosscheck"].items())
    sections.append(f'<h2>最新数据时间与数据质量</h2><div class="tbl"><table><tr><th>实时成交流</th><th>最新成交（UTC）</th><th>已采集笔数</th><th>回补缺口</th><th>未补齐缺口</th><th>缺失编号</th><th>延迟 中位 / 95%</th></tr>{rows or "<tr><td colspan=7>实时采集尚未运行</td></tr>"}</table></div>'
                    f'<div class="small">采集器：开始 {str(cst.get("started_utc"))[:19]} UTC，更新 {str(cst.get("updated_utc"))[:19]} UTC；运行 {rs.get("elapsed_s")} 秒，CPU 占单核 {_p(rs.get("cpu_share_one_core"), 2)}，内存峰值 {rs.get("max_rss_mb")} MB，下载 {rs.get("download_mb")} MB（折合 {rs.get("download_mb_per_day")} MB/天），请求 {rs.get("requests")} 次，HTTP 错误 {rs.get("http_errors")} 次。延迟 = 收到时间 − 成交时间，含轮询间隔。</div>'
                    f'<div class="tbl"><table><tr><th>历史来源</th><th>日期范围（UTC）</th><th>文件</th><th>缺失 / 错误文件</th><th>文件内缺失编号</th><th>跨文件不衔接</th><th>成交笔数</th></tr>{cont}</table></div><div class="small">方向口径旁证（OKX 官方日主动买卖量统计，币种全部现货交易对汇总）：{rub}</div>')
    # 3 主动买卖额
    trs = ""
    for s in SYMS:
        d = pd.read_csv(od / f"daily_{s}.csv", parse_dates=["ts"]); full = d[d["hours"] == 24]
        for lab, x in (("最近 1 个完整日", full.tail(1)), ("最近 7 天", full.tail(7)), ("最近 30 天", full.tail(30)), ("最近 90 天", full.tail(90))):
            m = _sum(x); trs += f'<tr><td>{s}</td><td>{lab}</td><td>{_m(m["buy"])}</td><td>{_m(m["sell"])}</td><td class="{"up" if m["net"] > 0 else "dn"}">{_m(m["net"])}</td><td>{_p(m["imb"])}</td><td>{_m(m["lb"])} / {_m(m["ls"])}</td></tr>'
        x = d.tail(365); g = make_subplots(specs=[[{"secondary_y": True}]])
        g.add_trace(go.Bar(x=x["ts"], y=x["net"] / 1e6, name="净主动成交额（百万 USDT）", marker_color=np.where(x["net"] >= 0, "#27c281", "#f0556a")), secondary_y=False)
        g.add_trace(go.Scatter(x=x["ts"], y=(x["cvd"] - x["cvd"].iloc[0]) / 1e6, name="CVD（区间累计，百万 USDT）", line=dict(color="#4c9aff", width=1.6)), secondary_y=False)
        g.add_trace(go.Scatter(x=x["ts"], y=x["close"], name="收盘价", line=dict(color="#f5b942", width=1.2)), secondary_y=True)
        g.update_layout(title=f"{s} 主动买卖与 CVD（OKX 逐笔，最近 365 天）<br><sup>真实成交的主动方向；不是 K 线成交量</sup>", height=380); figs.append(g)
    sections.append(f'<h2>主动买入 / 卖出成交额 {TAG["trade"]}</h2><div class="tbl"><table><tr><th>标的</th><th>区间</th><th>主动买入</th><th>主动卖出</th><th>净额</th><th>失衡度</th><th>大额（≥ 10 万）买 / 卖</th></tr>{trs}</table></div>')
    # 4 实时分钟 CVD（OKX 与 Coinbase）
    g = go.Figure(); any_live = False
    for ex in ("okx", "coinbase"):
        for s in SYMS:
            p = dd / "live" / "raw" / ex / s
            files = sorted(p.rglob("*.parquet")) if p.exists() else []
            if not files:
                continue
            t = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).sort_values("tid"); t["m"] = pd.to_datetime(t["ts_ms"], unit="ms", utc=True).dt.floor("min"); t["q"] = t["price"] * t["size"] * np.where(t["taker"] == "buy", 1, -1)
            mm = t.groupby("m")["q"].sum().cumsum() / 1e6; any_live = True
            g.add_trace(go.Scatter(x=mm.index, y=mm.values, name=f'{ex} {CB_PRODUCT[s] if ex == "coinbase" else s}', line=dict(width=1.4)))
    if any_live:
        g.update_layout(title="实时 CVD（分钟累计，百万；OKX 为 USDT，Coinbase 为 USD）<br><sup>实时采集期间的真实成交</sup>", height=340); figs.append(g)
    # 5 盘口
    brs = ""; nps = ""
    for ex in ("okx", "coinbase"):
        for s in SYMS:
            p = dd / "live" / f"book_{ex}_{s}.jsonl"
            if not p.exists():
                continue
            B = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]; b = B[-1]; lab = f'{ex} {CB_PRODUCT[s] if ex == "coinbase" else s}'
            brs += f'<tr><td>{lab}</td><td>{str(pd.Timestamp(b["ts_ms"], unit="ms", tz="UTC"))[:19]}</td><td>{b["mid"]:,.2f}</td><td>{b["spread_bps"]:.2f}</td>' + "".join(f'<td>{_m(b[f"bid_{k}"])} / {_m(b[f"ask_{k}"])}{"" if b[f"covered_{k}"] else " *"}</td>' for k in (0.005, 0.01, 0.02)) + "</tr>"
            for x in B[-6:]:
                if "net_bid_placement_1pct" in x:
                    nps += f'<tr><td>{lab}</td><td>{str(pd.Timestamp(x["ts_ms"], unit="ms", tz="UTC"))[11:19]}</td><td>{x["interval_s"]:.0f}</td><td class="{"up" if x["net_bid_placement_1pct"] > 0 else "dn"}">{_m(x["net_bid_placement_1pct"])}</td><td>{_m(x["net_ask_placement_1pct"])}</td><td>{x["trades_attributed"]}</td></tr>'
    sections.append(f'<h2>盘口深度与净挂单变化 {TAG["book"]}</h2><div class="tbl"><table><tr><th>来源</th><th>快照（UTC）</th><th>中间价</th><th>价差 bps</th><th>±0.5% 买 / 卖</th><th>±1% 买 / 卖</th><th>±2% 买 / 卖</th></tr>{brs or "<tr><td colspan=7>尚无快照</td></tr>"}</table></div>'
                    f'<div class="small">* 该档超出快照覆盖范围（OKX 5000 档约覆盖 BTC ±1.4%、ETH ±4%），数值偏小。</div>'
                    f'<div class="tbl"><table><tr><th>来源</th><th>时间</th><th>间隔秒</th><th>买盘 ±1% 净挂单变化</th><th>卖盘 ±1% 净挂单变化</th><th>归因成交笔数</th></tr>{nps or "<tr><td colspan=6>需要至少两次快照</td></tr>"}</table></div>'
                    '<div class="small">净挂单变化 = 两次快照间该档深度变化 + 期间落在该档的主动成交额（即“新增 − 撤单”）；负值表示撤单多于新增。公开快照无法单独区分撤单与新增，挂单不计为买盘，也不参与“买盘确认”。</div>')
    # 6 吸收候选
    cand = ""
    for s in SYMS:
        st = pd.read_csv(od / f"states_{s}.csv", parse_dates=["ts"]); x = st.tail(120); x = x[(x["A"]) | (x["B"]) | (x["V"]) | (x["level"] != "证据不足")]
        for _, r in x.iloc[::-1].head(15).iterrows():
            cand += f'<tr><td>{s}</td><td>{str(r["ts"])[:10]}</td><td class="{LV.get(r["level"], "")}">{r["level"]}</td><td>{_ck(r["L"])}</td><td>{_ck(r["A"])}</td><td>{_ck(r["B"])}</td><td>{_ck(r["V"])}</td><td>{_ck(r["R"])}</td><td>{_ck(r["Q"])}</td><td>{_p(r.get("net5_share"))}</td><td>{_p(r.get("imb3"))}</td><td>{(r.get("tot_vs_med20") or 0):.2f}</td><td>{r["close"]:,.0f}</td></tr>'
    sections.append(f'<h2>吸收候选与条件明细（最近 120 天，按日）{TAG["trade"]}</h2><div class="tbl"><table><tr><th>标的</th><th>日期</th><th>等级</th><th>L</th><th>A</th><th>B</th><th>V</th><th>R</th><th>Q</th><th>5 天净主动</th><th>3 天失衡</th><th>成交额 / 中位</th><th>收盘</th></tr>{cand or "<tr><td colspan=13>最近 120 天没有任何订单流条件成立</td></tr>"}</table></div>')
    # 7 历史验证
    V = R["validation"]; vr = ""
    for seg, sl in (("dev", "开发段 2021-09–2023-12"), ("holdout", "留出段 2024-01 起")):
        for name, nl in (("confirm", "买盘确认"), ("observe", "观察中"), ("control_L", "对照：只看位置"), ("all_days", "对照：全部日子")):
            for h in HORIZONS:
                x = V[seg][name][str(h)]["pooled"] if str(h) in V[seg][name] else V[seg][name][h]["pooled"]
                wl, wh = x.get("wilson_lo"), x.get("wilson_hi"); ci = f" [{_p(wl, 0)}, {_p(wh, 0)}]" if x.get("n") else ""
                vr += f'<tr><td>{sl}</td><td>{nl}</td><td>{h}</td><td>{x.get("n", 0)}</td><td>{x.get("n_independent", 0)}</td><td>{_p(x.get("win_rate"), 0)}{ci}</td><td>{_p(x.get("median_ret"))}</td><td>{_p(x.get("median_mae"))}</td><td>{_p(x.get("worst_mae"))}</td></tr>'
    vd = R["verdict"]; vchk = "".join(f"<li>{'✓' if c['ok'] else '✗'} {html.escape(c['detail'])}</li>" for c in vd["checks"].values())
    sections.append(f'<h2>独立样本验证 {TAG["hist"]}</h2><div class="note">预登记判定：<b>{vd["verdict"]}</b><ul>{vchk}</ul>{html.escape(vd["rule"])}。价格历史已看过；订单流数据此前从未用于本项目设计。</div>'
                    f'<div class="tbl"><table><tr><th>样本段</th><th>方案</th><th>天数</th><th>样本</th><th>独立样本</th><th>盈利比例 [Wilson 95%]</th><th>中位扣费收益</th><th>中位最大不利波动</th><th>最差不利波动</th></tr>{vr}</table></div>'
                    '<div class="small">BTC、ETH 合计；确认日次日开盘买入，往返 30 bps；事件之间 30 天冷却；“全部日子”不设冷却。</div>')
    fe = od / "forward_events.jsonl"; fev = [json.loads(x) for x in fe.read_text().splitlines()] if fe.exists() else []
    sections.append(f'<h2>前瞻“买盘确认”事件 {TAG["fwd"]}</h2>' + ("<div class='tbl'><table><tr><th>标的</th><th>日期</th><th>收盘</th><th>OKX 3 天失衡</th><th>Coinbase 3 天失衡</th></tr>" + "".join(f'<tr><td>{e["symbol"]}</td><td>{e["day"]}</td><td>{e["close"]:,.0f}</td><td>{_p(e.get("imb3_okx"))}</td><td>{_p(e.get("imb3_coinbase"))}</td></tr>' for e in fev) + "</table></div>" if fev else '<div class="small">尚无前瞻事件。事件到期后按 30 / 90 / 180 天评估，未到期不判定。</div>'))
    plots = "".join(f'<div class="plot">{_fig(x, i == 0)}</div>' for i, x in enumerate(figs))
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>真实买盘监测</title>
<style>:root{{--bg:#0f1420;--panel:#171e2e;--line:#2a354d;--text:#e6ebf5;--muted:#8b98b5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;font-size:14px;line-height:1.5;overflow-x:hidden;overflow-wrap:anywhere}}
main{{max-width:1280px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:6px 0}}h2{{font-size:16px;margin:20px 0 8px}}ul{{margin:4px 0;padding-left:20px}}.note{{background:rgba(245,185,66,.12);border:1px solid rgba(245,185,66,.35);color:#f3d58a;padding:8px 10px;border-radius:8px;font-size:12.5px;margin:8px 0}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.grid>*{{min-width:0}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow:hidden}}.card .k{{color:var(--muted);font-size:12px}}.card .v{{font-size:17px;font-weight:600;margin-top:2px}}.card .s{{color:var(--muted);font-size:12px;margin-top:2px}}
.v.up,td.up{{color:#27c281}}.v.warn,td.warn{{color:#f5b942}}td.dn{{color:#f0556a}}.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;color:#fff;vertical-align:middle}}.tag.tr{{background:#1f7a52}}.tag.bk{{background:#8a5a00}}.tag.hi{{background:#6e40c9}}.tag.fw{{background:#0969da}}
.plot{{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;min-width:0;overflow:hidden}}.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px;margin:6px 0}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{color:var(--muted);font-weight:500}}td:first-child,th:first-child{{text-align:left}}.small{{color:var(--muted);font-size:12px;margin:4px 0}}
.foot{{color:var(--muted);font-size:12px;margin-top:20px;border-top:1px solid var(--line);padding-top:8px}}@media (max-width:700px){{.grid{{grid-template-columns:1fr}}main{{padding:10px}}h1{{font-size:17px}}}}</style></head><body><main>
<h1>真实买盘监测 {TAG["trade"]}</h1>
<div class="note">研究页面（第二十二阶段），只读，不下单，真实交易禁用；不影响 B2、SMA200、第二十一阶段四个观察账户与任何运行服务。主动买入 / 卖出全部来自交易所公开逐笔成交的主动方向（OKX：side 为主动方；Coinbase：side 为挂单方，已取反），不使用 K 线成交量；挂单深度可撤销，单独显示、不计为买盘。生成于 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC。</div>
{sections[0]}{sections[1]}{sections[2]}{plots}{"".join(sections[3:])}
<div class="foot">来源：OKX 每日成交归档（2021-09 起）与 /market/trades、history-trades、books-full；Coinbase Exchange /trades、/book。Binance 在本环境返回 451（受限地区），未接入。WebSocket 在云端代理不可用，实时采集为 REST 轮询。撤单与新增无法用公开快照分开（需逐单数据）。预登记：{R["preregistration"]}。</div>
</main></body></html>"""
    p = od / "dashboard22_real_buying.html"; p.write_text(page, encoding="utf-8"); return p


if __name__ == "__main__":
    print(build())
