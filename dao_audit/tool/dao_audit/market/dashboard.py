"""Render a real-data monitoring dashboard (self-contained HTML) from a scan.

Shows ONLY real data and its provenance. When sources are unavailable it says
so plainly and marks every opportunity NO TRADE; it never shows invented prices.
"""
from __future__ import annotations
import html
import json
from pathlib import Path


def _badge(status: str) -> str:
    color = {"OK": "#15803d", "TRADE": "#15803d", "UNAVAILABLE": "#b91c1c",
             "STALE": "#b45309", "ERROR": "#b91c1c", "NO_TRADE": "#b45309",
             "INSUFFICIENT_DATA": "#b45309"}.get(status, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:1px 7px;border-radius:6px;font-size:12px">{html.escape(status)}</span>'


def render(scan: dict) -> str:
    g = html.escape(scan.get("generated_at", ""))
    any_real = scan.get("summary", {}).get("any_real_data", False)
    n_trade = scan.get("summary", {}).get("trade_signals", 0)
    banner_color = "#15803d" if any_real else "#b91c1c"
    banner_txt = ("已接入真实数据" if any_real
                  else "所有外部数据源不可达（环境网络策略限制）——未使用任何模拟行情，全部 NO TRADE")

    rows_chain = ""
    for ch, rec in scan.get("chains", {}).items():
        rows_chain += (f"<tr><td>{html.escape(ch)}</td><td>{_badge(rec.get('status','?'))}</td>"
                       f"<td>{rec.get('block') or '-'}</td>"
                       f"<td>{rec.get('data_latency_s') if rec.get('data_latency_s') is not None else '-'}</td>"
                       f"<td>{rec.get('latency_ms') and round(rec['latency_ms']) or '-'} ms</td>"
                       f"<td class='err'>{html.escape(str(rec.get('error') or ''))}</td></tr>")

    rows_dao = ""
    for d in scan.get("daos", []):
        gov = d.get("governance", {})
        rows_dao += (f"<tr><td>{html.escape(d['name'])}</td>"
                     f"<td>{html.escape(d.get('snapshot_space') or '-')}</td>"
                     f"<td>{_badge(gov.get('status','?'))}</td>"
                     f"<td style='font-family:monospace;font-size:11px'>{html.escape((d.get('treasury') or '-'))}</td>"
                     f"<td class='err'>{html.escape(str(gov.get('error') or ''))}</td></tr>")

    rows_opp = ""
    for o in scan.get("opportunities", []):
        dec = o.get("decision", {})
        cost = o.get("cost") or {}
        net = cost.get("net_bps")
        rows_opp += (f"<tr><td>{html.escape(str(o.get('symbol')))}</td>"
                     f"<td>{_badge(o.get('data_status','?'))}</td>"
                     f"<td>{o.get('evidence_sources')}</td>"
                     f"<td>{'-' if net is None else round(net,1)}</td>"
                     f"<td>{_badge(dec.get('decision','?'))}</td>"
                     f"<td>{html.escape(dec.get('reason',''))}</td></tr>")

    raw = html.escape(json.dumps(scan, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DAO Market Monitor</title>
<style>
:root{{--bg:#f8fafc;--fg:#0f172a;--muted:#64748b;--card:#fff;--line:#e2e8f0}}
@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0b1120;--fg:#e2e8f0;--muted:#94a3b8;--card:#111827;--line:#1f2937}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:16px}}
h1{{font-size:20px;margin:8px 0}} h2{{font-size:15px;margin:22px 0 8px}}
.banner{{background:{banner_color};color:#fff;padding:12px 14px;border-radius:10px;font-weight:600}}
.meta{{color:var(--muted);font-size:12px;margin:6px 0 2px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{background:rgba(100,116,139,.12);font-size:12px}} td.err{{color:#b91c1c;font-size:11px;max-width:320px;word-break:break-word}}
details{{margin-top:18px}} pre{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto;font-size:11px;max-height:360px}}
.note{{color:var(--muted);font-size:12px;margin-top:6px}}
</style></head><body><div class="wrap">
<h1>DAO 治理 · 真实链上事件与市场监控看板</h1>
<div class="meta">生成时间 (UTC): {g} · 交易信号: {n_trade}</div>
<div class="banner">{html.escape(banner_txt)}</div>
<p class="note">完整性原则：结果仅来自真实数据；源不可达→UNAVAILABLE→NO TRADE；绝不以模拟行情替代；不承诺固定收益；未经人工确认不连接钱包或执行真实交易。安全发现(VULN-1..5)与交易信号分开管理。</p>

<h2>链 RPC 健康 / 数据新鲜度</h2>
<table><tr><th>链</th><th>状态</th><th>区块</th><th>数据延迟(s)</th><th>往返</th><th>错误/证据</th></tr>{rows_chain or '<tr><td colspan=6>无配置</td></tr>'}</table>

<h2>DAO 治理 / 金库</h2>
<table><tr><th>DAO</th><th>Snapshot 空间</th><th>治理数据</th><th>金库地址</th><th>错误/证据</th></tr>{rows_dao or '<tr><td colspan=5>无配置</td></tr>'}</table>

<h2>交易机会 · 净收益(扣全部成本) · TRADE/NO TRADE</h2>
<table><tr><th>代币</th><th>数据状态</th><th>独立源</th><th>净收益(bps)</th><th>决策</th><th>依据</th></tr>{rows_opp or '<tr><td colspan=6>无</td></tr>'}</table>

<details><summary>原始扫描 JSON（链上证据：source/url/block/timestamp/latency/status）</summary><pre>{raw}</pre></details>
</div></body></html>"""


def write(scan: dict, out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(scan), encoding="utf-8")
    return str(out_path)
