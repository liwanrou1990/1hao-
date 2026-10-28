"""spot90 风险监测与仓位管理 Dashboard（中文，Streamlit）。只读取每日快照；不含任何上涨概率预测、必涨价格或 AI 买入区间。
启动：PYTHONPATH=src python -m streamlit run src/spot90/dashboard/app.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from spot90.config import report_dir  # noqa: E402
from spot90.monitor.position import PositionPlan, Tranche  # noqa: E402

st.set_page_config(page_title="BTC/ETH 风险监测与仓位管理", layout="wide")
C_PRICE, C_MA = "#2f6fdb", ["#8aa4c8", "#c9a227", "#d26a3f", "#3a3a3a"]
RISK_COLOR = {"低": "#2e8b57", "中": "#c9a227", "高": "#d26a3f", "极高": "#b22222"}


@st.cache_data(ttl=300)
def load_snapshot() -> dict | None:
    p = report_dir() / "monitor" / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


def pct(x, d=1):
    return "—" if x is None else f"{x*100:+.{d}f}%"


def money(x, d=2):
    return "—" if x is None else f"{x:,.{d}f}"


snap = load_snapshot()
st.title("BTC / ETH 中长期现货投资：风险监测与仓位管理（研究用）")
if snap is None:
    st.error("未找到快照。请先运行：PYTHONPATH=src python -m spot90.cli ingest && PYTHONPATH=src python -m spot90.monitor.snapshot")
    st.stop()
st.caption(f"快照生成时间 {snap['generated_at'][:19]} UTC · {snap['disclaimer']} · 本页不显示任何上涨概率、必涨价格或 AI 买入区间（三阶段样本外验证均未通过，见 docs/14-16）。")

tabs = st.tabs(["BTC", "ETH", "方案历史比较", "数据来源与验证"])
for tab, sym in zip(tabs[:2], ("BTCUSDT", "ETHUSDT")):
    v = snap["symbols"].get(sym)
    with tab:
        if not v or "error" in v:
            st.error(f"{sym} 无数据"); continue
        m, t, d, w = v["market"], v["trend_risk"], v["distances"], v["windows"]
        if m["data_delayed"]:
            st.warning(f"数据延迟：日线 {m['daily_bar']['freshness']['status']}；参考价 {m['live'].get('status')}。下方价格不是实时价。")
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.6, 1, 1, 0.8, 0.8, 0.8, 1])
        c1.metric(f"{sym[:3]} {m['price_label']}", money(m["price_for_display"]), help=f"参考价来源 {m['live'].get('source')}，价格时间 {m['live'].get('price_ts')}，延迟 {m['live'].get('age_s')} 秒；日线来源 {m['daily_bar']['source']}，最后收盘日 {m['daily_bar']['date']}")
        c2.metric("趋势状态（固定规则）", t["state"])
        c3.metric("风险等级 / 评分", f"{t['risk_level']} / {t['risk_score']:.0f}")
        c4.metric("7 天收益", pct(w["7"]["ret"])); c5.metric("30 天收益", pct(w["30"]["ret"])); c6.metric("90 天收益", pct(w["90"]["ret"]))
        c7.metric("距 365 日高点", pct(w["365"]["dd_from_high"]), help=f"距 30 日高点 {pct(w['30']['dd_from_high'])}")
        st.markdown(f"<div style='padding:6px 10px;border-left:6px solid {RISK_COLOR.get(t['risk_level'], '#888')};background:#f6f6f6'>风险评分构成（各 0~25）："
                    + "，".join(f"{k} {x:.1f}" for k, x in t["components"].items()) + f"。规则：{t['rule_text']}</div>", unsafe_allow_html=True)
        # 价格 + 均线
        s = pd.DataFrame(v["series_400d"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=s["ts"], y=s["close"], name="收盘", line=dict(color=C_PRICE, width=2)))
        for ma, col in zip(("ma20", "ma50", "ma90", "ma200"), C_MA):
            fig.add_trace(go.Scatter(x=s["ts"], y=s[ma], name=ma.upper(), line=dict(color=col, width=1.2)))
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"), title="最近 400 天收盘与均线（日线）", hovermode="x unified", template="simple_white")
        st.plotly_chart(fig, use_container_width=True)
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("距离主要均线 / 高低点")
            lv = d["levels"]
            rows = [("MA20", lv["ma20"], d["vs_ma20"]), ("MA50", lv["ma50"], d["vs_ma50"]), ("MA90", lv["ma90"], d["vs_ma90"]), ("MA200", lv["ma200"], d["vs_ma200"]),
                    ("30 日最高", lv["high_30d"], d["vs_30d_high"]), ("30 日最低", lv["low_30d"], d["vs_30d_low"]), ("90 日最高", lv["high_90d"], d["vs_90d_high"]), ("90 日最低", lv["low_90d"], d["vs_90d_low"]),
                    ("365 日最高", lv["high_365d"], d["vs_365d_high"]), ("365 日最低", lv["low_365d"], d["vs_365d_low"])]
            st.dataframe(pd.DataFrame([{"参考位": a, "价位": money(b), "现价相对": pct(c)} for a, b, c in rows]), hide_index=True, use_container_width=True)
            st.caption(f"MA200 30 日斜率 {pct(v['indicators']['ma200_slope30'], 2)}；RSI14 {v['indicators']['rsi14']:.0f}；ATR14/价 {pct(v['indicators']['atr_rel'] if 'atr_rel' in v['indicators'] else v['indicators']['atr14']/m['price_for_display'], 2)}")
        with col_b:
            st.subheader("历史收益 / 波动率 / 回撤")
            st.dataframe(pd.DataFrame([{"窗口": f"{n} 天", "收益": pct(w[n]["ret"]), "年化波动率": pct(w[n]["rv_ann"], 0), "距窗口最高": pct(w[n]["dd_from_high"]), "距窗口最低": pct(w[n]["up_from_low"])} for n in ("7", "30", "90")]), hide_index=True, use_container_width=True)
            rh = pd.DataFrame({"日期": t["risk_dates_90d"], "风险评分": t["risk_history_90d"]})
            fig2 = go.Figure(go.Scatter(x=rh["日期"], y=rh["风险评分"], line=dict(color="#d26a3f", width=2), name="风险评分"))
            fig2.update_layout(height=200, margin=dict(l=10, r=10, t=30, b=10), title="最近 90 天风险评分（0~100）", yaxis=dict(range=[0, 100]), template="simple_white")
            st.plotly_chart(fig2, use_container_width=True)
        st.subheader("当前趋势状态下的历史 90 天持有统计（描述性，不是预测）")
        ch = pd.DataFrame(v["conditional_history_90d"])
        ch_show = ch.copy()
        for c_ in ("盈利频率", "收益中位", "收益均值", "收益5%分位", "收益95%分位", "浮亏>10%", "浮亏>15%", "浮亏>20%", "平均最大浮亏"):
            ch_show[c_] = ch_show[c_].map(lambda x: pct(x))
        st.dataframe(ch_show, hide_index=True, use_container_width=True)
        st.caption("注意：第三阶段验证显示趋势状态对 90 天结果几乎没有区分力；此表只说明历史分布，不能当成当前条件下的概率。")
        st.subheader("14 天限价单成交概率（已验证的唯一模型；成交概率 ≠ 盈利概率）")
        ft = pd.DataFrame(v["limit_fill_table"])
        if not ft.empty:
            fvv = v["fill_model_validation"] or {}
            fvp = fvv.get("fill_vs_profit", {})
            ft["历史成交后 90 天盈利率"] = ft["折价%"].map(lambda x: pct(fvp.get(str(float(x)), {}).get("profit_rate_given_fill")))
            ft["历史成交后 90 天均值收益"] = ft["折价%"].map(lambda x: pct(fvp.get(str(float(x)), {}).get("mean_ret_given_fill")))
            ft["14天成交概率"] = ft["14天成交概率"].map(lambda x: pct(x, 0)); ft["限价"] = ft["限价"].map(money)
            st.dataframe(ft, hide_index=True, use_container_width=True)
            st.caption(f"模型：逻辑回归（折价/14 天波动标准化距离 + 波动率 + 趋势状态），Walk-Forward 样本外 AUC {fvv.get('auc'):.3f}、Brier {fvv.get('brier'):.3f}、校准误差 ECE {fvv.get('ece'):.3f}（n={fvv.get('n_oos')}，{fvv.get('oos_range')}）；分趋势状态 AUC 0.75~0.80、分波动分位 0.76~0.81。"
                       f" 右两列是历史事实：无论折价多少，成交后 90 天盈利率都在 56%~60%，与立即买入无异——挂低价只改变成交概率，不改变盈利概率。")
        st.subheader("持仓成本 / 止损 / 潜在亏损计算器")
        cc1, cc2, cc3, cc4 = st.columns(4)
        amt = cc1.number_input("买入金额 USDT", 100.0, 10_000_000.0, 10_000.0, step=100.0, key=f"amt{sym}")
        p1 = cc2.number_input("第 1 笔买入价", 1.0, 10_000_000.0, float(round(m["price_for_display"], 2)), key=f"p1{sym}")
        p2 = cc3.number_input("第 2 笔买入价（0=不用）", 0.0, 10_000_000.0, 0.0, key=f"p2{sym}")
        sl = cc4.slider("止损幅度（相对平均成本）", 0.05, 0.40, 0.15, 0.01, key=f"sl{sym}")
        tr = [Tranche(p1, amt if p2 <= 0 else amt / 2)] + ([Tranche(p2, amt / 2)] if p2 > 0 else [])
        r = PositionPlan(tr, stop_loss_pct=sl).compute(m["price_for_display"])
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("持仓成本（含费）", money(r["avg_cost"])); k2.metric("保本价", money(r["breakeven_price"])); k3.metric("止损价", money(r["stop_price"]))
        k4.metric("止损亏损", f"{money(r['loss_at_stop_usdt'])} USDT ({pct(r['loss_at_stop_pct'])})"); k5.metric("按现价盈亏", f"{money(r['pnl_now_usdt'])} ({pct(r['pnl_now_pct'])})")
        ps = pd.DataFrame(v["position_scenarios"])
        for c_ in ("买入价", "持仓成本", "保本价", "止损价", "止损亏损USDT"):
            ps[c_] = ps[c_].map(money)
        ps["止损幅度"] = ps["止损幅度"].map(lambda x: f"{x:.0%}"); ps["止损亏损%"] = ps["止损亏损%"].map(pct); ps["现价相对成本"] = ps["现价相对成本"].map(pct)
        with st.expander("情景表：不同买入折价 × 止损幅度（1 万 USDT）"):
            st.dataframe(ps, hide_index=True, use_container_width=True)

with tabs[2]:
    st.subheader("一次性买入 / 分批定投 / 等待回调：相同起始日、资金、手续费与现金收益假设下的历史比较")
    st.caption("每个历史起始日（2018-09 起）各跑一遍三种方案，窗口末按收盘卖出计净值；手续费 10 bps/边 + 市价滑点 5 bps；等待方案 14 天限价。数字是历史分布，不是对未来的预测。")
    for sym in ("BTCUSDT", "ETHUSDT"):
        pl = snap["symbols"][sym].get("plans")
        if not pl:
            continue
        st.markdown(f"**{sym[:3]}**")
        rows = []
        for key, res in pl.items():
            H, name = key.split("|", 1)
            for p, lab in (("P1", "一次性买入"), ("P2", "分批定投"), ("P3", "等待回调")):
                x = res["plans"][p]
                rows.append({"窗口": H, "参数": name, "方案": lab, "起始日数": res["n_starts"], "平均净收益": pct(x["mean_ret"]), "中位净收益": pct(x["median_ret"]), "盈利频率": pct(x["win_rate"], 0),
                             "5% 分位": pct(x["p5_ret"]), "平均最大回撤": pct(x["mean_mdd"]), "回撤>15%": pct(x["p_mdd_gt_15"], 0), "收益/回撤": f"{x['ret_over_mdd']:.2f}" if x.get("ret_over_mdd") else "—",
                             "与一次性买入差（95% CI）": "—" if p == "P1" else f"{pct(x['diff_vs_P1_mean'])} [{pct(x['diff_vs_P1_ci95'][0])}, {pct(x['diff_vs_P1_ci95'][1])}]",
                             "成交率": pct(x.get("fill_rate"), 0) if p == "P3" else "—"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        first = next(iter(pl.values()))
        by = pd.DataFrame([{"年份": y, **{f"{lab}均值": pct(r[p]["mean_ret"]) for p, lab in (("P1", "一次性"), ("P2", "定投"), ("P3", "等待"))}, **{f"{lab}回撤": pct(r[p]["mean_mdd"]) for p, lab in (("P1", "一次性"), ("P2", "定投"), ("P3", "等待"))}, "起始日数": r["n"]} for y, r in first["by_year"].items()])
        with st.expander(f"{sym[:3]} 默认参数 · 90 天窗口 · 按起始年份"):
            st.dataframe(by, hide_index=True, use_container_width=True)
        bs = pd.DataFrame([{"起始日趋势状态": y, **{f"{lab}均值": pct(r[p]["mean_ret"]) for p, lab in (("P1", "一次性"), ("P2", "定投"), ("P3", "等待"))}, **{f"{lab}回撤": pct(r[p]["mean_mdd"]) for p, lab in (("P1", "一次性"), ("P2", "定投"), ("P3", "等待"))}, "起始日数": r["n"]} for y, r in first["by_state"].items()])
        with st.expander(f"{sym[:3]} 默认参数 · 90 天窗口 · 按起始日趋势状态"):
            st.dataframe(bs, hide_index=True, use_container_width=True)

with tabs[3]:
    st.subheader("数据来源")
    for sym in ("BTCUSDT", "ETHUSDT"):
        m = snap["symbols"][sym]["market"]; db = m["daily_bar"]
        st.markdown(f"- **{sym}** 日线：`{db['source']}`，{db['history_first']} → {db['date']}（{db['n_bars']} 根），新鲜度：{db['freshness']['status']}；与 DefiLlama/CoinGecko 日价中位偏差 {(db.get('cross_check') or {}).get('median_abs_pct', '—')}%。"
                    f" 参考价：`{m['live'].get('source')}`，价格时间 {m['live'].get('price_ts')}，延迟 {m['live'].get('age_s')} 秒，状态 {m['live'].get('status')}；Bitget 探测：{(db.get('bitget_probe') or {}).get('detail')}")
    st.markdown("- 优先级：Bitget 现货（api.bitget.com，本环境被出网策略拒绝时自动跳过）→ GitHub 公开 Binance 现货 K 线缓存（每日更新）→ DefiLlama/CoinGecko 参考价。任何来源失效都会在页顶显示“数据延迟”，不会用旧数据冒充实时价。")
    st.subheader("历史验证摘要（全部为样本外）")
    st.markdown("- 三阶段预测研究（docs/14、15、16）：7/14/30/90 天上涨方向、链上数据、AI 买入区间均未通过样本外检验，本系统因此**不显示**这些内容。\n"
                "- 14 天限价成交概率模型：Walk-Forward 2020-01 → 2026-07，n=57,312，AUC 0.784、Brier 0.188、ECE 0.046，分趋势状态/波动分位/年份稳定（AUC 0.75~0.83）。\n"
                "- 方案历史比较：见“方案历史比较”页；报告 docs/17。")
