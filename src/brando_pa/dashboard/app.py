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
tabs = st.tabs(["市场状态与信号", "回测对比", "Walk-Forward", "模拟盘", "导出", "数据审计", "信号漏斗", "入场/退出/多周期对比", "策略筛选", "样本外详情", "第三阶段：止损诊断与结构入场", "纯现货研究（第四阶段）", "趋势参与研究（第五阶段）", "三层趋势系统（第六阶段）", "USDT 利润与资产配置（第七阶段）"])

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


with tabs[10]:
    st.subheader("第三阶段：策略 A 止损距离诊断、模式 A/B 结构入场、预登记锁定测试")
    P3 = RD / "phase3"; pj = load_json(str(P3 / "phase3_results.json"))
    if not pj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli --exec-tf 1h phase3")
    else:
        st.caption(f"窗口：{json.dumps(pj['windows'], ensure_ascii=False)}；预登记见 docs/brando_pa_validation/phase3/preregistration.md")
        sd = pj.get("stop_diag")
        if sd:
            st.markdown("**策略 A 止损距离诊断（训练窗）**")
            c1, c2 = st.columns([1, 1])
            c1.json({"候选": sd["n_candidates"], "止损/ATR 中位": sd["stop_atr_all"].get("median"), "通过过滤中位": sd["stop_atr_accepted"].get("median"), "结构点在区域另一侧占比": sd.get("share_structure_on_zone_far_side"), "分解占比": sd["decomposition_share"], "结论": sd["verdict"]})
            for ch in sd.get("charts", []):
                if Path(ch).exists():
                    c2.image(ch)
        rows = []
        for sk, d in pj["train_valid"].items():
            for sm, r in d.items():
                rows.append({"策略": sk, "止损": sm, "训练 n": r["train"].get("n_trades"), "训练 ExpR": r["train"].get("expectancy_r"), "验证 n": r["valid"].get("n_trades"), "验证 ExpR": r["valid"].get("expectancy_r"), "验证 PF": r["valid"].get("profit_factor"), "验证 被扫损": r["valid"].get("stopped_share")})
        st.markdown("**训练 / 验证（选止损模式）**"); st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption("选定：" + json.dumps({k: v["chosen"] for k, v in pj["selected_stop_mode"].items()}, ensure_ascii=False))
        rows = []
        for name, d in (("L2 保留集", pj["holdout"]), ("L1 锁定集", pj["locked"])):
            for sk, r in d.items():
                ci = r.get("bootstrap_mean_r_95ci") or {}
                rows.append({"集合": name, "策略": sk, "止损": r.get("stop_mode"), "n": r.get("n_trades"), "ExpR": r.get("expectancy_r"), "胜率": r.get("win_rate"), "PF": r.get("profit_factor"), "净收益": pct(r.get("net_return")), "回撤": pct(r.get("max_drawdown")), "CI": [ci.get("lower"), ci.get("upper")], "候选→通过": f"{r.get('candidates')}→{r.get('accepted')}"})
        st.markdown("**锁定测试（只运行一次）**"); st.dataframe(pd.DataFrame(rows), use_container_width=True)
        rows = [{"策略": sk, "场景": n, **r} for sk, d in pj["stress"].items() for n, r in d.items()]
        st.markdown("**压力测试（保留集）**"); st.dataframe(pd.DataFrame(rows), use_container_width=True)
        for sk, r in pj["holdout"].items():
            g = r.get("by_symbol_regime_direction")
            if g:
                st.markdown(f"**{sk}：标的 × 环境 × 方向（保留集）**"); st.dataframe(pd.DataFrame(g).T, use_container_width=True)
        m15 = load_json(str(P3 / "m15_descriptive.json"))
        if m15:
            st.markdown("**15m 描述性对照（历史 22 个月，不参与选型）**"); st.dataframe(pd.DataFrame({k: {kk: v.get(kk) for kk in ("n_trades", "expectancy_r", "profit_factor", "net_return", "max_drawdown", "candidates", "accepted", "stop_mode")} for k, v in m15.items()}).T, use_container_width=True)
        fp = RD.parent.parent / "data_store/brando_pa/derivatives/forward_snapshots.parquet"
        if fp.exists():
            st.markdown("**永续前瞻快照（CoinGecko，每次模拟盘运行采集；不回填历史）**"); st.dataframe(pd.read_parquet(fp).tail(10), use_container_width=True)


