"""跨市场动态轮动综合报告：排名、六类策略族对比、各档位适用范围与失败原因、最优候选逐日明细、K 线路径图、CSV。"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..costs.model import profile_for_asset
from .charts import bars_to_candles, chart_time_seconds, ensure_vendor_js
from .daily import _num, _pct

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["pct"] = _pct
_env.filters["num"] = _num

FAMILY_DESC = {"A": "固定单只股票（逐只评估）", "A*": "固定单只股票·事前按前几日表现选股", "B": "固定单种加密货币（逐币评估）",
               "B*": "固定单种加密货币·事前选币", "C": "股票之间动态轮动", "D": "加密货币之间动态轮动", "E": "股票×加密跨市场动态轮动", "F": "多资产组合并行（均分）"}


def _clean(row: dict) -> dict:
    """NaN → None，避免模板把缺失值当字符串/数字处理。"""
    return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}


def _markers(trades: list[dict], bars: pd.DataFrame, symbol: str) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t in trades:
        if t["symbol"] != symbol or not t.get("open_children"):
            continue
        oi, ci = int(t["open_children"][0]["idx"]), int(t["close_children"][-1]["idx"]) if t.get("close_children") else int(t["open_children"][-1]["idx"])
        oi, ci = min(oi, len(secs) - 1), min(ci, len(secs) - 1)
        long = t["direction"] == "long"
        out.append({"time": int(secs[oi]), "position": "belowBar" if long else "aboveBar", "color": "#2962FF" if long else "#8E24AA",
                    "shape": "arrowUp" if long else "arrowDown", "text": f"{'买' if long else '空'}{t['seq']} {t['avg_open']:.2f}×{t['size_usd']:,.0f}"})
        out.append({"time": int(secs[ci]), "position": "aboveBar" if long else "belowBar", "color": "#2e7d32" if t["net_pnl_usd"] >= 0 else "#D50000",
                    "shape": "arrowDown" if long else "arrowUp", "text": f"{'卖' if long else '平'}{t['seq']} 净{t['net_pnl_usd']:+,.0f} {t['reason_exit'][:6]}"})
    return sorted(out, key=lambda m: m["time"])


def render_rotation_chart(pipeline, summary: dict, trades: list[dict], name: str, out_path: Path) -> None:
    from ..rotation.universe import day_grid_utc

    day = date.fromisoformat(summary["day"])
    assets = {}
    for sym in summary["universe"]:
        a = pipeline.asset_day(sym, day)
        if a is None:
            continue
        n_tr = sum(t["symbol"] == sym for t in trades)
        if n_tr == 0 and sym not in (summary.get("path") or []):
            continue
        assets[sym] = {"candles": bars_to_candles(a.bars), "markers": _markers(trades, a.bars, sym), "n_trades": n_tr, "source": a.source}
    grid = day_grid_utc(day).tz_convert("America/New_York")
    epoch = pd.Timestamp("1970-01-01")
    secs = ((grid.tz_localize(None) - epoch) // pd.Timedelta(seconds=1)).to_numpy()
    curve = summary.get("curve")
    if not curve:                                   # 缓存不含分钟曲线：按平仓时间从逐笔累计重建（阶梯）
        g0 = grid[0].tz_localize(None)
        step = np.zeros(len(grid))
        for t in sorted(trades, key=lambda x: x["close_ts"]):
            k = int((pd.Timestamp(t["close_ts"]) - g0) // pd.Timedelta(minutes=1))
            if 0 <= k < len(step):
                step[k:] = t["cum_net_pnl_usd"]
        curve = step.tolist()
    cum = [{"time": int(s), "value": float(v)} for s, v in zip(secs, curve)]
    kpis = [("C 净收益", f"{summary['realized_net_usd']:,.0f} USD ({summary['realized_net_return']*100:.3f}%)"), ("目标", f"{summary['target_usd']:,.0f}"),
            ("达标", "是 @ " + summary["target_hit_ts"][11:16] if summary.get("target_hit_ts") else "否"), ("笔数 多/空", f"{summary['n_trades']} ({summary['n_long']}/{summary['n_short']})"),
            ("切换次数", summary["n_switches"]), ("路径", "→".join(summary.get("path") or [])[:80] or "空仓"), ("日内回撤", f"{summary['max_drawdown_usd']:,.0f}"),
            ("峰值占用", f"{summary['peak_deployed_usd']/summary['capital_usd']*100:.0f}%"), ("空仓时间", f"{summary['flat_ratio']*100:.0f}%"),
            ("执行成本", f"{summary['execution_cost_usd']:,.0f}"), ("达标后交易/盈亏", f"{summary['post_target_trades']} / {summary['post_target_net_usd']:,.0f}"),
            ("停止原因", summary.get("stop_reason") or "—")]
    html = _env.get_template("rotation_chart.html.j2").render(strategy=summary["strategy_key"], name=name, tier=summary.get("tier", ""), day=summary["day"],
                                                             simulated=summary["data_is_simulated"], kpis=kpis, switches=summary.get("switches") or [],
                                                             trades=trades, assets_json=json.dumps(assets), cum_json=json.dumps(cum))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_vendor_js(out_path.parent)
    out_path.write_text(html, encoding="utf-8")


def generate_rotation_report(pipeline, end_day: date, test_days: int | None = None, train_days: int | None = None, workers: int = 4,
                             run_missing: bool = True, out_dir: Path | None = None, cache_path: str | None = None, specs=None) -> dict:
    from ..rotation.backtest import RotationBacktest

    bt = RotationBacktest(pipeline, end_day=end_day, test_days=test_days, train_days=train_days, workers=workers, cache_path=cache_path, specs=specs)
    bt.prepare()
    if run_missing:
        bt.run_c()
        bt.run_ab()
    oos = bt.oos_table()
    rk = bt.ranking(oos)
    out_dir = out_dir or (pipeline.settings.resolve_path(pipeline.settings.report_dir) / "rotation")
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = pipeline.settings.resolve_path(pipeline.settings.report_dir) / "charts"
    tag = f"{bt.test_days[0]}_{bt.test_days[-1]}_{pipeline.cost_profile_name}"
    files = {"oos": f"rotation_oos_{tag}.csv", "ranking": f"rotation_ranking_{tag}.csv", "trades": f"rotation_trades_{tag}.csv"}
    oos.to_csv(out_dir / files["oos"], index=False)
    rk.to_csv(out_dir / files["ranking"], index=False)
    bt.trades_frame(oos).to_csv(out_dir / files["trades"], index=False)
    if oos.empty:
        return {"html": None, "note": "无结果"}
    # 排名表（每档位前 8）
    ranking_by_tier = {}
    for tier, g in rk.groupby("tier", sort=False):
        ranking_by_tier[tier] = [_clean(r) for r in g.sort_values("rank_in_tier").head(8).to_dict("records")]
    # 策略族
    fam_rows = []
    for fam, g in oos.groupby("family"):
        fam_rows.append({"family": fam, "desc": FAMILY_DESC.get(fam, ""), "attainment": float(g["C_met"].mean()), "mean_net_pct": float(g["C_net_pct"].mean()),
                         "losing_day_rate": float((g["C_net_usd"] < 0).mean()), "mean_trades": float(g["n_trades"].mean()), "mean_switches": float(g["n_switches"].mean()),
                         "A_attainment": float(pd.to_numeric(g["A_met"], errors="coerce").fillna(0).mean()), "B_attainment": float(pd.to_numeric(g["B_met"], errors="coerce").fillna(0).mean()), "n": int(len(g))})
    fam_rows.sort(key=lambda r: -r["attainment"])
    # 档位
    tier_rows, details = [], []
    tier_order = [t[0] for t in bt.tiers]
    for tier in tier_order:
        g = oos[oos["tier"] == tier]
        if g.empty:
            continue
        best = rk[(rk["tier"] == tier) & (rk["rank_in_tier"] == 1)].iloc[0]
        reasons = g["reason"].str.split("：").str[0].value_counts()
        cap = float(g["capital_usd"].iloc[0]); tgt = float(g["target_usd"].iloc[0])
        a_att = float(pd.to_numeric(g["A_met"], errors="coerce").fillna(0).mean()); b_att = float(pd.to_numeric(g["B_met"], errors="coerce").fillna(0).mean())
        scope = []
        if b_att < 0.5:
            scope.append(f"本金 {cap:,.0f}：流动性/成本约束下即使事后最优也只有 {b_att:.0%} 的天数可达 {tgt:,.0f}，目标对该档位偏高")
        if a_att < 1:
            scope.append(f"{1 - a_att:.0%} 的档位×天连理论上限都低于目标（行情不足）")
        if best["attainment"] == 0:
            scope.append("因果模型无一天达标：现有信号无预测优势，不应据此交易")
        elif best["attainment"] < 0.5:
            scope.append("因果模型达标率低于 50%，稳定性不足")
        tier_rows.append({"tier": tier, "best": best["name"], "attainment": float(best["attainment"]), "A_attainment": a_att, "B_attainment": b_att,
                          "reasons": "；".join(f"{k}×{v}" for k, v in reasons.items()), "scope": "；".join(scope) or "该档位有达标记录，仍需真实行情验证"})
        rows = [_clean(r) for r in g[g["strategy"] == best["strategy"]].sort_values("day").to_dict("records")]
        for r in rows:
            r["chart"] = None
            if not str(r["strategy"]).startswith(("A*", "B*")):
                try:
                    summ, trades = bt.run_detail(r["strategy"], tier, r["day"], json.loads(r["params"]))
                    summ["tier"] = tier
                    fn = f"rotation_{r['strategy'].replace(':', '_')}_{tier}_{r['day']}_{pipeline.cost_profile_name}.html"
                    render_rotation_chart(pipeline, summ, trades, best["name"], charts_dir / fn)
                    r["chart"] = f"../charts/{fn}"
                except LookupError:
                    pass
        details.append({"tier": tier, "name": best["name"], "rows": rows})
    st = pipeline.status()
    stocks = pipeline.symbols(tradable_only=True); cryptos = pipeline.crypto_symbols(tradable_only=True)
    html = _env.get_template("rotation_report.html.j2").render(
        period=f"{bt.test_days[0]} ~ {bt.test_days[-1]}", generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        cost_profile=pipeline.cost_profile_name, crypto_profile=profile_for_asset(pipeline.cost_profile_name, "crypto"), n_test_days=len(bt.test_days),
        train_days=bt.train_n, stocks=stocks, cryptos=cryptos, formal=not pipeline.plan.is_simulated and pipeline.plan.crypto_mode == "live",
        banner=pipeline.plan.banner(), ranking_by_tier=ranking_by_tier, family_rows=fam_rows, tier_rows=tier_rows, details=details,
        latency=pipeline.engine_cfg.execution.get("latency_bars", 1), fill_rate=pipeline.engine_cfg.execution.get("fill_rate", 0.95),
        participation=f"{float(pipeline.engine_cfg.execution.get('participation_rate', 0.1)):.0%}",
        data_note=("模拟行情（SIMULATED），流动性来源为 K 线成交额；真实行情接入后将以官方逐笔成交为流动性来源" if pipeline.plan.is_simulated else "官方行情"),
        files=files)
    out = out_dir / f"rotation_report_{tag}.html"
    out.write_text(html, encoding="utf-8")
    best_rows = rk[rk["rank_in_tier"] == 1][["tier", "strategy", "name", "attainment", "mean_net_pct", "losing_day_rate", "multi_day_dd_usd", "score"]]
    return {"html": str(out), "csv": {k: str(out_dir / v) for k, v in files.items()}, "n_oos_rows": int(len(oos)), "test_days": [str(d) for d in bt.test_days],
            "best_by_tier": best_rows.to_dict("records"), "family_summary": fam_rows, "formal_validation": not pipeline.plan.is_simulated}
