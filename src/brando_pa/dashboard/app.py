"""brando_pa 本地控制面板（Streamlit，中文）。只读 reports_out/brando_pa 下的产物：最新信号、关键价位、三策略状态、回测矩阵、Walk-Forward、模拟盘。
启动：PYTHONPATH=src python -m streamlit run src/brando_pa/dashboard/app.py --server.port 8502"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brando_pa.config import cfg, report_dir, resolve  # noqa: E402

C = cfg(); RD = report_dir(C)
st.set_page_config(page_title="Brando PA 引擎", layout="wide")
st.title("Brando Price Action 量化交易引擎 · 控制面板（研究/模拟，默认禁止真实下单）")


@st.cache_data(ttl=60)
def load_json(p: str):
    pp = Path(p)
    return json.loads(pp.read_text()) if pp.exists() else None


def pct(x, d=1):
    return "—" if x is None else f"{x*100:+.{d}f}%"


sig = load_json(str(RD / "signals" / "latest.json"))
tabs = st.tabs(["市场状态与信号", "回测对比", "Walk-Forward", "模拟盘", "导出", "数据审计", "信号漏斗", "入场/退出/多周期对比", "策略筛选", "样本外详情"])

with tabs[0]:
    if not sig:
        st.warning("尚无信号文件。运行：PYTHONPATH=src python -m brando_pa.cli signal")
    else:
        st.caption(f"生成时间 {sig['generated_at'][:19]} UTC · 执行周期 {sig['exec_tf']} · 账户净值假设 {sig['equity']}")
        for s, d in sig["symbols"].items():
            color = {"LONG": "#2e8b57", "SHORT": "#b22222", "NO_TRADE": "#c9a227", "WAIT": "#888", "EXIT": "#2f6fdb"}.get(d["status"], "#888")
            st.markdown(f"<div style='border-left:6px solid {color};padding:6px 10px;background:#f6f6f6'><b>{s}</b> {d['ts'][:16]} 价格 {d['price']:,.2f} → <b>{d['status']}</b> · 日线环境 {d['trend_1d'].get('trend')} · 4h 结构 {d['structure_4h'].get('trend')} · {d.get('freshness','')}</div>", unsafe_allow_html=True)
            if not d.get("data_ok", True):
                st.error("行情数据过期：禁止新开仓（本环境 K 线来自每日更新的公开缓存）")
            c1, c2 = st.columns([1, 1])
            with c1:
                st.markdown("**为什么开仓 / 为什么不交易**")
                for r in d["reasons"]:
                    st.write("- ", r)
                st.markdown("**三套策略当前状态**")
                for k, v in d["strategy_states"].items():
                    st.write(f"- {k}：活动设置 {len(v.get('active_setups', []))}；", "；".join(v.get("wait_reasons", [])[:2]))
                if d.get("candidates"):
                    st.markdown("**本根候选信号与过滤结果**")
                    for e in d["candidates"]:
                        fails = [f"{ch['name']}: {ch['detail']}" for ch in e.get("filter", {}).get("checks", []) if ch["status"] == "FAIL"]
                        st.write(f"- {e['strategy']} {e['signal']['direction']} 毛RR {e['signal']['rr_gross']}；" + ("通过" if not fails else "拒绝：" + "；".join(fails)))
            with c2:
                z = pd.DataFrame(d["zones"])
                if not z.empty:
                    z = z[["tfs", "lo", "hi", "role", "touches", "breaks", "flipped", "strength"]].rename(columns={"tfs": "周期", "lo": "下沿", "hi": "上沿", "role": "角色", "touches": "触及", "breaks": "有效突破", "flipped": "支撑阻力转换", "strength": "强度"})
                    z["周期"] = z["周期"].map(lambda x: "/".join(x)); st.dataframe(z, use_container_width=True, height=360)
                st.json(d["structure_4h"])

with tabs[1]:
    bdir = RD / "backtest"
    names = sorted([p.name for p in bdir.glob("*") if (p / "compare.json").exists()]) if bdir.exists() else []
    if not names:
        st.info("尚无回测。运行：PYTHONPATH=src python -m brando_pa.cli backtest --name latest")
    for n in names:
        cj = load_json(str(bdir / n / "compare.json"))
        st.subheader(f"{n}（执行周期 {cj['exec_tf']}，{cj['period'][0][:10]} → {cj['period'][1][:10]}）")
        bh = cj.get("buy_and_hold", {})
        st.caption("买入持有：" + "，".join(f"{k} {pct(v.get('return'))}" for k, v in bh.items() if isinstance(v, dict) and "return" in v))
        rows = [{"组合": v, "交易数": r["n_trades"], "胜率": pct(r["win_rate"]), "PF": r["profit_factor"], "Expectancy(R)": r["expectancy_r"], "总净收益": pct(r["total_net_return"]), "最大回撤": pct(r["max_drawdown"]), "Sharpe": r["sharpe"], "最大连亏": r["max_consecutive_losses"], "成本": r["total_costs"], "候选→通过": f"{r.get('candidates')}→{r.get('accepted')}"} for v, r in cj["variants"].items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        pick = st.selectbox("查看净值与分组", list(cj["variants"]), key=f"pick_{n}")
        r = cj["variants"][pick]; eqp = Path(r["paths"]["equity_csv"])
        if eqp.exists():
            eq = pd.read_csv(eqp); fig = go.Figure(go.Scatter(x=pd.to_datetime(eq["ts"]), y=eq["equity"], mode="lines")); fig.update_layout(height=280, margin=dict(l=30, r=10, t=20, b=20)); st.plotly_chart(fig, use_container_width=True)
        if r.get("by_regime"):
            st.dataframe(pd.DataFrame(r["by_regime"]).T.rename(columns={"n_trades": "次数", "win_rate": "胜率", "net_pnl": "净盈亏", "expectancy_r": "ExpR", "profit_factor": "PF"}), use_container_width=True)
        st.caption(f"报告 HTML：{r['paths']['report_html']}")

with tabs[2]:
    wdir = RD / "walkforward"
    names = sorted([p.name for p in wdir.glob("*") if (p / "walkforward.json").exists()]) if wdir.exists() else []
    if not names:
        st.info("尚无 Walk-Forward 结果。运行：PYTHONPATH=src python -m brando_pa.cli walkforward --name latest")
    for n in names:
        wj = load_json(str(wdir / n / "walkforward.json")); st.subheader(f"{n}（执行周期 {wj['exec_tf']}）")
        for k, v in wj["results"].items():
            st.markdown(f"**{k}** · OOS：{json.dumps({kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v['oos_stats'].items() if kk != 'exit_reasons'}, ensure_ascii=False)}")
            st.caption(f"默认参数全程：{v['default_full_stats']}")
            st.dataframe(pd.DataFrame([{"折": f["fold"], "训练": f"{f['train'][0]}→{f['train'][1]}", "测试": f"{f['test'][0]}→{f['test'][1]}", "参数": json.dumps(f["chosen_params"]), "退回默认": f["fallback_to_default"], **{f"测试_{kk}": vv for kk, vv in f["test_stats"].items()}} for f in v["folds"]]), use_container_width=True)

with tabs[3]:
    sp = resolve(C["paper"]["state_file"]); stt = load_json(str(sp))
    if not stt:
        st.info("尚无模拟盘状态。运行：PYTHONPATH=src python -m brando_pa.cli paper --once（或 --replay）")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("净值", f"{stt.get('equity', stt['cash']):,.2f}"); c2.metric("已实现盈亏", f"{stt['realized_pnl']:,.2f}"); c3.metric("交易数", stt["n_trades"]); c4.metric("持仓", len(stt["positions"]))
        risk = stt["risk"]; st.caption(f"风控：日期 {risk['day']} 当日已实现 {risk['day_realized_pnl']:.2f}；暂停至 {risk['halted_until_day']}；上次运行 {stt.get('last_run')}；模式 {stt.get('mode')}（真实下单禁止）")
        if stt["positions"]:
            st.dataframe(pd.DataFrame(stt["positions"])[["symbol", "strategy", "direction", "instrument", "qty", "entry_price", "stop", "tp", "risk_usd", "entry_ts"]], use_container_width=True)
        if stt.get("pending"):
            st.write("待执行：", stt["pending"])
        tl = resolve(C["paper"]["log_file"])
        if tl.exists():
            tr = pd.DataFrame([json.loads(x) for x in tl.read_text().splitlines() if x.strip()])
            if not tr.empty:
                tr["cum_pnl"] = tr["net_pnl"].cumsum()
                fig = go.Figure(go.Scatter(x=pd.to_datetime(tr["exit_ts"]), y=tr["cum_pnl"], mode="lines+markers")); fig.update_layout(height=260, title="模拟盘累计净盈亏", margin=dict(l=30, r=10, t=30, b=20)); st.plotly_chart(fig, use_container_width=True)
                st.dataframe(tr[["symbol", "strategy", "direction", "entry_ts", "exit_ts", "entry_price", "exit_price", "net_pnl", "r_multiple", "exit_reason", "regime"]].tail(50), use_container_width=True)
                st.dataframe(tr.groupby("strategy")["net_pnl"].agg(["count", "sum", "mean"]).rename(columns={"count": "次数", "sum": "净盈亏", "mean": "均值"}))
        if stt.get("last_signal"):
            for s, d in stt["last_signal"].items():
                st.write(f"**{s}** 最新 {d['ts'][:16]} {d['status']}：", "；".join(d["reasons"][:3]))

with tabs[4]:
    if st.button("导出 CSV / JSON / HTML"):
        from brando_pa.export import export_all
        st.json(export_all())
    st.caption("命令行：PYTHONPATH=src python -m brando_pa.cli export")


# ===================== 第二阶段诊断页 =====================
P2 = RD / "phase2"


def _load_md(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


with tabs[5]:
    st.subheader("数据质量与回测真实性审计")
    aj = load_json(str(RD / "audit" / "data_quality.json"))
    if not aj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli audit")
    else:
        for s_, v in aj["symbols"].items():
            st.markdown(f"**{s_}**")
            rows = [{"周期": tf, "根数": b.get("n_bars"), "起": (b.get("first") or "")[:10], "最新收盘": (b.get("last_close") or "")[:16], "落后(h)": b.get("age_hours"), "重复": b.get("duplicates"), "错位": b.get("misaligned"), "缺口/缺根": f"{b.get('n_gaps')}/{b.get('missing_bars_total')}", "非法OHLC": b.get("bad_ohlc"), "8σ异常": b.get("n_return_outliers_8sigma"), "状态": b.get("status")} for tf, b in v["bars"].items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
            st.caption("跨周期一致性：" + "；".join(f"{x['pair']} {x.get('verdict', '')}" for x in v["cross_timeframe"]))
            pv = v["perp_vs_spot"]
            if pv.get("available"):
                f8 = pv["funding_8h"]; b = pv.get("basis_mark_vs_spot", {})
                st.caption(f"永续资金费率样本：{'；'.join(g['from'][:10] + '→' + g['to'][:10] for g in pv['covered_segments'])}；8h 均值 {f8['mean']:.6f}（年化 {f8['mean_annualized']:.2%}），回测假设 {pv['assumption_in_backtest_8h']}；mark 基差均值 {b.get('mean_bps', float('nan')):.1f} bps。{pv['gap_statement']}")
        st.json(aj["cost_assumptions"])

with tabs[6]:
    st.subheader("候选信号漏斗：原始候选 → 趋势 → 价格确认 → 成本 → 盈亏比 → 风险 → 有效订单 → 成交")
    edir = P2 / "experiments"
    names = sorted([p.name for p in edir.glob("*") if (p / "experiments.json").exists()]) if edir.exists() else []
    if not names:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli experiments --name h1")
    for n in names:
        ej = load_json(str(edir / n / "experiments.json"))
        base = [k for k in ej["variants"] if k.endswith("__entry_market__mtf_single")]
        pick = st.selectbox(f"{n}（执行周期 {ej['exec_tf']}）选择变体", base + [k for k in ej["variants"] if k not in base], key=f"fun_{n}")
        fp = Path(ej["variants"][pick]["paths"]["summary_json"]).parent / f"{pick}_funnel.csv"
        if fp.exists():
            df = pd.read_csv(fp)
            cols = [c for c in ["symbol", "direction", "strategy", "setup", "candidate", "after_trend", "after_price", "after_cost", "after_rr", "after_risk", "order", "fill", "cancelled_at_fill", "avg_stop_distance_pct", "avg_stop_distance_atr", "median_cost_to_risk", "median_cost_share_of_reward", "avg_net_rr_valid", "top_reject_reasons"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True)
            tot = df[["candidate", "after_trend", "after_price", "after_cost", "after_rr", "after_risk", "order", "fill"]].sum()
            fig = go.Figure(go.Funnel(y=["候选", "趋势过滤后", "价格确认后", "成本检查后", "盈亏比检查后", "风险检查后", "有效订单", "实际成交"], x=tot.values))
            fig.update_layout(height=380, margin=dict(l=20, r=20, t=20, b=20)); st.plotly_chart(fig, use_container_width=True)
    cj = load_json(str(P2 / "cost_to_risk_audit.json"))
    if cj:
        st.markdown("**cost_to_risk 语义审计**"); st.json(cj)

with tabs[7]:
    st.subheader("入场模式（市价 / 限价）× 多周期模式（single / aligned / zone_limit）× 退出方案 对比")
    edir = P2 / "experiments"
    names = sorted([p.name for p in edir.glob("*") if (p / "experiments.json").exists()]) if edir.exists() else []
    for n in names:
        ej = load_json(str(edir / n / "experiments.json")); st.markdown(f"**{n}（执行周期 {ej['exec_tf']}，{ej['start']} →）**")
        rows = [{"变体": k, "交易数": r["n_trades"], "胜率": pct(r["win_rate"]), "PF": r["profit_factor"], "ExpR": r["expectancy_r"], "总净收益": pct(r["total_net_return"]), "最大回撤": pct(r["max_drawdown"]), "Sharpe": r["sharpe"], "连亏": r["max_consecutive_losses"],
                 "手续费": r["total_fees"], "滑点": r["total_slippage"], "资金费": r["total_funding"], "候选→通过": f"{r.get('candidates')}→{r.get('accepted')}", "限价 挂/成/撤": f"{r.get('limit_orders', '—')}/{r.get('limit_filled', '—')}/{r.get('limit_cancelled', '—')}", "未成交机会PnL": r.get("missed_opportunity_net_pnl_sum")} for k, r in ej["variants"].items()]
        df = pd.DataFrame(rows)
        flt = st.multiselect("筛选关键字", ["entry_market", "entry_limit", "mtf_single", "mtf_aligned", "mtf_zone_limit", "exit_", "ALL__", "A_breakout", "B_reclaim", "C_rejection"], default=[], key=f"flt_{n}")
        if flt:
            df = df[df["变体"].apply(lambda x: all(f in x for f in flt))]
        st.dataframe(df, use_container_width=True, height=420)
        pick = st.selectbox("按市场环境 / 方向 / 标的分组查看", list(ej["variants"]), key=f"grp_{n}")
        r = ej["variants"][pick]
        c1, c2, c3 = st.columns(3)
        for col, key_, title in ((c1, "by_regime4", "市场环境"), (c2, "by_direction", "方向"), (c3, "by_symbol", "标的")):
            if r.get(key_):
                col.markdown(f"**{title}**"); col.dataframe(pd.DataFrame(r[key_]).T, use_container_width=True)

with tabs[8]:
    st.subheader("Strategy Evaluator：合格 / 仅研究 / 不合格（全部基于样本外）")
    evj = load_json(str(P2 / "strategy_evaluation.json"))
    if not evj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli evaluate")
    else:
        st.caption(f"门槛：{json.dumps(evj['thresholds'], ensure_ascii=False)}")
        rows = [{"运行": r.get("run"), "变体": r.get("variant"), "周期": r["exec_tf"], "策略": r["strategy"], "标的": r["symbol"], "方向": r["direction"], "状态": r["status"], "OOS交易": r["stats"].get("n_trades"), "ExpR": r["stats"].get("expectancy_r"), "PF": r["stats"].get("profit_factor"), "净收益": pct(r["stats"].get("net_return")), "回撤": pct(r["stats"].get("max_drawdown")), "均值R CI": r["stats"].get("ci_mean_r"), "折非负占比": r["stats"].get("fold_nonneg_share"), "原因": "；".join(r["reasons"])} for r in evj["rows"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=500)
        st.success(f"模拟盘候选：{evj['qualified_paper'] or '无'}"); st.info(f"仅研究：{evj['research_only'] or '无'}")

with tabs[9]:
    st.subheader("样本外详情：年度 / 方向 / 环境 / 费用敏感性 / Bootstrap / 参数扰动")
    wdir = RD / "walkforward"
    names = sorted([p.name for p in wdir.glob("*_p2*") if (p / "walkforward.json").exists()]) if wdir.exists() else []
    if not names:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli walkforward --name h1_p2")
    for n in names:
        wj = load_json(str(wdir / n / "walkforward.json")); st.markdown(f"### {n}（执行周期 {wj['exec_tf']}；覆盖 {wj.get('overrides')}；环境启停 {wj.get('regime_gating')}；窗口 {wj.get('windows')}）")
        for k, v in wj["results"].items():
            o = v["oos_stats"]
            with st.expander(f"{k} · OOS {o.get('n_trades', 0)} 笔 · ExpR {o.get('expectancy_r')} · PF {o.get('profit_factor')} · 回撤 {pct(o.get('max_drawdown'))}"):
                c1, c2, c3 = st.columns(3)
                for col, key_, title in ((c1, "by_year", "年度"), (c2, "by_direction", "方向"), (c3, "by_regime4", "市场环境")):
                    if o.get(key_):
                        col.markdown(f"**{title}**"); col.dataframe(pd.DataFrame(o[key_]).T, use_container_width=True)
                if o.get("cost_sensitivity"):
                    st.markdown("**费用敏感性（按交易记录重算）**"); st.dataframe(pd.DataFrame({k_: v_ for k_, v_ in o["cost_sensitivity"].items() if isinstance(v_, dict)}).T, use_container_width=True)
                    st.caption(f"盈亏平衡费用倍数：{o['cost_sensitivity'].get('breakeven_fee_multiplier')}")
                st.caption(f"Bootstrap 均值 R 95% CI：{o.get('bootstrap_mean_r_95ci')}；净收益 CI：{o.get('bootstrap_net_return_95ci')}")
                if v.get("perturbation"):
                    st.markdown("**参数扰动稳定性（默认参数 ±20%，OOS 跨度）**"); st.dataframe(pd.DataFrame({k_: v_ for k_, v_ in v["perturbation"].items() if k_ != "summary"}).T, use_container_width=True); st.caption(str(v["perturbation"].get("summary")))
                st.dataframe(pd.DataFrame([{"折": f["fold"], "训练": f"{f['train'][0]}→{f['train'][1]}", "测试": f"{f['test'][0]}→{f['test'][1]}", "参数": json.dumps(f["chosen_params"]), "退回默认": f["fallback_to_default"], **{f"测试_{kk}": vv for kk, vv in f["test_stats"].items()}} for f in v["folds"]]), use_container_width=True)