with tabs[11]:
    st.subheader("BTC/ETH 纯现货价格行为研究（只做多、无杠杆；默认零成本研究模式；禁止真实下单）")
    P4 = RD / "phase4_spot"; pj = load_json(str(P4 / "phase4_results.json")); ls = load_json(str(P4 / "latest_signal.json"))
    st.warning("可信度警告：1h 历史 2022-04 起已在前三阶段反复观察，保留集 2025-07→ 为半锁定；真正未使用的数据只有 2022-01→03；15m 仅 22 个月；全部为理想成本研究，含成本结果见对照列。")
    if ls:
        st.markdown("**最新可用价格与 BUY / HOLD / SELL**")
        for s_, d in ls.items():
            color = {"BUY": "#2e8b57", "SELL": "#b22222", "HOLD": "#888"}.get(d.get("decision"), "#888")
            st.markdown(f"<div style='border-left:6px solid {color};padding:6px 10px;background:#f6f6f6'><b>{s_}</b> 数据截至 {str(d.get('data_last_close'))[:16]} UTC · 最新收盘 {d.get('price')} → <b>{d.get('decision')}</b> {d.get('strategy') or ''}<br><small>日线环境 {d.get('regime4')} · 4h 结构 {d.get('trend_4h')} · 本地阻力 {d.get('local_res')} · 本地支撑 {d.get('local_sup')} · {'靠近高周期支撑' if d.get('near_htf_support') else '不在高周期支撑附近'}</small><br><small>{d.get('reasons')}</small><br><small>模拟现金 {d.get('cash_sim')} · 模拟持仓 {d.get('holding_qty_sim')}（{d.get('note')}）</small></div>", unsafe_allow_html=True)
    if not pj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli phase4")
    else:
        for w, modes in pj["windows"].items():
            with st.expander(f"窗口 {w}：{modes['zero_cost']['period'][0][:10]} → {modes['zero_cost']['period'][1][:10]}", expanded=(w == "holdout")):
                for mode, r in modes.items():
                    st.markdown(f"**{'零成本（理想研究）' if mode == 'zero_cost' else '含成本 10 bps + 5 bps'}**")
                    rows = [{"组合": k, "总收益": pct(v["equity"].get("total_return")), "年化": pct(v["equity"].get("cagr")), "最大回撤": pct(v["equity"].get("max_drawdown")), "Sharpe": v["equity"].get("sharpe_daily"), "交易数": v["trades"].get("n_trades"), "胜率": pct(v["trades"].get("win_rate")), "平均每笔": v["trades"].get("avg_pnl"), "PF": v["trades"].get("profit_factor"), "ExpR": v["trades"].get("expectancy_r"), "持仓时间占比": pct(v["util"].get("time_in_market_share"), 0), "资金利用率": pct(v["util"].get("avg_exposure"), 0), "超额 vs B&H": pct(v.get("excess_vs_bh"))} for k, v in r["rows"].items()]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                    if r.get("chart") and Path(r["chart"]).exists():
                        st.image(r["chart"])
                    for k, v in r["rows"].items():
                        if k.startswith(("C_", "D_", "E_策略")) and v["trades"].get("n_trades"):
                            c1, c2, c3 = st.columns(3)
                            c1.markdown(f"{k} 按策略"); c1.dataframe(pd.DataFrame(v["trades"]["by_strategy"]).T)
                            c2.markdown("按环境"); c2.dataframe(pd.DataFrame(v["trades"]["by_regime4"]).T)
                            c3.markdown("年度"); c3.dataframe(pd.Series(v["yearly"], name="收益").to_frame())
                pick = st.selectbox("查看买卖标记", [p_.name for p_ in P4.glob(f"{w}_zero_*_trades.html")], key=f"html_{w}") if list(P4.glob(f"{w}_zero_*_trades.html")) else None
                if pick:
                    import streamlit.components.v1 as components
                    components.html((P4 / pick).read_text(encoding="utf-8"), height=540, scrolling=True)
                eqs = list(P4.glob(f"{w}_zero_*_equity.csv"))
                if eqs:
                    pick2 = st.selectbox("现金与持仓曲线", [p_.name for p_ in eqs], key=f"eq_{w}")
                    eq = pd.read_csv(P4 / pick2); eq["ts"] = pd.to_datetime(eq["ts"])
                    fig = go.Figure(); fig.add_trace(go.Scatter(x=eq["ts"], y=eq["equity"], name="权益")); fig.add_trace(go.Scatter(x=eq["ts"], y=eq["cash"], name="现金"))
                    fig.update_layout(height=280, margin=dict(l=30, r=10, t=20, b=20)); st.plotly_chart(fig, use_container_width=True)


