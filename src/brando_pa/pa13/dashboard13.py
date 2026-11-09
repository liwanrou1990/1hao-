"""第十三阶段前瞻监控只读页面（Plotly 静态 HTML；复用 Phase 11/12 页面技术）。分区：前瞻模拟（影子账户）/ 历史回测（引用 Phase 12 结果，只作对照）/ 真实账户（无）。
内容：各策略影子净值、BTC/ETH 趋势、持仓与现金比例、最近信号与理由、下一候选买入区（Phase 11 已确认支撑区）、当前持仓退出条件、30/90/180/365 绩效（只写已到期）、
与 BTC 持有比较、交易成本 / 信号延迟 / 数据质量 / 服务连续性。不填写未来日期。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from ..config import cfg
from ..data.store import BarStore
from ..pa11.structure import DailyStructureDetector, atr as _atr, swings, structure_state
from ..pa11.zones import SupportResistanceZones
from .audit13 import HORIZONS, daily_nav, shadow_audit
from .shadow import shadow_dirs, shadow_specs

SYMS = ["BTCUSDT", "ETHUSDT"]


def _market_state(store: BarStore, now: pd.Timestamp) -> dict:
    out = {}
    for s in SYMS:
        d = store.load(s, "1d", allow_fallback=False)
        if d.empty:
            out[s] = {"note": "无行情"}; continue
        d["ts"] = pd.to_datetime(d["ts"], utc=True); d = d[d["ts"] + pd.Timedelta(days=1) <= now].reset_index(drop=True)
        c = float(d["close"].iloc[-1]); sma = float(d["close"].rolling(200).mean().iloc[-1]); a = float(_atr(d).iloc[-1]); det = DailyStructureDetector(d); i = len(d) - 1
        z = SupportResistanceZones(det.sw, None, None, det.atr).zones_at(i, c); sup = SupportResistanceZones.nearest_support(z, c); res = SupportResistanceZones.resistances_above(z, c); st = structure_state(det.sw, i)
        out[s] = {"last_closed_day": str(d["ts"].iloc[-1].date()), "close": c, "sma200": sma, "trend": "ON（close > SMA200）" if c > sma else "OFF（close ≤ SMA200）", "atr14": a, "support_zone": [sup.low, sup.high, sup.strength] if sup else None, "next_resistance": [res[0].low, res[0].high, res[0].strength] if res else None, "last_swing_low": st["last_low"], "vol_trail_level_3atr": None}
    return out


def build_dashboard13(log_dir: Path | None = None, out: Path | None = None, embed: bool = True, now: pd.Timestamp | None = None) -> str:
    c = cfg(); d = shadow_dirs(c); log_dir = Path(log_dir or d["log_dir"]); out = Path(out or (log_dir / "dashboard_phase13.html")); now = now or pd.Timestamp.now(tz="UTC")
    a = shadow_audit(log_dir, now=now, write=False); specs = shadow_specs(c); store = BarStore(root=d["bars_root"], fallback_root=None)
    ms = _market_state(store, now) if (d["bars_root"] / "bars").exists() else {s: {"note": "影子行情仓尚未建立"} for s in SYMS}
    started = "accounts" in a and a["accounts"]
    html = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'><title>Brando Phase 13 前瞻监控（只读）</title><style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:20px;color:#222}table{border-collapse:collapse;font-size:12px;margin:8px 0}th,td{border:1px solid #ddd;padding:3px 6px;text-align:right}th{background:#f5f5f5}td:first-child,th:first-child{text-align:left}.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0;background:#fafafa}.note{background:#fff8c5;padding:8px;border-radius:6px}.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;color:#fff;margin-right:6px}.bt{background:#6e40c9}.fw{background:#0969da}.rl{background:#cf222e}.act{font-weight:700}</style></head><body>",
            f"<h1>Brando Price Action · 第十三阶段前瞻监控（只读）</h1><p><span class='tag fw'>前瞻模拟</span>本页主体（独立影子账户）；<span class='tag bt'>历史回测</span>仅底部对照表；<span class='tag rl'>真实账户</span>无（真实交易禁用、无实现）。生成 {str(now)[:19]} UTC。未到期窗口与未发生日期不填数。</p>"]
    # 市场状态
    html.append("<h2><span class='tag fw'>前瞻</span>BTC/ETH 市场趋势与候选区域（按最新已收盘日线）</h2>")
    for s, m in ms.items():
        if "note" in m:
            html.append(f"<div class='card'><h3>{s}</h3><p>{m['note']}</p></div>"); continue
        html.append(f"<div class='card'><h3>{s} · 最新已收盘日 {m['last_closed_day']} · 收盘 {m['close']:,.0f} · SMA200 {m['sma200']:,.0f} · 趋势 {m['trend']}</h3><p>下一个候选买入区域（最近已确认支撑区，Phase 11 规则）：{f'[{m['support_zone'][0]:,.0f}, {m['support_zone'][1]:,.0f}] 强度 {m['support_zone'][2]}' if m['support_zone'] else '无'} · 上方最近阻力：{f'[{m['next_resistance'][0]:,.0f}, {m['next_resistance'][1]:,.0f}]' if m['next_resistance'] else '无（新高区）'} · ATR14 {m['atr14']:,.0f} · 最近已确认摆动低点 {m['last_swing_low'] if m['last_swing_low'] is None else f'{m['last_swing_low']:,.0f}'}</p><p>当前持仓退出条件：A/B/F 日线收盘 ≤ SMA200（{m['sma200']:,.0f}）→ 次轮卖出；A/F 另受状态机（权益回撤 −10% 限仓 50%、−20% 全部卖出）；F 另有 3 ATR 跟踪减仓（最高收盘 − {3 * m['atr14']:,.0f}）；C/D/E 不退出。</p></div>")
    # 影子账户
    html.append("<h2><span class='tag fw'>前瞻</span>影子账户实时净值（cost 账本，单侧 15 bps）</h2>")
    if not started:
        html.append(f"<p class='note'>{a.get('status', '尚未开始')}。页面只读；服务启动后此处显示各账户净值、持仓、最近信号与决策理由。</p>")
    else:
        rows = []
        for n, x in a["accounts"].items():
            lt = x["latest"]; ev = pd.read_json(log_dir / n / "events.jsonl", lines=True); sig = ev[ev["kind"].isin(["signal", "missed_signal"])].tail(1)
            last_sig = f"{str(sig.iloc[0]['signal_ts'])[:16]} → {sig.iloc[0]['signal']}（决策 {str(sig.iloc[0]['decision_time'])[:16]}；{str(sig.iloc[0].get('reason', ''))[:80]}）" if len(sig) else "尚无信号"
            rows.append(f"<tr><td>{n}</td><td>{specs.get(n, {}).get('label', '')}</td><td>{lt['liquidation_usdt']:,.2f}</td><td>{lt['equity_usdt']:,.2f}</td><td>{lt['cash_usdt']:,.0f}（{(lt['cash_usdt'] / lt['equity_usdt']) if lt['equity_usdt'] else 1:.0%}）</td><td>{lt['exposure']:.0%}</td><td>{x['n_signals']}/{x['n_fills']}</td><td>{x['drawdown']['max_drawdown']:.2%} / {x['drawdown']['longest_drawdown_days']} 日</td><td>{'—' if x['diff_vs_btc_buyhold_usdt'] is None else f'{x['diff_vs_btc_buyhold_usdt']:+,.2f}'}</td><td>{last_sig}</td></tr>")
        html.append("<table><thead><tr><th>账户</th><th>策略</th><th>清仓净值</th><th>账面权益</th><th>现金（比例）</th><th>暴露</th><th>信号/成交</th><th>最大回撤 / 持续</th><th>vs BTC 持有</th><th>最近信号与理由</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
        fig = go.Figure()
        for n in a["accounts"]:
            nav = daily_nav(log_dir / n)
            if len(nav):
                fig.add_trace(go.Scatter(x=nav["day"], y=nav["liquidation"], name=n, line=dict(width=1.3)))
        fig.update_layout(title="影子账户清仓口径净值（USDT）· 前瞻", height=420, legend=dict(orientation="h"), margin=dict(l=40, r=20, t=60, b=30)); html.append(fig.to_html(full_html=False, include_plotlyjs=(True if embed else "cdn")))
        html.append("<h3>30 / 90 / 180 / 365 天绩效（只写已到期）</h3><table><thead><tr><th>账户</th>" + "".join(f"<th>{h} 天</th>" for h in HORIZONS) + "</tr></thead><tbody>" + "".join("<tr><td>" + n + "</td>" + "".join(f"<td>{x['horizons'][f'{h}d']['status'] if x['horizons'][f'{h}d']['status'] != '已到期' else f'{x['horizons'][f'{h}d']['net_profit_usdt']:+,.0f}（vs BTC {x['horizons'][f'{h}d'].get('diff_vs_btc_buyhold_usdt', 0):+,.0f}）'}</td>" for h in HORIZONS) + "</tr>" for n, x in a["accounts"].items()) + "</tbody></table>")
        sv = a["service"]["continuity"]
        html.append(f"<h3>成本 / 延迟 / 数据质量 / 连续性</h3><ul>" + "".join(f"<li>{n}：信号延迟 {x['signal_latency_min']} 分钟，成交延迟 {x['fill_latency_min']} 分钟，估算成交 {x['n_fills_estimated']}，迟到 {x['n_fills_late']}，错过 {x['n_missed']}；时间审计 {'通过' if x['timeline_ok'] else '违规'}，账本 {'一致' if x['ledger_ok'] else '问题'}</li>" for n, x in a["accounts"].items()) + f"<li>服务：覆盖率 {sv.get('coverage')}，最长中断 {sv.get('longest_gap_hours')} h，行情正常占比 {sv.get('data_ok_share')}，轮询 {sv.get('cycles_observed')}/{sv.get('cycles_expected')}，主机 {sv.get('hosts')}；中断 {a['service']['gaps_gt_3_intervals'] or '无'}</li></ul>")
    # 历史对照
    p12 = Path("docs/brando_pa_validation/phase12_core_pa/phase12_results.json")
    if p12.exists():
        rows = pd.DataFrame(json.loads(p12.read_text())["rows"]); sel = rows[(rows.window == "full") & (rows.cost_bps == 15) & (rows.group.isin(["A_B2", "B_SMA200_btc", "G_BUYHOLD_BTC", "G_BUYHOLD_ETH", "G_BUYHOLD_5050", "E_B2_vol_trail"]))].sort_values("liquidation_net_profit_usdt", ascending=False)
        html.append("<h2><span class='tag bt'>历史回测</span>对照（Phase 12 日线研究引擎，2018-01 → 2026-11-07，单侧 15 bps；与前瞻引擎规则不完全相同，见冻结清单）</h2><table><thead><tr><th>组</th><th>期末净值</th><th>清仓净收益</th><th>最大回撤</th><th>成交</th></tr></thead><tbody>" + "".join(f"<tr><td>{r['group']}</td><td>{r['final_equity']:,.0f}</td><td>{r['liquidation_net_profit_usdt']:+,.0f}</td><td>{r['max_drawdown']:.1%}</td><td>{r['n_fills']}</td></tr>" for _, r in sel.iterrows()) + "</tbody></table>")
    html.append("<p class='note'>只读页面；不写任何账户；真实交易禁用；评价规则 docs/brando_pa_validation/phase13_forward/evaluation_rules.md。</p></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(html), encoding="utf-8"); return str(out)
