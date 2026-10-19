"""命令行入口：bgwq <command>。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

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
    p = sub.add_parser("tiers", help="查看本金档位及问题标记"); p.set_defaults(f=cmd_tiers)
    p = sub.add_parser("verify-assets", help="官方接口核对资产性质/发行商/合约"); p.add_argument("--symbols", nargs="*"); p.set_defaults(f=cmd_verify_assets)
    p = sub.add_parser("serve", help="启动 FastAPI 工作台"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.set_defaults(f=cmd_serve)
    args = ap.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