with tabs[12]:
    st.subheader("第五阶段：趋势状态 + 模型 A/B/C + 三种退出 vs 十组对照（纯现货，禁止真实下单）")
    P5 = RD / "phase5_spot"; pj = load_json(str(P5 / "phase5_results.json")); ls = load_json(str(P5 / "latest_trend_signal.json"))
    st.warning("可信度：全部历史区间在前四阶段已被观察；本阶段为历史稳健性研究，不存在新的样本外验证。零成本为理想条件。")
    if ls:
        for s_, d in ls.items():
            color = {"BULL": "#2e8b57", "BEAR": "#b22222", "RANGE": "#c9a227", "UNCERTAIN": "#888"}.get(d.get("state"), "#888")
            st.markdown(f"<div style='border-left:6px solid {color};padding:6px 10px;background:#f6f6f6'><b>{s_}</b> 数据截至 {str(d.get('data_last_close'))[:16]} UTC · 收盘 {d.get('price')} · 日线状态 <b>{d.get('state')}</b>（SMA50 {d.get('sma50')}, SMA200 {d.get('sma200')}）· 4h {d.get('trend_4h')}<br><small>" + " | ".join(f"{m}: {v.get('decision')} — {v.get('reasons')}" for m, v in (d.get('models') or {}).items()) + "</small></div>", unsafe_allow_html=True)
    if not pj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli phase5")
    else:
        for s_, rs in pj.get("regime_summary", {}).items():
            st.caption(f"{s_} 日线状态分布 {rs['state_counts']}，切换 {rs['transitions']} 次；各年 BULL 占比 {rs['bull_share_by_year']}")
        for w, d in pj["windows"].items():
            with st.expander(f"窗口 {w}（{d['exec_tf']}）：{d['period'][0]} → {d['period'][1] or '最新'}", expanded=(w == "full_1h")):
                for rnd in ("round1", "round2"):
                    if rnd not in d:
                        continue
                    st.markdown(f"**{'零成本' if rnd == 'round1' else '10 bps + 5 bps'}**")
                    rows = [{"组合": r["name"], "总收益": pct(r.get("total_return")), "年化": pct(r.get("cagr")), "最大回撤": pct(r.get("max_drawdown")), "Sharpe": r.get("sharpe"), "Calmar": r.get("calmar"), "交易": r.get("n_trades"), "平均仓位": pct(r.get("avg_exposure"), 0), "BULL期持仓": pct(r.get("bull_time_in_market_share"), 0), "上涨月捕获": pct(r.get("up_month_capture"), 0), "下跌月捕获": pct(r.get("down_month_capture"), 0), "卖后错过合计": pct(r.get("missed_after_sell_sum"), 0), "止损后再入": r.get("reentries_after_stop"), "均持仓天": r.get("avg_hold_days"), "超额 vs B&H": pct(r.get("excess_vs_bh")), "低回撤因空仓": r.get("low_dd_due_to_idle_cash")} for r in d[rnd]["rows"]]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=560)
                    if d[rnd].get("chart") and Path(d[rnd]["chart"]).exists():
                        st.image(d[rnd]["chart"])
        rec = pj.get("phase4_btc_valid_reconstruction")
        if rec:
            st.markdown("**第四阶段 BTC 验证窗（买入持有 +73%，原策略 +7.9%）逐笔还原**")
            st.json(rec["exposure"]); st.json(rec["reconstruction"]["summary"]); st.dataframe(pd.DataFrame(rec["reconstruction"]["segments"]), use_container_width=True, height=300)

