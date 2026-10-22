"""命令行入口：bgwq <command>。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .calendar_utils import previous_trading_day



def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_probe(args):
    from .data.registry import build_data_plan
    plan = build_data_plan(force_mode=args.mode, do_probe=True)
    print(plan.banner())
    for p in plan.probes:
        _p(p.__dict__)
    if plan.blockers:
        print("\n阻塞项：")
        for b in plan.blockers:
            print(" -", b)


def _pipeline(args):
    from .pipeline import Pipeline
    return Pipeline(cost_profile=args.cost_profile, capital_usd=args.capital, probe=True)


def cmd_ingest(args):
    from .pipeline import default_window
    pl = _pipeline(args)
    start, end = default_window(args.days, _day(args.end))
    print(pl.plan.banner())
    for s in pl.ingest(args.symbols, start, end, periods=tuple(args.periods)):
        print(f"{s.symbol:5s} {s.venue:6s} 请求 {s.days_requested} 天 / 已有 {s.days_ingested} 天，新增 {s.bars} 根，来源 {s.source}")
    if pl.plan.blockers:
        print("阻塞项：", *pl.plan.blockers[:5], sep="\n - ")


def cmd_replay(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    ab = pl.replay_day(args.symbol, day)
    print(pl.plan.banner())
    print(ab.summary_table().to_string())
    print("\n".join(" - " + g for g in ab.gap_explanation))
    print(f"图表：reports_out/charts/{args.symbol}_{day}_{pl.cost_profile_name}.html")


def cmd_backtest(args):
    pl = _pipeline(args)
    res = pl.backtest(args.symbol, args.train_days, args.test_days)
    print(pl.plan.banner())
    rows = [{"strategy": k, **{m: v["oos_metrics"].get(m) for m in ["mean_daily_net_return", "total_net_return", "sharpe", "max_drawdown", "trades_per_day", "cost_pct_of_capital_per_day"]},
             "in_sample_mean_daily": v["in_sample_vs_oos"]["in_sample_mean_daily"]} for k, v in res["strategies"].items()]
    print(pd.DataFrame(rows).to_string())
    print("基准：", {k: round(v.get("mean_daily_net_return", 0), 5) for k, v in res["benchmarks"].items()})
    print(pl.tier_attainment(args.symbol)[["tier", "target_daily_pct", "n_days", "attained_days", "attainment_rate", "mean_daily_ret_pct", "status"]].to_string())


def cmd_train_ml(args):
    pl = _pipeline(args)
    wf = pl.train_ml(args.symbol, args.train_days, args.test_days)
    print(pl.plan.banner())
    print(wf.summary().to_string())
    print(wf.note)


def cmd_report(args):
    from .reports.daily import generate_daily_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    out = generate_daily_report(pl, day, args.symbols, with_ml=not args.no_ml, with_backtest=not args.no_backtest)
    _p(out)


def cmd_optimal(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    rows = pl.hindsight_tiers(args.symbol, day)
    from .hindsight.optimal_path import DISCLAIMER
    print(DISCLAIMER)
    for row in rows:
        r = row["result"]
        print(f"{row['tier']:3s} {row['point']:3s} 本金 {row['capital_usd']:>12,.0f}  目标 {row['target_daily_pct'][0]:.2f}%  "
              f"最高可实现 {r.max_net_return:+.3%} ({r.max_net_profit_usd:,.2f} USD, {r.n_trades} 笔)  "
              f"{'达标 最少'+str(r.min_trades_for_target)+'笔' if r.target_met else '未达标'}  {'不可验证' if r.unverifiable else ''}")
    mid = [row for row in rows if row["point"] == "mid"]
    if mid:
        r = mid[0]["result"]
        print(f"\n{mid[0]['tier']} mid 最高收益路径：")
        for i, t in enumerate(r.trades, 1):
            print(f"  {i}. {t.buy_ts[11:16]} 买 {t.buy_exec:.3f} → {t.sell_ts[11:16]} 卖 {t.sell_exec:.3f}  投入 {t.size_usd:,.0f} 数量 {t.qty:.4f} 成本 {t.cost_usd:.2f} 净盈亏 {t.net_pnl_usd:+.2f} ({t.net_ret:+.3%})")


def cmd_optimal_report(args):
    from .reports.hindsight_report import generate_hindsight_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_hindsight_report(pl, day, args.symbols, with_portfolio=not args.no_portfolio))


def cmd_target(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    caps = [args.capital_usd] if args.capital_usd else [c["capital_usd"] for c in pl.target_capitals()]
    for cap in caps:
        try:
            r = pl.target_teaching(args.symbol, day, cap, args.target_pct)
        except (LookupError, ValueError) as e:
            print(f"本金 {cap:,.0f}: {e}")
            continue
        print(f"\n[教学·事后] {args.symbol} {day} 本金 {cap:,.0f} 目标 {r.target_return:.3%}={r.target_usd:,.2f} USD → 已实现 {r.realized_net_usd:,.2f} "
              f"({r.realized_net_return:+.3%}) {r.n_buys} 买/{r.n_sells} 卖 {'达标@'+r.target_hit_ts[11:16] if r.target_met else '未达标'}"
              f"{'（不可验证）' if r.unverifiable else ''}")
        for n in r.reasons_not_met + r.notes:
            print("   -", n)
        for t in r.trades:
            print(f"   {t.seq:2d}. {t.entry.fills[0].ts_ny[11:16]} 买{len(t.entry.fills)}批@{t.avg_entry:.3f} → {t.exit.fills[-1].ts_ny[11:16]} 卖{len(t.exit.fills)}批@{t.avg_exit:.3f} "
                  f"投入 {t.size_usd:,.0f} 净 {t.net_pnl_usd:+.2f} ({t.net_ret:+.3%}) 累计 {t.cum_net_pnl_usd:,.2f} 最大不利 {t.mae_pct:.2f}%")
        s = pl.target_strategy_day(args.symbol, day, cap, target_pct=args.target_pct)
        print(f"[策略·无未来信息] 同日回放：净 {s.realized_net_usd:,.2f} ({s.realized_net_return:+.3%}) {len(s.trades)} 笔 "
              f"{'达标' if s.target_met else '未达标'} {s.stopped_reason or ''}")


def cmd_target_backtest(args):
    pl = _pipeline(args)
    print(pl.plan.banner())
    caps = [args.capital_usd] if args.capital_usd else pl.target_cfg.validation_capitals
    for cap in caps:
        wf = pl.target_walkforward(args.symbol, cap, args.target_pct, args.train_days, args.test_days)
        st = wf.oos_stats
        print(f"\n{args.symbol} 本金 {cap:,.0f} 目标 {wf.target_return:.3%} · 样本外 {st.n_days} 天 / {len(wf.folds)} 折")
        print(f"  达标率 {st.attainment_rate:.0%} 亏损日 {st.losing_day_rate:.0%} 日均 {st.mean_daily_return:+.3%} 标准差 {st.std_daily_return:.3%} "
              f"最大回撤 {st.max_drawdown_pct:.2%} 最差日 {st.worst_day_return:+.2%} 笔/日 {st.avg_trades_per_day:.1f} 部分成交天 {st.partial_fill_days} 不可验证天 {st.unverifiable_days}")
        print(f"  样本内均值(对照)：{wf.in_sample_stats_mean}")
        print(wf.oos_frame().to_string(index=False))


def cmd_target_report(args):
    from .reports.target_report import generate_target_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_target_report(pl, day, args.symbols, with_walkforward=not args.no_walkforward, wf_symbols=args.wf_symbols))


def cmd_simple(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    from .simple_sim.engine import DISCLAIMER
    print(DISCLAIMER)
    results = [pl.simple_day(args.symbol, day, args.capital_usd, args.target_usd)] if args.capital_usd else pl.simple_tiers(args.symbol, day)
    for r in results:
        print(f"\n{r.symbol} {r.day} 档位 {r.tier} 本金 {r.capital_usd:,.0f} 目标利润 {r.target_usd:,.2f} → 实际净利润 {r.realized_net_usd:,.2f} "
              f"({r.realized_net_return:+.3%}) 买 {r.n_buys} 卖 {r.n_sells} 多 {r.n_long} 空 {r.n_short} 资金利用率 {r.capital_utilization:.0%} "
              f"{'达标@'+r.target_hit_ts[11:16] if r.target_met else '未达标'}{'（不可验证）' if r.unverifiable else ''}")
        for n in r.notes:
            print("   -", n)
        for t in r.trades:
            print(f"   {t.seq:2d}. {'多' if t.direction=='long' else '空'} {t.open_fill.fills[0].ts_ny[11:16]}@{t.avg_open:.3f} → {t.close_fill.fills[-1].ts_ny[11:16]}@{t.avg_close:.3f} "
                  f"投入 {t.size_usd:,.0f} 交易成本 {t.trading_cost_usd:.2f} 资金成本 {t.capital_cost_usd:.2f} 净 {t.net_pnl_usd:+.2f} 累计 {t.cum_net_pnl_usd:,.2f}")


def cmd_simple_report(args):
    from .reports.simple_report import generate_simple_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_simple_report(pl, day, args.symbols))


def cmd_compare(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    for c in pl.compare_tiers(args.symbol, day):
        th, ex, fa = c["theoretical"], c["executable"], c["analysis"]
        print(f"\n{args.symbol} {c['tier']} 本金 {c['capital_usd']:,.0f} 目标 {c['target_usd']:,.0f} | A理论 {th.max_net_profit_usd:,.0f} ({th.n_trades}笔{'，达标' if th.target_met else '，未达标'}) "
              f"| B可执行 {ex.realized_net_usd:,.2f} ({ex.realized_net_return:+.3%}) {ex.n_trades}笔 多{ex.n_long}/空{ex.n_short} 并行{ex.params.max_open} 单笔计划 {fa['tranche_usd']:,.0f} "
              f"利用率峰{ex.capital_utilization:.0%}/均{ex.avg_utilization:.0%} {'达标@'+ex.target_hit_ts[11:16] if ex.target_met else '未达标'}")
        for r in fa["reasons"]:
            print("   -", r)
        if args.trades:
            for t in ex.trades:
                print(f"   {t.seq:2d}. {'多' if t.direction=='long' else '空'} {t.open_fill.fills[0].ts_ny[11:16]}@{t.avg_open:.3f} → {t.close_fill.fills[-1].ts_ny[11:16]}@{t.avg_close:.3f} 投入 {t.size_usd:,.0f} 净 {t.net_pnl_usd:+.2f} 累计 {t.cum_net_pnl_usd:,.2f}")


def cmd_compare_report(args):
    from .reports.compare_report import generate_compare_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_compare_report(pl, day, args.symbols, wf_symbols=args.wf_symbols, wf_min_capital=args.wf_min_capital))


def cmd_engine(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    cap = pl.capability(args.symbol)
    print(f"品种能力 {args.symbol}: 方向={cap.directions} 杠杆={cap.leverage_long}x API核实={cap.verified_by_api} | " + "；".join(cap.notes))
    for c in pl.engine_tiers(args.symbol, day):
        r, th, cls, opt = c["run"], c["theoretical"], c["classification"], c["optimization"]
        oos = f"OOS {opt.oos_stats.n_days}天 达标率 {opt.oos_stats.attainment_rate:.0%} 日均 {opt.oos_stats.mean_daily_return:+.3%} 回撤 {opt.oos_stats.max_drawdown_pct:.2%}" if opt else "OOS 历史不足"
        print(f"\n{c['tier']} 本金 {c['capital_usd']:,.0f} 目标 {c['target_usd']:,.0f} | 实际 {r.realized_net_usd:,.2f} ({r.realized_net_return:+.3%}) 交易 {r.n_trades} 多/空 {r.n_long}/{r.n_short} "
              f"胜率 {'—' if r.win_rate is None else f'{r.win_rate:.0%}'} 回撤 {r.max_drawdown_usd:,.0f} 利用率 {r.avg_deployed_usd / c['capital_usd']:.0%} 执行成本 {r.execution_cost_usd:,.2f} 资金成本 {r.funding_cost_usd:.2f} "
              f"{'达标' if r.target_met else '未达标'} | A理论 {th.max_net_profit_usd:,.0f} | 参数 {r.params.to_dict()} | {oos}")
        print("   原因:", "、".join(cls["tags"]) or "—", "|", "；".join(cls["details"]))
        if args.trades:
            for t in r.trades:
                print(f"   {t.seq:2d}. {'多' if t.direction=='long' else '空'} {t.mode} {t.open_fill.fills[0].ts_ny[11:16]}@{t.avg_open:.3f} → {t.close_fill.fills[-1].ts_ny[11:16]}@{t.avg_close:.3f} 投入 {t.size_usd:,.0f} 净 {t.net_pnl_usd:+.2f} {t.reason_exit}")


def cmd_engine_report(args):
    from .reports.engine_report import generate_engine_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_engine_report(pl, day, args.symbols))


def cmd_matrix_report(args):
    from .reports.matrix_report import generate_matrix_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_matrix_report(pl, day, args.symbols))


def cmd_feasibility(args):
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day(args.symbol)
    print(pl.plan.banner())
    for r in pl.feasibility_tiers(args.symbol, day):
        print(f"\n{r.tier} 本金 {r.capital_usd:,.0f} 目标 {r.target_usd:,.0f} | A理论最大 {r.theoretical_net_usd:,.0f} ({r.theoretical_trades}笔{'，达标' if r.theoretical_met else '，未达标'}) "
              f"| B可实现最大 {r.executable_net_usd:,.2f} ({r.executable_return:+.3%}) 资金片{r.n_tranches} {r.n_trades}笔 多{r.n_long}/空{r.n_short} "
              f"{'达标 需'+str(r.trades_to_target)+'笔@'+r.target_hit_ts[11:16] if r.executable_met else '未达标'} 峰值占用 {r.peak_deployed_usd / r.capital_usd:.0%} 流动性 {r.liquidity_source}")
        for lim in r.limits:
            print("   -", lim)
        if args.trades:
            for i, t in enumerate(r.trades, 1):
                print(f"   {i:2d}. 片{t.tranche} {'多' if t.direction=='long' else '空'} {t.open_ts[11:16]}@{t.open_exec:.3f} → {t.close_ts[11:16]}@{t.close_exec:.3f} 投入 {t.size_usd:,.0f} 净 {t.net_pnl_usd:+.2f} 累计 {t.cum_net_pnl_usd:,.2f}")


def cmd_key_audit(args):
    """密钥审计：泄露扫描 + 未知值字段识别（输入只来自环境变量；输出不含任何密钥）。"""
    from .data.key_audit import run_key_audit
    rep = run_key_audit()
    if args.out:
        import json as _json
        Path(args.out).write_text(_json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _p(rep)


def cmd_rotation(args):
    """单日：六类策略 × 七档的因果轮动结果（C），可选同时输出 A/B 对照。"""
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    specs = {s.key: s for s in pl.rotation_specs()}
    keys = args.strategies or list(specs)
    rows = []
    for key in keys:
        spec = specs[key]
        for t in pl.tiers.tiers:
            cap = t.representative_capitals(("mid",))["mid"]
            lo, hi = t.target_daily_pct
            tgt = (lo + hi) / 2 / 100 * cap
            r = pl.rotation_day(spec, day, cap, tgt)
            row = {"strategy": key, "tier": t.id, "capital_usd": cap, "target_usd": tgt, "C_net_usd": round(r.realized_net_usd, 2),
                   "C_met": r.target_met, "n_trades": r.n_trades, "n_switches": r.n_switches, "path": "→".join(r.path), "stop": r.stop_reason,
                   "flat_ratio": round(r.flat_ratio, 3), "simulated": r.data_is_simulated}
            if args.ab:
                from .rotation.hindsight import universe_ab
                assets = [a for a in (pl.asset_day(s, day) for s in spec.universe) if a is not None]
                ab = universe_ab(assets, spec, cap, tgt, str(day), pl.participation_rate)
                row.update({"A_theoretical_usd": round(ab.theoretical_net_usd, 2), "B_executable_usd": round(ab.executable_net_usd, 2), "B_alloc": ab.executable_alloc})
            rows.append(row)
            if args.trades:
                for tr in r.trades:
                    _p({k: tr.to_dict()[k] for k in ("seq", "symbol", "direction", "mode", "open_ts", "close_ts", "size_usd", "net_pnl_usd", "reason_exit")})
    _p(rows)


def cmd_rotation_backtest(args):
    """多日时间顺序样本外回测（C）+ A/B 对照 + 排名；结果缓存可续跑。"""
    from .rotation.backtest import RotationBacktest
    pl = _pipeline(args)
    end = date.fromisoformat(args.end) if args.end else pl.latest_day()
    bt = RotationBacktest(pl, end_day=end, test_days=args.test_days, train_days=args.train_days, workers=args.workers, cache_path=args.cache)
    info = bt.prepare()
    print(f"数据：{info['n_assets_loaded']} 资产日；缺失 {len(info['missing'])}；日期 {info['days'][0]}…{info['days'][-1]}", flush=True)
    if not args.skip_c:
        n = bt.run_c(progress=lambda d, n: print(f"C 运行 {d}/{n}", flush=True))
        print(f"C 新增运行 {n}", flush=True)
    if not args.skip_ab:
        n = bt.run_ab(progress=lambda d, n: print(f"A/B 运行 {d}/{n}", flush=True))
        print(f"A/B 新增运行 {n}", flush=True)
    oos = bt.oos_table()
    rk = bt.ranking(oos)
    out_dir = pl.settings.resolve_path(pl.settings.report_dir) / "rotation"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{bt.test_days[0]}_{bt.test_days[-1]}_{pl.cost_profile_name}"
    oos.to_csv(out_dir / f"rotation_oos_{tag}.csv", index=False)
    rk.to_csv(out_dir / f"rotation_ranking_{tag}.csv", index=False)
    bt.trades_frame(oos).to_csv(out_dir / f"rotation_trades_{tag}.csv", index=False)
    _p({"oos_rows": len(oos), "ranking_rows": len(rk), "files": [str(out_dir / f"rotation_{x}_{tag}.csv") for x in ("oos", "ranking", "trades")],
        "top_by_tier": rk[rk["rank_in_tier"] == 1][["tier", "strategy", "attainment", "mean_net_pct", "losing_day_rate", "score"]].to_dict("records") if not rk.empty else []})


def cmd_rotation_report(args):
    from .reports.rotation_report import generate_rotation_report
    pl = _pipeline(args)
    end = date.fromisoformat(args.end) if args.end else pl.latest_day()
    _p(generate_rotation_report(pl, end, args.test_days, args.train_days, workers=args.workers, cache_path=args.cache))


def cmd_feasibility_report(args):
    from .reports.feasibility_report import generate_feasibility_report
    pl = _pipeline(args)
    day = _day(args.day) or pl.latest_day()
    _p(generate_feasibility_report(pl, day, args.symbols))


def cmd_tiers(args):
    from .capital.tiers import load_tiers
    cfg = load_tiers()
    _p(cfg.to_dict())


def cmd_serve(args):
    import uvicorn
    uvicorn.run("bgw_quant.api.main:app", host=args.host, port=args.port, reload=False)


def cmd_verify_assets(args):
    """用官方 stockList/stockInfo 核对标的资产性质、发行商与合约；无密钥时明确阻塞。"""
    from .data.bitget_rwa import BitgetRwaClient
    c = BitgetRwaClient()
    if not c.has_credentials:
        print(f"阻塞：BGW_API_KEY={'已配置' if c.api_key else '缺失'}，BGW_API_SECRET={'已配置' if c.api_secret else '缺失'}；官方签名需要 Secret。")
        sys.exit(2)
    _p(c.verify_assets(args.symbols or ["NVDA", "SPCX", "TSLA"]))


def cmd_verify_matrix(args):
    """逐个品种核实：产品类型、状态、链/合约、现货/做空/杠杆支持、最小/最大订单；无法核实的标为“未验证”，不猜测。"""
    import json as _json
    from pathlib import Path
    from .config import PROJECT_ROOT, research_symbols
    from .data.bitget_rwa import BitgetRwaClient
    from .decision.capability import capability_for
    c = BitgetRwaClient()
    syms = args.symbols or [x["ticker"] for x in research_symbols()]
    api = {}
    err = None
    if c.has_credentials:
        try:
            api = {a["ticker"]: a for a in c.verify_assets(syms)}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        err = "缺少 BGW_API_KEY/BGW_API_SECRET"
    rows = []
    for sym in syms:
        cap = capability_for(sym, api.get(sym))
        rows.append({"ticker": sym, "verification_status": cap.verification_status, "spot_long": cap.spot_long, "short": cap.short,
                     "leverage": cap.leverage_long, "order_types": cap.order_types, "chain": cap.chain, "contract": cap.contract,
                     "data_source": cap.data_source, "market_status": cap.market_status, "tx_min_buy_usd": cap.tx_min_buy_usd,
                     "tx_max_buy_usd": cap.tx_max_buy_usd, "tx_max_sell_usd": cap.tx_max_sell_usd, "notes": cap.notes,
                     "api_raw": api.get(sym)})
    rep = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "api_error": err, "rows": rows,
           "note": "官方 RWA 接口仅链上现货 swap（市价）；做空/杠杆需人工登记可核实渠道。api_error 非空时全部为“未验证”。"}
    out = Path(args.out) if args.out else PROJECT_ROOT / "docs" / "asset_verification_latest.json"
    out.write_text(_json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for r in rows:
        print(f"{r['ticker']:5s} {r['verification_status']:11s} 现货多={r['spot_long']} 做空={r['short']} 杠杆={r['leverage']}x 链/合约={r['chain']}/{(r['contract'] or '—')[:10]} 状态={r['market_status']} 单笔买入上限={r['tx_max_buy_usd']}")
    if err:
        print("API 错误：", err)
    print(f"报告已写入 {out}")


def cmd_net_diag(args):
    """网络/DNS/TLS/代理/时钟/签名/限频诊断与接口归属确认（不输出密钥）。"""
    import json as _json
    from pathlib import Path
    from .config import PROJECT_ROOT
    from .data.net_diag import run_diagnostics
    rep = run_diagnostics()
    out = Path(args.out) if args.out else PROJECT_ROOT / "docs" / "net_diag_latest.json"
    out.write_text(_json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _p({k: v for k, v in rep.items() if k != "api_family"})
    print(f"报告已写入 {out}")
    sys.exit(0 if not rep["blocked_domains"] else 3)


def cmd_live_test(args):
    """API 连接测试：权限 → stockList/stockInfo 核对 → 1m/5m/15m K线分页获取；输出 JSON 报告并写入 docs/。"""
    import json as _json
    from pathlib import Path
    from .config import PROJECT_ROOT
    from .data.bitget_rwa import BitgetRwaClient
    c = BitgetRwaClient()
    day = _day(args.day) or previous_trading_day(date.today())
    rep = c.live_test(args.symbols or ["NVDA", "SPCX", "TSLA"], day, tuple(args.periods))
    # 网络层错误也要捕获
    rep["conclusion"] = ("连接测试通过" if rep.get("klines") and all(k.get("ok") for k in rep["klines"]) and not rep["errors"]
                         else "连接测试未通过：" + "；".join(rep["errors"][:5] or ["无K线返回"]))
    out = Path(args.out) if args.out else PROJECT_ROOT / "docs" / f"api_live_test_{day}.json"
    out.write_text(_json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _p({k: v for k, v in rep.items() if k != "calls"})
    print(f"报告已写入 {out}")
    sys.exit(0 if rep["conclusion"].startswith("连接测试通过") else 3)


def _day(s):
    return date.fromisoformat(s) if s else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bgwq", description="Bitget Wallet 美股代币化 AI 量化复盘系统（V1 研究版）")
    ap.add_argument("--cost-profile", default="token_dex")
    ap.add_argument("--capital", type=float, default=10_000)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="探测 API 权限/历史跨度/频率/成本"); p.add_argument("--mode", default="auto"); p.set_defaults(f=cmd_probe)
    p = sub.add_parser("ingest", help="接入历史K线（live：官方 kline 多周期分页）"); p.add_argument("--symbols", nargs="*"); p.add_argument("--days", type=int, default=70); p.add_argument("--end"); p.add_argument("--periods", nargs="*", default=["1m"]); p.set_defaults(f=cmd_ingest)
    p = sub.add_parser("verify-matrix", help="逐个品种核实产品类型/状态/链合约/方向支持/订单限制（无法核实=未验证）"); p.add_argument("--symbols", nargs="*"); p.add_argument("--out"); p.set_defaults(f=cmd_verify_matrix)
    p = sub.add_parser("net-diag", help="网络/DNS/TLS/代理/时钟/限频诊断与接口归属确认"); p.add_argument("--out"); p.set_defaults(f=cmd_net_diag)
    p = sub.add_parser("live-test", help="Bitget RWA API 连接测试（只读）"); p.add_argument("--symbols", nargs="*"); p.add_argument("--day"); p.add_argument("--periods", nargs="*", default=["1m", "5m", "15m"]); p.add_argument("--out"); p.set_defaults(f=cmd_live_test)
    p = sub.add_parser("replay", help="双模式复盘"); p.add_argument("symbol"); p.add_argument("--day"); p.set_defaults(f=cmd_replay)
    p = sub.add_parser("backtest", help="规则策略 walk-forward"); p.add_argument("symbol"); p.add_argument("--train-days", type=int, default=20); p.add_argument("--test-days", type=int, default=5); p.set_defaults(f=cmd_backtest)
    p = sub.add_parser("train-ml", help="LightGBM walk-forward"); p.add_argument("symbol"); p.add_argument("--train-days", type=int, default=40); p.add_argument("--test-days", type=int, default=5); p.set_defaults(f=cmd_train_ml)
    p = sub.add_parser("report", help="生成日报"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.add_argument("--no-ml", action="store_true"); p.add_argument("--no-backtest", action="store_true"); p.set_defaults(f=cmd_report)
    p = sub.add_parser("optimal", help="事后最优路径：七档本金 × 最高收益/最少交易达标路径"); p.add_argument("symbol"); p.add_argument("--day"); p.set_defaults(f=cmd_optimal)
    p = sub.add_parser("optimal-report", help="生成事后最优日报（全部标的×七档+组合）"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.add_argument("--no-portfolio", action="store_true"); p.set_defaults(f=cmd_optimal_report)
    p = sub.add_parser("target", help="固定收益目标·多次交易：教学路径 + 同日策略回放"); p.add_argument("symbol"); p.add_argument("--day"); p.add_argument("--capital-usd", type=float); p.add_argument("--target-pct", type=float); p.set_defaults(f=cmd_target)
    p = sub.add_parser("target-backtest", help="固定收益目标策略滚动样本外验证"); p.add_argument("symbol"); p.add_argument("--capital-usd", type=float); p.add_argument("--target-pct", type=float); p.add_argument("--train-days", type=int, default=20); p.add_argument("--test-days", type=int, default=5); p.set_defaults(f=cmd_target_backtest)
    p = sub.add_parser("target-report", help="固定收益目标日报（教学 + 样本外策略）"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.add_argument("--wf-symbols", nargs="*"); p.add_argument("--no-walkforward", action="store_true"); p.set_defaults(f=cmd_target_report)
    p = sub.add_parser("simple", help="简化版：昨日真实走势多空多次交易，累计净利润达档位目标即停"); p.add_argument("symbol"); p.add_argument("--day"); p.add_argument("--capital-usd", type=float); p.add_argument("--target-usd", type=float); p.set_defaults(f=cmd_simple)
    p = sub.add_parser("simple-report", help="简单报告：各标的×档位 本金/目标/实际/次数/利用率/达标 + K线买卖点"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_simple_report)
    p = sub.add_parser("compare", help="档位对比：A理论最优 vs B多笔可执行 + 失败原因"); p.add_argument("symbol"); p.add_argument("--day"); p.add_argument("--trades", action="store_true"); p.set_defaults(f=cmd_compare)
    p = sub.add_parser("compare-report", help="各档位对比报告（A/B/C）+ CSV + K线图"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.add_argument("--wf-symbols", nargs="*"); p.add_argument("--wf-min-capital", type=float, default=175000); p.set_defaults(f=cmd_compare_report)
    p = sub.add_parser("engine", help="统一收益决策引擎：七档昨日因果复盘 + 滚动样本外 + 原因归类"); p.add_argument("symbol"); p.add_argument("--day"); p.add_argument("--trades", action="store_true"); p.set_defaults(f=cmd_engine)
    p = sub.add_parser("engine-report", help="决策引擎报告（默认 NVDA SPCX）"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_engine_report)
    p = sub.add_parser("matrix-report", help="70 组测试矩阵：A/B/C + 能力核实 + 原因 + 建议"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_matrix_report)
    p = sub.add_parser("feasibility", help="历史可行性：A 理论最大 / B 执行约束下最大可实现 / 达标所需笔数 / 完整买卖点"); p.add_argument("symbol"); p.add_argument("--day"); p.add_argument("--trades", action="store_true"); p.set_defaults(f=cmd_feasibility)
    p = sub.add_parser("feasibility-report", help="历史可行性报告（全标的 × 七档）+ CSV + 图表"); p.add_argument("--day"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_feasibility_report)
    p = sub.add_parser("key-audit", help="密钥审计：泄露扫描、未知 32 位值字段识别（BGWQ_AUDIT_VALUE 环境变量输入，不输出密钥）"); p.add_argument("--out"); p.set_defaults(f=cmd_key_audit)
    p = sub.add_parser("rotation", help="跨市场轮动（因果 C）：六类策略 × 七档 单日结果，--ab 同时输出事后 A/B 对照"); p.add_argument("--day"); p.add_argument("--strategies", nargs="*"); p.add_argument("--ab", action="store_true"); p.add_argument("--trades", action="store_true"); p.set_defaults(f=cmd_rotation)
    p = sub.add_parser("rotation-backtest", help="多日时间顺序样本外回测 + A/B 对照 + 六类策略排名（可续跑）"); p.add_argument("--end"); p.add_argument("--test-days", type=int); p.add_argument("--train-days", type=int); p.add_argument("--workers", type=int, default=4); p.add_argument("--skip-c", action="store_true"); p.add_argument("--skip-ab", action="store_true"); p.add_argument("--cache", help="结果缓存 DuckDB 路径（默认 data_store/rotation_cache.duckdb）"); p.set_defaults(f=cmd_rotation_backtest)
    p = sub.add_parser("rotation-report", help="跨市场轮动综合报告（排名、A/B/C、切换路径、逐笔 CSV、图表）"); p.add_argument("--end"); p.add_argument("--test-days", type=int); p.add_argument("--train-days", type=int); p.add_argument("--workers", type=int, default=4); p.add_argument("--cache"); p.set_defaults(f=cmd_rotation_report)
    p = sub.add_parser("tiers", help="查看本金档位及问题标记"); p.set_defaults(f=cmd_tiers)
    p = sub.add_parser("verify-assets", help="官方接口核对资产性质/发行商/合约"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_verify_assets)
    p = sub.add_parser("serve", help="启动 FastAPI 工作台"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.set_defaults(f=cmd_serve)
    args = ap.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
