"""第四阶段预登记流程：六组对照（A/B 买入持有 BTC/ETH、C/D/E 策略 BTC/ETH/双资产、F 全程 USDT）× 窗口 × 成本模式；零自由度，一次性运行。
产出 reports_out/brando_pa/phase4_spot/：phase4_results.json/md、各组合 equity/trades/fills/decisions CSV、净值曲线 PNG、买卖标记 HTML、最新信号 JSON。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..backtest.oos_metrics import block_bootstrap_ci
from ..backtest.runner import load_contexts
from ..config import cfg, report_dir
from ..engine.context import regime_frame
from .engine import SpotBacktester, buy_and_hold
from .report import compare_table, equity_stats, plot_equity, plot_trades_html, regime_returns, trade_stats, utilization, yearly

CONFIGS = {"C_策略_BTC": ["BTCUSDT"], "D_策略_ETH": ["ETHUSDT"], "E_策略_BTC+ETH": ["BTCUSDT", "ETHUSDT"]}


def _regime_series(ctx_btc) -> pd.Series:
    """市场环境（BTC 日线四分类，用于按环境分段统计）；只用已收盘日线，按日线收盘时间对齐。"""
    rf = regime_frame(ctx_btc.bars["1d"], ctx_btc.c["regime"])
    cls = rf["trend"].map({"strong_up": "强势上涨", "up": "上涨趋势", "strong_down": "强势下跌", "down": "下跌趋势", "range": "横盘震荡", "undefined": "横盘震荡"})
    cls = cls.where(~rf["highvol"], "高波动异常")
    return pd.Series(cls.values, index=pd.to_datetime(rf["ts"]) + pd.Timedelta(days=1))


def run_window(c: dict, ctxs: dict, exec_tf: str, name: str, start, end, research: bool, out_dir: Path, initial: float, regime_series: pd.Series, write_charts: bool = True) -> dict:
    rows = {}; curves = {}
    bh = {"A_买入持有_BTC": ["BTCUSDT"], "B_买入持有_ETH": ["ETHUSDT"], "E基准_买入持有_BTC+ETH(50/50)": ["BTCUSDT", "ETHUSDT"]}
    fee = 0.0 if research else float(c["spot"]["costs"]["sensitivity"]["fee_bps"])
    for k, syms in bh.items():
        eq = buy_and_hold(ctxs, syms, start, end, initial, fee_bps=fee)
        curves[k] = eq
        rows[k] = {"equity": equity_stats(eq, initial), "trades": {"n_trades": 1 if len(syms) == 1 else 2}, "util": {"time_in_market_share": 1.0, "avg_exposure": 1.0}, "yearly": yearly(eq), "regime": regime_returns(eq, regime_series)}
    usdt = pd.DataFrame({"ts": curves["A_买入持有_BTC"]["ts"], "equity": initial})
    curves["F_全程_USDT"] = usdt; rows["F_全程_USDT"] = {"equity": equity_stats(usdt, initial) | {"max_drawdown": 0.0, "total_return": 0.0}, "trades": {"n_trades": 0}, "util": {"time_in_market_share": 0.0, "avg_exposure": 0.0}, "yearly": yearly(usdt), "regime": {}}
    for k, syms in CONFIGS.items():
        t0 = time.time()
        res = SpotBacktester(c, syms, exec_tf, start, end, research_costs=research).run(ctxs)
        tag = f"{name}_{'zero' if research else 'cost'}_{k}"
        res.equity.to_csv(out_dir / f"{tag}_equity.csv", index=False); res.trades.to_csv(out_dir / f"{tag}_trades.csv", index=False); res.fills.to_csv(out_dir / f"{tag}_fills.csv", index=False)
        res.decisions.to_csv(out_dir / f"{tag}_decisions.csv", index=False)
        es = equity_stats(res.equity, initial); ts = trade_stats(res.trades); ut = utilization(res.equity)
        bench = "A_买入持有_BTC" if syms == ["BTCUSDT"] else ("B_买入持有_ETH" if syms == ["ETHUSDT"] else "E基准_买入持有_BTC+ETH(50/50)")
        r_arr = res.trades["r_multiple"].dropna().to_numpy(float) if not res.trades.empty else np.array([])
        rows[k] = {"equity": es, "trades": ts, "util": ut, "yearly": yearly(res.equity), "regime": regime_returns(res.equity, regime_series), "excess_vs_bh": (es.get("total_return") or 0) - (rows[bench]["equity"].get("total_return") or 0), "bench": bench,
                   "final": res.final, "counters": res.counters, "decisions": res.decisions["decision"].value_counts().to_dict(), "bootstrap_mean_r_95ci": block_bootstrap_ci(r_arr) if len(r_arr) else {},
                   "stop_atr_median": float(res.decisions.loc[res.decisions["decision"] == "BUY", "diag_stop_atr"].median()) if "diag_stop_atr" in res.decisions and (res.decisions["decision"] == "BUY").any() else None,
                   "buy_rejected_reasons": res.decisions.loc[res.decisions["reasons"].str.contains("被拒绝", na=False), "reasons"].str.extract(r"被拒绝：(.*)")[0].str.split("；").str[0].value_counts().head(6).to_dict(), "elapsed_s": round(time.time() - t0, 1)}
        curves[k] = res.equity[["ts", "equity"]]
        if write_charts:
            for s in syms:
                plot_trades_html(ctxs[s], res.trades, res.fills, out_dir / f"{tag}_{s}_trades.html", s, start, end)
        print(f"[phase4] {name} {'zero' if research else 'cost'} {k}: ret={es.get('total_return')} dd={es.get('max_drawdown')} n={ts.get('n_trades')} PF={ts.get('profit_factor')} ExpR={ts.get('expectancy_r')} bench={rows[bench]['equity'].get('total_return')} ({rows[k]['elapsed_s']}s)", flush=True)
    en = {"A_买入持有_BTC": "A BuyHold BTC", "B_买入持有_ETH": "B BuyHold ETH", "E基准_买入持有_BTC+ETH(50/50)": "BuyHold BTC+ETH 50/50", "F_全程_USDT": "F USDT", "C_策略_BTC": "C Strategy BTC", "D_策略_ETH": "D Strategy ETH", "E_策略_BTC+ETH": "E Strategy BTC+ETH"}
    chart = plot_equity({en.get(k, k): v for k, v in curves.items()}, out_dir / f"{name}_{'zero' if research else 'cost'}_equity.png", f"{name} ({'zero cost' if research else '10bps fee + 5bps slip'}): strategy vs buy&hold vs USDT, start {initial:.0f} USDT") if write_charts else None
    return {"rows": rows, "chart": chart, "period": [str(start), str(end)]}


def latest_signal(c: dict, ctxs: dict, exec_tf: str, initial: float, warm_bars: int = 400) -> dict:
    """最新一根已收盘 K 线的 BUY/HOLD/SELL 与理由（假设从 warm_bars 前空仓起跑以还原策略状态与持仓）。"""
    out = {}
    for s, mc in ctxs.items():
        start = mc.ts.iloc[max(1, mc.n - warm_bars)]
        res = SpotBacktester(c, [s], exec_tf, str(start), None).run({s: mc})
        d = res.decisions.iloc[-1].to_dict() if not res.decisions.empty else {}
        out[s] = {"ts": str(d.get("ts")), "price": d.get("price"), "decision": d.get("decision"), "strategy": d.get("strategy"), "reasons": d.get("reasons"), "regime4": d.get("regime4"), "trend_4h": d.get("trend_4h"), "local_res": d.get("local_res"), "local_sup": d.get("local_sup"), "near_htf_support": d.get("near_htf_support"),
                  "data_last_close": str(mc.ts.iloc[-1] + pd.Timedelta(seconds=mc.bar_seconds)), "holding_qty_sim": d.get("holding_qty"), "cash_sim": d.get("cash"), "note": "持仓/现金为自 warm_bars 前空仓起跑的模拟状态，不是真实账户"}
    return out


def run_phase4(c: dict | None = None, out_dir: Path | None = None, charts: bool = True) -> dict:
    c = c or cfg(); sc = c["spot"]
    out_dir = out_dir or (report_dir(c) / "phase4_spot"); out_dir.mkdir(parents=True, exist_ok=True)
    initial = float(sc["initial_cash_usdt"]); exec_tf = sc["exec_tf"]
    ctxs = load_contexts(sc["symbols"], exec_tf, c)
    reg = _regime_series(ctxs["BTCUSDT"])
    log = {"exec_tf": exec_tf, "initial_cash": initial, "windows": {}, "notes": []}
    t0 = time.time()
    for w, (start, end) in sc["windows"].items():
        if w == "full_4h":
            continue
        log["windows"][w] = {}
        for research in (True, False):
            log["windows"][w]["zero_cost" if research else "with_cost"] = run_window(c, ctxs, exec_tf, w, start, end, research, out_dir, initial, reg, write_charts=charts and research)
    # 4h 长历史描述性对照（跨牛熊；不作判定）
    try:
        s4, e4 = sc["windows"]["full_4h"]
        ctx4 = load_contexts(sc["symbols"], "4h", c); reg4 = _regime_series(ctx4["BTCUSDT"])
        log["windows"]["full_4h"] = {"zero_cost": run_window(c, ctx4, "4h", "full_4h", s4, e4, True, out_dir, initial, reg4, write_charts=charts), "with_cost": run_window(c, ctx4, "4h", "full_4h", s4, e4, False, out_dir, initial, reg4, write_charts=False)}
    except Exception as e:  # noqa: BLE001
        log["notes"].append(f"4h 对照失败：{e}")
    log["latest_signal"] = latest_signal(c, ctxs, exec_tf, initial)
    log["elapsed_s"] = round(time.time() - t0, 1)
    atomic_write_json(out_dir / "phase4_results.json", log, indent=2, default=str)
    atomic_write_text(out_dir / "phase4_results.md", to_markdown(log))
    atomic_write_json(out_dir / "latest_signal.json", log["latest_signal"], indent=2, default=str)
    return log


def to_markdown(log: dict) -> str:
    L = [f"# 第四阶段纯现货回测（执行周期 {log['exec_tf']}，初始 {log['initial_cash']} USDT）", ""]
    for w, modes in log["windows"].items():
        for mode, r in modes.items():
            L += [f"## {w} · {'零成本（理想研究）' if mode == 'zero_cost' else '含成本 10 bps + 5 bps'} · {r['period'][0][:10]} → {r['period'][1][:10] if r['period'][1] != 'None' else '最新'}", "", compare_table(r["rows"]), ""]
            for k, row in r["rows"].items():
                if k.startswith(("C_", "D_", "E_策略")):
                    ts = row["trades"]
                    L.append(f"- **{k}**：按策略 {json.dumps(ts.get('by_strategy'), ensure_ascii=False)}；退出 {ts.get('by_exit')}；环境 {json.dumps({kk: round(v['expectancy_r'], 2) for kk, v in (ts.get('by_regime4') or {}).items()}, ensure_ascii=False)}；年度 {json.dumps({kk: round(v, 3) for kk, v in row['yearly'].items()})}；止损/ATR 中位 {row.get('stop_atr_median')}；均值 R CI {row.get('bootstrap_mean_r_95ci')}；买入被拒 {row.get('buy_rejected_reasons')}")
            L.append("")
    L += ["## 最新信号", "", json.dumps(log.get("latest_signal"), ensure_ascii=False, indent=1, default=str), ""]
    return "\n".join(L)