with tabs[13]:
    st.subheader("第六阶段：SMA200 审计 · 策略 A/B · 回撤熔断状态机 · 前瞻模拟（纯现货，禁止真实下单）")
    P6 = RD / "phase6_spot"; pj = load_json(str(P6 / "phase6_results.json")); ls = load_json(str(P6 / "latest_trendsys_signal.json"))
    st.warning("可信度：全部历史窗口在前五阶段已被观察（训练 / 验证 / 已被观察的测试窗），没有新的样本外数据；本页为历史稳健性研究。真正的新数据只来自前瞻模拟记录。")
    if ls:
        for s_, d in ls.items():
            st.markdown(f"**{s_}** 数据截至 {str(d.get('data_last_close'))[:16]} UTC · 日线收盘 {d.get('daily_close')} · SMA200 {d.get('sma200'):.1f} · " + " | ".join(f"{k}: {'ON' if v['on'] else 'OFF'}（{v['signal_day']}）" for k, v in d["strategies"].items()) + f" · Brando 辅助信号 {'开' if d.get('brando_signals_enabled') else '关（默认）'}")
    if not pj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli phase6")
    else:
        acc = pj.get("acceptance", {})
        st.success(acc.get("final", {}).get("statement", ""))
        st.json({k: v.get("pass") for k, v in acc.get("criteria", {}).items()})
        with st.expander("SMA200 基准审计（复现第五阶段 BTC +232% / ETH +114%）", expanded=False):
            for s_, a in (pj.get("audit") or {}).items():
                st.caption(f"{s_}: 第五阶段 {pct(a['phase5_benchmark']['total_return'])} · 独立复算 {pct(a['independent_reimpl']['total_return'])} · 三层引擎 {pct(a['trendsys_A_none']['total_return'])} · 最大相对差 {a['max_abs_rel_diff_phase5_vs_independent']:.1e} · 预热首日 {a['warmup']['first_sma_day']} · 成交均在日线收盘后首根开盘 {a.get('fill_timing', {}).get('all_fills_at_first_bar_after_daily_close')} · 截断一致 {a['lookahead']['truncation_identical']} · 若误用未收盘日线 {pct(a['lookahead']['if_used_unclosed_daily_bar_total_return'])}")
        for w, d in pj["windows"].items():
            with st.expander(f"窗口 {w}（{d.get('label')}，{d['exec_tf']}）：{d['period'][0]} → {d['period'][1] or '最新'}", expanded=(w == "full_1h")):
                for rnd in ("round1", "round2"):
                    if rnd not in d:
                        continue
                    st.markdown(f"**{'零成本' if rnd == 'round1' else '10 bps + 5 bps'}**")
                    rows = [{"组合": r["name"], "总收益": pct(r.get("total_return")), "年化": pct(r.get("cagr")), "最大回撤": pct(r.get("max_drawdown")), "Sharpe": r.get("sharpe"), "Calmar": r.get("calmar"), "卖出次数": r.get("n_trades"), "平均仓位": pct(r.get("avg_exposure"), 0), "上涨月捕获": pct(r.get("up_month_capture"), 0), "下跌月捕获": pct(r.get("down_month_capture"), 0), "熔断次数": r.get("n_halts"), "禁买时间占比": pct((r.get("halt_episodes") or {}).get("share"), 0), "最长禁买天": (r.get("halt_episodes") or {}).get("longest_days"), "超额 vs B&H": pct(r.get("excess_vs_bh"))} for r in d[rnd]["rows"]]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=560)
                    if d[rnd].get("chart") and Path(d[rnd]["chart"]).exists():
                        st.image(d[rnd]["chart"])
                if d.get("state_chart") and Path(d["state_chart"]).exists():
                    st.image(d["state_chart"])
        if pj.get("phase5_tactical_frequency"):
            st.markdown("**第五阶段模型 B 战术袖子 / 原 Brando 交易频率（只读历史产物）**")
            st.dataframe(pd.DataFrame(pj["phase5_tactical_frequency"]).T.drop(columns=[c for c in ("by_exit", "by_strategy_net_pnl", "by_category") if c in pd.DataFrame(pj["phase5_tactical_frequency"]).T.columns]), use_container_width=True)
    pdir = RD / "trendsys_paper"
    if (pdir / "nav.jsonl").exists():
        st.markdown("**前瞻模拟记录（追加写入、哈希链）**")
        nav = pd.read_json(pdir / "nav.jsonl", lines=True)
        st.line_chart(nav.set_index("signal_ts")["equity"]) if len(nav) > 1 else st.json(nav.tail(1).to_dict("records"))
        for name in ("signals", "fills", "risk_state", "failures"):
            fp = pdir / f"{name}.jsonl"
            if fp.exists() and fp.stat().st_size:
                st.caption(name); st.dataframe(pd.read_json(fp, lines=True).tail(20), use_container_width=True)

