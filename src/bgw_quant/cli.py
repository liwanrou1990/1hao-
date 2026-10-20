"""命令行入口：bgwq <command>。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import pandas as pd



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
    for s in pl.ingest(args.symbols, start, end):
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
        print("阻塞：未配置 BGW_API_KEY/BGW_API_SECRET，无法核对 SPCX 等标的的资产性质/发行商/合约。")
        sys.exit(2)
    for sym in args.symbols or ["SPCX"]:
        cs = c.contracts_for(sym)
        info = c.stock_info(ticker=sym)
        _p({"ticker": sym, "contracts": [x.__dict__ for x in cs], "stock_info": info})


def _day(s):
    return date.fromisoformat(s) if s else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bgwq", description="Bitget Wallet 美股代币化 AI 量化复盘系统（V1 研究版）")
    ap.add_argument("--cost-profile", default="token_dex")
    ap.add_argument("--capital", type=float, default=10_000)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="探测 API 权限/历史跨度/频率/成本"); p.add_argument("--mode", default="auto"); p.set_defaults(f=cmd_probe)
    p = sub.add_parser("ingest", help="接入历史分钟K线"); p.add_argument("--symbols", nargs="*"); p.add_argument("--days", type=int, default=70); p.add_argument("--end"); p.set_defaults(f=cmd_ingest)
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
    p = sub.add_parser("tiers", help="查看本金档位及问题标记"); p.set_defaults(f=cmd_tiers)
    p = sub.add_parser("verify-assets", help="官方接口核对资产性质/发行商/合约"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_verify_assets)
    p = sub.add_parser("serve", help="启动 FastAPI 工作台"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.set_defaults(f=cmd_serve)
    args = ap.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
