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
tabs = st.tabs(["市场状态与信号", "回测对比", "Walk-Forward", "模拟盘", "导出"])

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
