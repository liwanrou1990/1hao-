"""回测产物：trades.csv、summary.json、signals_log.csv、equity.csv、report.html（K 线 + 入场/出场标记 + 净值曲线，plotly）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text


def _json_default(o):
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def write_outputs(result, summary: dict, out_dir: Path, name: str, contexts: dict | None = None, candle_tf_for_chart: str = "4h") -> dict[str, str]:
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    tr = result.trades.copy()
    if not tr.empty:
        for col in ("signal_ts", "entry_ts", "exit_ts"):
            tr[col] = pd.to_datetime(tr[col], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    tr.to_csv(out_dir / f"{name}_trades.csv", index=False); paths["trades_csv"] = str(out_dir / f"{name}_trades.csv")
    result.equity.to_csv(out_dir / f"{name}_equity.csv", index=False); paths["equity_csv"] = str(out_dir / f"{name}_equity.csv")
    result.signals_log.to_csv(out_dir / f"{name}_signals_log.csv", index=False); paths["signals_csv"] = str(out_dir / f"{name}_signals_log.csv")
    atomic_write_json(out_dir / f"{name}_summary.json", summary | {"cancelled_entries": result.cancelled_entries[:200]}, indent=2, default=_json_default); paths["summary_json"] = str(out_dir / f"{name}_summary.json")
    html = render_html(result, summary, name, contexts, candle_tf_for_chart)
    atomic_write_text(out_dir / f"{name}_report.html", html); paths["report_html"] = str(out_dir / f"{name}_report.html")
    return paths


def _fmt(x, pct=False, d=2):
    if x is None or (isinstance(x, float) and (x != x)):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x*100:.{d}f}%" if pct else (f"{x:,.{d}f}" if isinstance(x, (int, float)) else str(x))


def render_html(result, summary: dict, name: str, contexts: dict | None, candle_tf: str) -> str:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    ts_ = summary.get("trade_stats", {}); es = summary.get("equity_stats", {}); bh = summary.get("buy_and_hold", {})
    rows = [("交易次数", ts_.get("n_trades")), ("胜率", _fmt(ts_.get("win_rate"), True)), ("平均盈利 USDT", _fmt(ts_.get("avg_win"))), ("平均亏损 USDT", _fmt(ts_.get("avg_loss"))),
            ("Profit Factor", _fmt(ts_.get("profit_factor"))), ("Expectancy (R)", _fmt(ts_.get("expectancy_r"), d=3)), ("Expectancy USDT", _fmt(ts_.get("expectancy_usd"))),
            ("最大连亏", ts_.get("max_consecutive_losses")), ("手续费+资金费合计", _fmt((ts_.get("total_fees") or 0) + (ts_.get("total_funding") or 0))), ("成本占毛利", _fmt(ts_.get("cost_share_of_gross"), True)),
            ("总净收益", _fmt(es.get("total_net_return"), True)), ("最大回撤", _fmt(es.get("max_drawdown"), True)), ("Sharpe(日)", _fmt(es.get("sharpe"))), ("Sortino(日)", _fmt(es.get("sortino"))),
            ("买入持有(等权)", _fmt((bh.get("equal_weight") or {}).get("return"), True))]
    table = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    def grp(title, d):
        if not d:
            return ""
        h = "".join(f"<tr><td>{k}</td><td>{v['n_trades']}</td><td>{_fmt(v['win_rate'], True)}</td><td>{_fmt(v['net_pnl'])}</td><td>{_fmt(v['expectancy_r'], d=3)}</td><td>{_fmt(v['profit_factor'])}</td></tr>" for k, v in d.items())
        return f"<h3>{title}</h3><table><tr><th>分组</th><th>次数</th><th>胜率</th><th>净盈亏</th><th>Expectancy R</th><th>PF</th></tr>{h}</table>"
    groups = grp("按策略", summary.get("by_strategy")) + grp("按市场环境（入场日线环境）", summary.get("by_regime")) + grp("按标的", summary.get("by_symbol")) + grp("按年份", summary.get("by_year")) + grp("按方向", summary.get("by_direction")) + grp("按出场原因", summary.get("by_exit_reason"))
    figs_html = ""
    eq = result.equity
    if not eq.empty:
        f = go.Figure(); f.add_trace(go.Scatter(x=eq["ts"], y=eq["equity"], mode="lines", name="净值"))
        f.update_layout(title="组合净值（每根执行 K 线收盘）", height=320, margin=dict(l=40, r=20, t=40, b=30))
        figs_html += f.to_html(full_html=False, include_plotlyjs="cdn")
    if contexts:
        tr = result.trades
        for s, mc in contexts.items():
            bars = mc.bars.get(candle_tf, mc.ex)
            if result.period[0]:
                bars = bars[(bars["ts"] >= pd.Timestamp(result.period[0], tz="UTC")) & (bars["ts"] <= pd.Timestamp(result.period[1], tz="UTC") + pd.Timedelta(days=1))]
            fig = make_subplots(rows=1, cols=1)
            fig.add_trace(go.Candlestick(x=bars["ts"], open=bars["open"], high=bars["high"], low=bars["low"], close=bars["close"], name=s, showlegend=False))
            if not tr.empty:
                t = tr[tr["symbol"] == s]
                for d, sym_, col in (("long", "triangle-up", "#2e8b57"), ("short", "triangle-down", "#b22222")):
                    tt = t[t["direction"] == d]
                    if not tt.empty:
                        fig.add_trace(go.Scatter(x=pd.to_datetime(tt["entry_ts"]), y=tt["entry_price"], mode="markers", marker=dict(symbol=sym_, size=11, color=col), name=f"{d} 入场",
                                                 text=[f"{r.strategy} R={r.r_multiple:.2f} {r.exit_reason}" for r in tt.itertuples()], hoverinfo="text+y+x"))
                fig.add_trace(go.Scatter(x=pd.to_datetime(t["exit_ts"]), y=t["exit_price"], mode="markers", marker=dict(symbol="x", size=9, color="#444"), name="出场"))
            fig.update_layout(title=f"{s} {candle_tf} K 线与入场/出场（执行周期 {result.exec_tf}）", height=520, xaxis_rangeslider_visible=False, margin=dict(l=40, r=20, t=40, b=30))
            figs_html += fig.to_html(full_html=False, include_plotlyjs=False)
    notes = "".join(f"<li>{n}</li>" for n in summary.get("notes", []))
    cands = summary.get("candidates", {})
    rej = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in (cands.get("rejection_counts") or {}).items())
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>brando_pa 回测报告 {name}</title>
<style>body{{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:20px;color:#222}} table{{border-collapse:collapse;margin:8px 0}} td,th{{border:1px solid #ddd;padding:4px 10px;font-size:13px}} th{{background:#f4f4f4}} .warn{{background:#fff6e5;padding:8px;border-left:4px solid #e6a23c}}</style></head>
<body><h1>Brando Price Action 引擎 · 回测报告 <small>{name}</small></h1>
<p>区间 {result.period[0]} → {result.period[1]} · 标的 {', '.join(result.symbols)} · 执行周期 {result.exec_tf}</p>
<div class="warn">本报告为历史回测，含手续费、滑点、资金费假设与保守同根成交规则；不是收益承诺。信号状态统计：{json.dumps(result.status_counts, ensure_ascii=False)}；候选 {cands.get('n_candidates', 0)}，通过过滤 {cands.get('n_accepted', 0)}，入场时取消 {summary.get('n_cancelled_entries', 0)}。</div>
<h2>总览</h2><table>{table}</table>
<h3>候选信号被拒原因（Top）</h3><table><tr><th>原因</th><th>次数</th></tr>{rej}</table>
{groups}
<h2>图表</h2>{figs_html}
<h2>说明</h2><ul>{notes}</ul>
</body></html>"""
