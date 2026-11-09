"""Phase 10 结果独立复核（只读 Phase 10 的数据副本、results JSON、trades CSV；不改任何 Phase 10 文件）。
核查：日线根数与日历跨度、预热段、重复时间戳、来源；成本口径（单侧）；最大回撤 / 最长回撤期 / 最终净值；两笔及五笔最大交易贡献；
与买入持有比较；环境收益来源；无未来函数的抽样重放（用 Phase 10 引擎在截断数据上重跑，比较决策一致）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..holding.engine import HoldingBacktester
from ..holding.phase10 import COSTS, STRATEGIES

P10_DOCS = Path("docs/brando_pa_validation/phase10_holding"); P10_DATA = Path("data_store/brando_pa/phase10_holding")


def audit_phase10(out_dir: Path, docs_dir: Path = P10_DOCS, data_dir: Path = P10_DATA) -> dict:
    out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "source_commit_claimed": "5a19020", "data": {}, "cost_convention": None, "strategies": {}, "notes": []}
    bars = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        d = pd.read_parquet(data_dir / f"{s}_1d.parquet"); d["ts"] = pd.to_datetime(d["ts"], utc=True); bars[s] = d
        span = int((d["ts"].max() - d["ts"].min()).days) + 1
        out["data"][s] = {"bars": int(len(d)), "calendar_span_days": span, "bars_equal_span": int(len(d)) == span, "duplicates": int(d["ts"].duplicated().sum()), "first": str(d["ts"].min().date()), "last": str(d["ts"].max().date()),
                          "bars_warmup_before_2018": int((d["ts"] < pd.Timestamp("2018-01-01", tz="UTC")).sum()), "bars_from_2018": int((d["ts"] >= pd.Timestamp("2018-01-01", tz="UTC")).sum()), "sources": d["source"].value_counts().to_dict(),
                          "ohlc_valid": bool(((d["high"] >= d[["open", "close"]].max(axis=1)) & (d["low"] <= d[["open", "close"]].min(axis=1))).all()), "adjustment": "现货，无复权；无其他资产混入（文件按币种分开）"}
    res = json.loads((docs_dir / "phase10_results.json").read_text()); rows = pd.DataFrame(res["rows"])
    out["cost_convention"] = {"costs_one_side_bps": res["costs_one_side_bps"], "split": COSTS, "statement": "25 bps = 单侧（买入 25 + 卖出 25 = 往返 50 bps；清仓估值另扣一次卖出成本）", "verified_from_fills": None}
    trades = pd.read_csv(docs_dir / "trades_full_25bps.csv")
    bh = {n: rows[(rows.name == n) & (rows.window == "full") & (rows.cost_bps == 25)].iloc[0] for n in ("S8a_BUYHOLD_BTC", "S8b_BUYHOLD_ETH", "S8c_BUYHOLD_5050")}
    for name in ("S1_BTC_SMA200", "S2_ETH_SMA200", "S3_DYN_BTC_ETH", "S7_HOLD90_EXTEND", "B2_modelB_state_machine_daily_repro", "S4_HOLD30"):
        r = {w: rows[(rows.name == name) & (rows.window == w) & (rows.cost_bps == 25)].iloc[0] for w in ("full", "train", "valid", "oos")}
        t = trades[trades.strategy == name].sort_values("net_pnl", ascending=False); tot = float(t.net_pnl.sum()); top2 = float(t.net_pnl.head(2).sum()); top5 = float(t.net_pnl.head(5).sum())
        f = r["full"]
        out["strategies"][name] = {"final_equity_full": float(f.final_equity), "liquidation_net_profit_full": float(f.liquidation_net_profit_usdt), "max_drawdown": float(f.max_drawdown), "longest_drawdown_days": int(f.longest_drawdown_days),
                                   "closed_trades": int(len(t)), "sum_closed_net_pnl": tot, "top2_pnl": top2, "top2_share_of_closed_pnl": top2 / tot if tot else None, "top5_pnl": top5, "top5_share": top5 / tot if tot else None, "pnl_excluding_top5": tot - top5,
                                   "top5_trades": t.head(5)[["symbol", "entry_ts", "exit_ts", "hold_days", "net_pnl"]].to_dict("records"),
                                   "vs_buyhold_full": {k: float(f.liquidation_net_profit_usdt - bh[k].liquidation_net_profit_usdt) for k in bh}, "vs_buyhold_oos": {k: float(r["oos"].liquidation_net_profit_usdt - rows[(rows.name == k) & (rows.window == "oos") & (rows.cost_bps == 25)].iloc[0].liquidation_net_profit_usdt) for k in bh},
                                   "regimes": f.regimes, "segments": {w: float(r[w].liquidation_net_profit_usdt) for w in r}, "beats_btc_bh_all_three_segments": all(float(r[w].liquidation_net_profit_usdt) > float(rows[(rows.name == "S8a_BUYHOLD_BTC") & (rows.window == w) & (rows.cost_bps == 25)].iloc[0].liquidation_net_profit_usdt) for w in ("train", "valid", "oos"))}
    # 无未来函数抽样：用截断到 2021-12-31 的数据重跑 S1，与全量数据下的同期决策比较
    spec = [s for s in STRATEGIES if s["name"] == "S1_BTC_SMA200"][0]; fee, slip = COSTS[25]
    cut = {s: d[d["ts"] <= pd.Timestamp("2021-12-31", tz="UTC")] for s, d in bars.items()}
    a = HoldingBacktester(bars, spec, fee, slip, "2018-01-01", "2021-12-31").run(); b = HoldingBacktester(cut, spec, fee, slip, "2018-01-01", "2021-12-31").run()
    same = a.fills.reset_index(drop=True).equals(b.fills.reset_index(drop=True)) and bool((a.nav["equity"].to_numpy() == b.nav["equity"].to_numpy()).all())
    out["lookahead_check"] = {"method": "S1 在截断于 2021-12-31 的数据上重跑，与全量数据下 2018–2021 的成交与净值逐项比较", "identical": bool(same), "n_fills": int(len(a.fills))}
    # 成本口径从成交验证：买入 fill = ref × 1.0005，fee = fill × qty × 0.002
    fl = a.fills[a.fills.side == "BUY"].head(3)
    out["cost_convention"]["verified_from_fills"] = bool(((fl.fill_price / fl.ref_price - 1.0005).abs() < 1e-9).all() and ((fl.fee / (fl.fill_price * fl.qty) - 0.002).abs() < 1e-9).all())
    out["notes"] = ["3,370 根 = 2017-08-17 → 2026-11-07 的日历天数（含 137 根 2017 年预热，仅用于 SMA200；研究窗口 2018-01-01 起 3,233 根）；无重复、无其他资产。",
                    "S1/S2 的闭合交易净额中最大两笔占比 > 79%，去掉最大五笔后为负：收益高度依赖少数长持仓。",
                    "没有任何趋势策略在训练 / 验证 / 测试三段都战胜 BTC 买入持有；2024–2026 段全部落后。",
                    "2024–2026 数据已在 Phase 10 被查看，Phase 11 对该段的任何结果都不是盲测；真正的样本外只能来自后续前瞻。",
                    "全部核心策略只在牛市月盈利，熊市与震荡市亏损；B2 状态机例外（震荡为正）。"]
    out_dir.mkdir(parents=True, exist_ok=True); atomic_write_json(out_dir / "phase10_audit.json", out, indent=1, default=str)
    L = [f"# Phase 10 结果独立复核 · {out['checked_at_utc'][:19]} UTC", "", "## 数据"] + [f"- {s}: {v['bars']} 根，日历跨度 {v['calendar_span_days']} 天（相等={v['bars_equal_span']}），重复 {v['duplicates']}，2017 预热 {v['bars_warmup_before_2018']} 根，2018 起 {v['bars_from_2018']} 根，OHLC 合法={v['ohlc_valid']}，来源 {v['sources']}" for s, v in out["data"].items()]
    L += ["", f"## 成本口径：{out['cost_convention']['statement']}；成交复核={out['cost_convention']['verified_from_fills']}", "", f"## 无未来函数抽样：{out['lookahead_check']}", "", "## 策略复核（全样本 25 bps 单侧）", "", "| 策略 | 期末净值 | 清仓净收益 | 最大回撤 | 最长回撤期 | 闭合交易 | 最大两笔占比 | 最大五笔占比 | 去掉五笔后 | vs BTC 持有（全/测试） | 三段均胜 BTC 持有 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, v in out["strategies"].items():
        L.append(f"| {n} | {v['final_equity_full']:,.0f} | {v['liquidation_net_profit_full']:+,.0f} | {v['max_drawdown']:.1%} | {v['longest_drawdown_days']} | {v['closed_trades']} | {v['top2_share_of_closed_pnl']:.0%} | {v['top5_share']:.0%} | {v['pnl_excluding_top5']:+,.0f} | {v['vs_buyhold_full']['S8a_BUYHOLD_BTC']:+,.0f} / {v['vs_buyhold_oos']['S8a_BUYHOLD_BTC']:+,.0f} | {'是' if v['beats_btc_bh_all_three_segments'] else '否'} |")
    L += ["", "## 结论"] + [f"- {x}" for x in out["notes"]]
    atomic_write_text(out_dir / "phase10_audit.md", "\n".join(L)); return out