with tabs[14]:
    st.subheader("第七阶段：BTC/ETH/USDT 纯现货动态资金配置 · USDT 利润排行榜（禁止真实下单）")
    P7 = RD / "phase7_usdt"; pj = load_json(str(P7 / "phase7_results.json")); la = load_json(str(P7 / "latest_allocation.json"))
    st.warning("可信度：全部历史窗口在前六阶段已被观察，没有新的样本外数据；风险约束上限为占位值、未经用户批准，不得据此宣称适合真实交易。零成本结果不是可实际获得的收益。")
    if la:
        st.markdown(f"数据截至 {la['data_last_close']} · 趋势 {la['trend_on']} · " + " | ".join(f"{m}: {v['target']}" for m, v in la["models"].items()))
    if not pj:
        st.info("运行：PYTHONPATH=src python -m brando_pa.cli phase7")
    else:
        rc = pj["risk_constraint"]; st.caption(f"风险约束占位：最大回撤 ≤ {rc['max_drawdown']:.0%}（approved_by_user = {rc['approved_by_user']}）")
        cols = [("策略", "name"), ("期末权益USDT", "final_equity_usdt"), ("期末清仓USDT", "liquidation_usdt"), ("可实现利润USDT", "realizable_profit_usdt"), ("总收益率", "total_return"), ("最大回撤", "max_drawdown"), ("最大回撤USDT", "max_drawdown_usdt"), ("交易次数", "n_trades"), ("平均持仓", "avg_exposure"), ("BTC贡献", "contrib_btc_usdt"), ("ETH贡献", "contrib_eth_usdt"), ("成本USDT", "cost_usdt"), ("vs BTC SMA200 USDT", "excess_vs_btc_sma200_usdt"), ("满足约束", "meets_risk_constraint"), ("独立验证", "independently_validated"), ("最长空仓天", "longest_all_cash_days"), ("上涨月捕获", "up_month_capture"), ("熔断", "n_halts")]
        def tbl(rows):
            return pd.DataFrame([{c: (pct(r.get(k)) if k in ("total_return", "max_drawdown", "avg_exposure", "up_month_capture") else (f"{r.get(k):,.0f}" if isinstance(r.get(k), (int, float)) and k.endswith(("usdt", "_usdt")) else r.get(k))) for c, k in cols} for r in rows])
        for w, d in pj["windows"].items():
            with st.expander(f"窗口 {w}（{d.get('label')}，{d['exec_tf']}）：{d['period'][0]} → {d['period'][1] or '最新'}", expanded=(w == "full_1h")):
                for rnd in ("round1", "round2"):
                    if rnd not in d:
                        continue
                    st.markdown(f"**{'零成本（理想）' if rnd == 'round1' else '10 bps + 5 bps（压力假设）'} · 满足风险约束，按期末清仓 USDT 排序**"); st.dataframe(tbl(d[rnd]["leaderboard_ok"]), use_container_width=True)
                    if d[rnd]["leaderboard_fail"]:
                        st.markdown("**不满足风险约束（单独展示）**"); st.dataframe(tbl(d[rnd]["leaderboard_fail"]), use_container_width=True)
                    if d[rnd].get("chart") and Path(d[rnd]["chart"]).exists():
                        st.image(d[rnd]["chart"])
                    st.caption("月度环境诊断（USDT 盈亏之和）"); st.dataframe(pd.DataFrame(d[rnd]["regime_months"]["pnl_usdt"]).T, use_container_width=True)
                for k in ("position_chart", "contrib_chart"):
                    if d.get(k) and Path(d[k]).exists():
                        st.image(d[k])
                al = P7 / f"{w}_round1_A_equal_weight_state_machine_alloc_log.csv"
                if al.exists():
                    a = pd.read_csv(al); st.caption("模型 A + 状态机：再平衡日志（最近 30 条）"); st.dataframe(a[a["rebalance"]].tail(30), use_container_width=True)
    pdir = RD / "usdt_paper"
    if (pdir / "nav.jsonl").exists():
        st.markdown("**第七阶段前瞻模拟（追加写入、哈希链）**"); nav = pd.read_json(pdir / "nav.jsonl", lines=True)
        st.dataframe(nav.tail(10), use_container_width=True)
        for name in ("signals", "fills", "failures"):
            fp = pdir / f"{name}.jsonl"
            if fp.exists() and fp.stat().st_size:
                st.caption(name); st.dataframe(pd.read_json(fp, lines=True).tail(20), use_container_width=True)
