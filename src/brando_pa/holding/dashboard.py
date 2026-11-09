"""第十阶段只读回测仪表盘（独立 Streamlit 应用；只读 reports_out/brando_pa/phase10_holding/*.json/csv，不触碰任何账本）。
运行：PYTHONPATH=src python -m brando_pa.cli phase10-dashboard --port 8520"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("BRANDO_PHASE10_DIR", ROOT / "reports_out" / "brando_pa" / "phase10_holding"))

st.set_page_config(page_title="第十阶段 · 中长期现货持仓回测（只读）", layout="wide")
st.title("第十阶段 · BTC/ETH 中长期现货持仓历史回测（只读仪表盘）")
st.caption("类别：历史回测（日线决策、次日开盘执行）。不是前瞻模拟，不读写任何前瞻账户。真实交易禁用。")
res_p = OUT / "phase10_results.json"
if not res_p.exists():
    st.warning(f"未找到 {res_p}；先运行 `python -m brando_pa.cli phase10`"); st.stop()
res = json.loads(res_p.read_text()); rows = pd.DataFrame(res["rows"]); curves = json.loads((OUT / "curves.json").read_text()) if (OUT / "curves.json").exists() else {}
trades = pd.read_csv(OUT / "trades.csv") if (OUT / "trades.csv").exists() else pd.DataFrame()
st.markdown(f"数据截至 **{res['latest_complete_day']}** · 初始本金 10,000 USDT · 排名依据 {res['rank_cost_bps']} bps 单侧成本下的假设清仓净收益 · 预登记：`{res['preregistration']}`")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["策略排名", "净值与回撤", "年度收益", "成本比较", "交易记录"])
with tab1:
    w = st.selectbox("窗口", list(res["windows"]), index=0, key="w1"); cost = st.selectbox("单侧成本 bps", res["costs_one_side_bps"], index=len(res["costs_one_side_bps"]) - 1, key="c1")
    rk = pd.DataFrame(res["rankings"].get(f"{w}|{cost}", []))
    if len(rk):
        ex = pd.json_normalize(rk["excess_vs_bh"]).add_prefix("excess_vs_"); rk = pd.concat([rk.drop(columns=["excess_vs_bh"]), ex], axis=1)
        st.dataframe(rk.style.format({c: "{:+,.0f}" for c in rk.columns if "usdt" in c or c.startswith("excess_vs_")} | {"cagr_liquidation": "{:.1%}", "max_drawdown": "{:.1%}", "total_cost_usdt": "{:,.0f}"}), use_container_width=True, hide_index=True)
    sel = rows[(rows["window"] == w) & (rows["cost_bps"] == cost)]
    if len(sel):
        st.markdown("**期末拆分：已实现 / 未实现 / 假设清仓**")
        st.dataframe(sel[["name", "final_equity", "equity_profit_usdt", "realized_net_usdt", "unrealized_usdt", "hypothetical_exit_cost_usdt", "liquidation_net_profit_usdt", "fees_usdt", "slippage_usdt", "n_fills", "time_in_market"]].sort_values("liquidation_net_profit_usdt", ascending=False), use_container_width=True, hide_index=True)
with tab2:
    w2 = st.selectbox("窗口", [k for k in res["windows"] if any(key.startswith(f"{w}|") for key in curves for w in [k])], key="w2"); c2 = st.selectbox("单侧成本 bps", sorted({int(k.split("|")[1]) for k in curves if k.startswith(f"{w2}|")}), key="c2")
    names = [k.split("|")[2] for k in curves if k.startswith(f"{w2}|{c2}|")]; pick = st.multiselect("策略", names, default=[n for n in names if n.startswith(("S1", "S3", "S7", "S8a"))])
    if pick:
        df = pd.concat([pd.DataFrame(curves[f"{w2}|{c2}|{n}"]).assign(strategy=n) for n in pick]); df["ts"] = pd.to_datetime(df["ts"])
        st.line_chart(df.pivot(index="ts", columns="strategy", values="liquidation"), height=360); st.caption("清仓口径净值（扣假设卖出成本）")
        st.line_chart(df.pivot(index="ts", columns="strategy", values="drawdown"), height=240); st.caption("回撤（账面权益口径）")
with tab3:
    w3 = st.selectbox("窗口", list(res["windows"]), key="w3"); c3 = st.selectbox("单侧成本 bps", res["costs_one_side_bps"], index=len(res["costs_one_side_bps"]) - 1, key="c3")
    sel = rows[(rows["window"] == w3) & (rows["cost_bps"] == c3)]
    if len(sel):
        ar = pd.DataFrame({r["name"]: r["annual_returns"] for _, r in sel.iterrows()}).T
        st.dataframe(ar.style.format("{:+.1%}"), use_container_width=True)
    yi = pd.DataFrame(res.get("yearly_independent", []))
    if len(yi):
        st.markdown("**逐年独立起算（每年 10,000 USDT，25 bps）假设清仓净收益**"); st.dataframe(yi.pivot(index="strategy", columns="year", values="liquidation_net_profit_usdt").style.format("{:+,.0f}"), use_container_width=True)
with tab4:
    w4 = st.selectbox("窗口", list(res["windows"]), key="w4"); sel = rows[rows["window"] == w4]
    if len(sel):
        st.dataframe(sel.pivot(index="name", columns="cost_bps", values="liquidation_net_profit_usdt").style.format("{:+,.0f}"), use_container_width=True); st.caption("假设清仓净收益 × 单侧成本 bps")
        st.dataframe(sel.pivot(index="name", columns="cost_bps", values="total_cost_usdt").style.format("{:,.0f}"), use_container_width=True); st.caption("总交易成本（手续费 + 滑点）USDT")
with tab5:
    if len(trades):
        s5 = st.selectbox("策略", sorted(trades["strategy"].unique()), key="s5"); w5 = st.selectbox("窗口", sorted(trades["window"].unique()), key="w5"); c5 = st.selectbox("成本", sorted(trades["cost_bps"].unique()), key="c5")
        t = trades[(trades["strategy"] == s5) & (trades["window"] == w5) & (trades["cost_bps"] == c5)]
        st.dataframe(t, use_container_width=True, hide_index=True); st.caption(f"{len(t)} 笔闭合交易；期末未平仓头寸不在此表（计入未实现 / 假设清仓）")
st.markdown("---"); st.caption("本页只读；不修改任何前瞻账户、事件链或策略参数。")
