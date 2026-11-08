"""命令行：python -m brando_pa.cli <command>

  ingest        采集/更新 K 线（1w/1d/4h/1h/15m）
  status        数据状态
  analyze       当前多周期分析：关键区域、4h 结构、日线环境（每个标的）
  signal        最新信号报告（WAIT/LONG/SHORT/EXIT/NO_TRADE + 理由）→ reports_out/brando_pa/signals/latest.json
  backtest      回测矩阵（全部策略 / 单策略 × 过滤开/关）
  walkforward   Walk-Forward 验证（每个策略独立）
  paper         模拟盘：--once 单轮 / --loop 持续 / --replay 用历史 K 线回放整个流程
  dashboard     启动 Streamlit 看板
  export        导出最近回测/模拟盘为 CSV/JSON/HTML
真实下单默认禁止；本 CLI 不包含任何真实下单路径（见 paper/broker.py 的 LiveBroker 闸门）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _p(o):
    print(json.dumps(o, ensure_ascii=False, indent=2, default=str))


def cmd_ingest(a):
    from .data.ingest import run_ingest
    st = run_ingest(a.symbols, a.timeframes)
    for s, v in st["symbols"].items():
        for tf, e in v.items():
            cr = e["continuity"]
            print(f"{s} {tf}: {e['result']} {cr['n_bars']} 根 {str(cr['first'])[:10]} → {str(cr['last'])[:16]} 缺口 {cr['n_gap_bars']} 根；{e['freshness']}")
    print("探测：", st["probe"])


def cmd_status(a):
    from .data.ingest import load_status
    _p(load_status() or {"error": "尚未 ingest"})


def cmd_analyze(a):
    from .config import cfg
    from .backtest.runner import load_contexts
    c = cfg(); exec_tf = a.exec_tf or c["timeframes"]["execution"]
    ctxs = load_contexts(a.symbols or c["symbols"], exec_tf, c)
    out = {}
    for s, mc in ctxs.items():
        i = mc.n - 1
        zones, by_tf = mc.zones_at(i)
        price = float(mc.cl[i])
        out[s] = {"as_of_bar_close": (mc.ts.iloc[i] + __import__("pandas").Timedelta(seconds=mc.bar_seconds)).isoformat(), "price": price, "regime_1d": mc.regime_at(i), "structure_4h": mc.structure_dict(i),
                  "zones": [z.to_dict() | {"role": z.role(price)} for z in zones], "zones_by_tf": {tf: len(v) for tf, v in by_tf.items()}}
    _p(out)


def cmd_signal(a):
    from .engine.live import latest_signals
    rep = latest_signals(a.symbols, a.exec_tf, equity=a.equity)
    for s, r in rep["symbols"].items():
        print(f"{s} @ {r['ts']} 价格 {r['price']:.2f} → {r['status']}；日线环境 {r['trend_1d'].get('trend')}；4h 结构 {r['structure_4h'].get('trend')}；数据 {'正常' if r['data_ok'] else '过期'}")
        for line in r["reasons"][:6]:
            print("   -", line)
    print("已写入", rep["path"])


def cmd_backtest(a):
    from .config import cfg
    from .backtest.runner import run_matrix
    c = cfg()
    variants = None
    if a.strategy or a.no_filters is not None:
        base = a.strategy or "all"
        variants = [f"{base}_{'nofilters' if a.no_filters else 'filters'}"]
    out = run_matrix(a.symbols or c["symbols"], a.exec_tf or c["timeframes"]["execution"], a.start, a.end, a.name, c, variants)
    print(open(Path(out["variants"][list(out["variants"])[0]]["paths"]["summary_json"]).parent / "compare.md", encoding="utf-8").read())


def cmd_walkforward(a):
    from .config import cfg, report_dir
    from .backtest.runner import load_contexts
    from .backtest.walkforward import run_walkforward, wf_to_dict
    from spot90.atomic import atomic_write_json
    c = cfg(); exec_tf = a.exec_tf or c["timeframes"]["execution"]
    for k in ("train_months", "valid_months", "test_months"):
        if getattr(a, k) is not None:
            c["backtest"]["walkforward"][k] = int(getattr(a, k))
    if a.set:
        import yaml as _yaml
        from .backtest.walkforward import set_path
        for kv in a.set:
            k, v = kv.split("=", 1); set_path(c, k, _yaml.safe_load(v))
    ctxs = load_contexts(a.symbols or c["symbols"], exec_tf, c)
    strategies = a.strategy.split(",") if a.strategy else [k for k, v in c["strategies"].items() if isinstance(v, dict) and v.get("enabled", True)]
    out_dir = report_dir(c) / "walkforward" / a.name; out_dir.mkdir(parents=True, exist_ok=True)
    allres = {}
    for sk in strategies:
        r = run_walkforward(ctxs, exec_tf, sk, c, not a.no_filters, a.start, a.end, a.workers, perturb=not a.no_perturb, regime_gating=a.regime_gating)
        d = wf_to_dict(r); allres[sk] = d
        if not r.oos_trades.empty:
            r.oos_trades.to_csv(out_dir / f"{sk}_oos_trades.csv", index=False)
        if r.default_full_trades is not None and not r.default_full_trades.empty:
            r.default_full_trades.to_csv(out_dir / f"{sk}_default_full_trades.csv", index=False)
        print(f"{sk}: 折数 {len(r.folds)} OOS 交易 {r.oos_stats.get('n_trades', 0)} ExpR {r.oos_stats.get('expectancy_r')} PF {r.oos_stats.get('profit_factor')} 净收益 {r.oos_stats.get('net_return')} 回撤 {r.oos_stats.get('max_drawdown')} CI {r.oos_stats.get('bootstrap_mean_r_95ci')} | 默认参数全程 {r.default_full_stats} | 扰动 {r.perturbation.get('summary')}")
        for f in r.folds:
            print(f"   fold{f.fold} test {f.test[0]}→{f.test[1]} 参数 {f.chosen_params}{'（退回默认）' if f.fallback_to_default else ''} → {f.test_stats}")
    atomic_write_json(out_dir / "walkforward.json", {"exec_tf": exec_tf, "symbols": a.symbols or c["symbols"], "overrides": a.set or [], "regime_gating": bool(a.regime_gating), "windows": {k: c["backtest"]["walkforward"][k] for k in ("train_months", "valid_months", "test_months", "embargo_days")}, "results": allres}, indent=2, default=str)
    print("已写入", out_dir / "walkforward.json")


def cmd_experiments(a):
    from .config import cfg
    from .backtest.runner import load_contexts
    from .backtest.experiments import run_experiments, phase2_variants
    c = cfg(); exec_tf = a.exec_tf or c["timeframes"]["execution"]
    ctxs = load_contexts(a.symbols or c["symbols"], exec_tf, c)
    variants = None
    if a.only:
        allv = phase2_variants([k for k, v in c["strategies"].items() if isinstance(v, dict) and v.get("enabled", True)])
        variants = {k: v for k, v in allv.items() if any(tok in k for tok in a.only.split(","))}
    if a.set:
        import yaml as _yaml
        from .backtest.walkforward import set_path
        extra = {}
        for kv in a.set:
            k, v = kv.split("=", 1); set_path(extra, k, _yaml.safe_load(v))
        from .backtest.experiments import _merge
        variants = {f"{k}__set_{'_'.join(x.split('=')[0].split('.')[-1] + str(x.split('=')[1]) for x in a.set)}": {**v, "override": _merge(v.get("override") or {}, extra), "label": v.get("label", "") + f" / 覆盖 {a.set}"} for k, v in (variants or allv_default(c)).items()}
    out = run_experiments(ctxs, exec_tf, a.name, a.start, a.end, variants, c, a.workers)
    print("已写入", Path(out["variants"][list(out["variants"])[0]]["paths"]["summary_json"]).parent / "experiments.md")


def allv_default(c):
    from .backtest.experiments import phase2_variants
    return phase2_variants([k for k, v in c["strategies"].items() if isinstance(v, dict) and v.get("enabled", True)])


def cmd_audit(a):
    from .audit.data_quality import run_audit
    r = run_audit(a.symbols)
    print(open(r["paths"]["md"], encoding="utf-8").read())


def cmd_evaluate(a):
    from .config import cfg, report_dir
    from .evaluation.evaluator import evaluate_walkforward_dir
    c = cfg(); rd = report_dir(c) / "walkforward"
    names = a.names.split(",") if a.names else ["h1_p2", "m15_p2", "h4_p2"]
    paths = {}
    for n in names:
        tf = n.split("_")[0].replace("h1", "1h").replace("m15", "15m").replace("h4", "4h")
        paths[n] = (tf, rd / n / "walkforward.json")
    out = evaluate_walkforward_dir(paths, c)
    print(open(out["paths"]["md"], encoding="utf-8").read())


def cmd_phase3(a):
    from .backtest.phase3 import run_phase3
    log = run_phase3(a.symbols, exec_tf=a.exec_tf or "1h")
    from .config import report_dir
    print(open(report_dir() / "phase3" / "phase3_results.md", encoding="utf-8").read())


def cmd_phase4(a):
    from .spot.phase4 import run_phase4
    from .config import report_dir
    run_phase4(charts=not a.no_charts)
    print(open(report_dir() / "phase4_spot" / "phase4_results.md", encoding="utf-8").read()[:6000])


def cmd_spot_backtest(a):
    from .config import cfg
    from .backtest.runner import load_contexts
    from .spot.engine import SpotBacktester
    from .spot.report import equity_stats, trade_stats, utilization
    c = cfg(); syms = a.symbols or c["spot"]["symbols"]; tf = a.exec_tf or c["spot"]["exec_tf"]
    res = SpotBacktester(c, syms, tf, a.start, a.end, research_costs=not a.with_cost).run(load_contexts(syms, tf, c))
    _p({"final": res.final, "equity": equity_stats(res.equity, c["spot"]["initial_cash_usdt"]), "trades": trade_stats(res.trades), "util": utilization(res.equity), "decisions": res.decisions["decision"].value_counts().to_dict(), "notes": res.notes})


def cmd_spot_signal(a):
    from .config import cfg, report_dir
    from .backtest.runner import load_contexts
    from .spot.phase4 import latest_signal
    from spot90.atomic import atomic_write_json
    c = cfg(); syms = a.symbols or c["spot"]["symbols"]; tf = a.exec_tf or c["spot"]["exec_tf"]
    out = latest_signal(c, load_contexts(syms, tf, c), tf, c["spot"]["initial_cash_usdt"])
    od = report_dir(c) / "phase4_spot"; od.mkdir(parents=True, exist_ok=True); atomic_write_json(od / "latest_signal.json", out, indent=2, default=str)
    _p(out)


def cmd_phase5(a):
    from .trend.phase5 import run_phase5
    from .config import cfg, report_dir
    c = cfg()
    if a.set:
        import yaml as _yaml
        from .backtest.walkforward import set_path
        for kv in a.set:
            k, v = kv.split("=", 1); set_path(c, k, _yaml.safe_load(v))
    od = report_dir(c) / ("phase5_spot" + (f"_{a.out_suffix}" if a.out_suffix else ""))
    run_phase5(c, od, workers=a.workers, windows=a.windows)
    print(open(od / "phase5_results.md", encoding="utf-8").read()[:3000])


def cmd_trend_signal(a):
    from .trend.phase5 import latest_trend_signal
    _p(latest_trend_signal())


def cmd_paper(a):
    from .paper.runner import PaperRunner
    r = PaperRunner(symbols=a.symbols, exec_tf=a.exec_tf, equity=a.equity)
    if a.replay:
        _p(r.replay(a.replay_bars))
    elif a.loop:
        r.loop()
    else:
        _p(r.run_once())


def cmd_phase6(a):
    from .trendsys.phase6 import run_phase6
    from .config import cfg, report_dir
    c = cfg()
    if a.set:
        import yaml as _yaml
        from .backtest.walkforward import set_path
        for kv in a.set:
            k, v = kv.split("=", 1); set_path(c, k, _yaml.safe_load(v))
    od = report_dir(c) / ("phase6_spot" + (f"_{a.out_suffix}" if a.out_suffix else ""))
    log = run_phase6(c, od, workers=a.workers, windows=a.windows)
    _p(log.get("acceptance"))


def cmd_sma200_audit(a):
    from .backtest.runner import load_contexts
    from .config import cfg
    from .trendsys.audit import audit_symbol
    c = cfg(); tc = c["trendsys"]; ctxs = load_contexts(tc["symbols"], "1h", c)
    for s, mc in ctxs.items():
        r = audit_symbol(mc, c, a.start or tc["windows"]["full_1h"][0], a.end, float(tc["initial_cash_usdt"]), a.fee, a.slip)
        _p({k: v for k, v in r.items() if k not in ("equity_phase5", "fills_phase5", "trendsys_result", "hold_segments")} | {"hold_segments": {k: v for k, v in r.get("hold_segments", {}).items() if k != "segments"}})


def cmd_trendsys_signal(a):
    from .trendsys.phase6 import latest_trendsys_signal
    _p(latest_trendsys_signal())


def cmd_trendsys_paper(a):
    from .trendsys.paper import TrendSysPaper
    from pathlib import Path as _P
    r = TrendSysPaper(log_dir=_P(a.log_dir) if a.log_dir else None, strategy=a.strategy, risk_mode=a.risk_mode)
    if a.replay:
        _p(r.replay(a.start, a.end))
    elif a.verify:
        _p(r.verify_all())
    else:
        _p(r.run_once())


def cmd_phase7(a):
    from .usdt.phase7 import run_phase7
    from .config import cfg, report_dir
    c = cfg()
    if a.set:
        import yaml as _yaml
        from .backtest.walkforward import set_path
        for kv in a.set:
            k, v = kv.split("=", 1); set_path(c, k, _yaml.safe_load(v))
    od = report_dir(c) / ("phase7_usdt" + (f"_{a.out_suffix}" if a.out_suffix else ""))
    log = run_phase7(c, od, workers=a.workers, windows=a.windows)
    print(open(od / "phase7_results.md", encoding="utf-8").read()[:4000])


def cmd_usdt_signal(a):
    from .usdt.phase7 import latest_allocation
    _p(latest_allocation())


def cmd_usdt_paper(a):
    from .usdt.paper import UsdtPaper
    from pathlib import Path as _P
    r = UsdtPaper(log_dir=_P(a.log_dir) if a.log_dir else None, model=a.model, risk_mode=a.risk_mode)
    if a.replay:
        _p(r.replay(a.start, a.end))
    elif a.verify:
        _p(r.verify_all())
    else:
        _p(r.run_once())


def cmd_forward_data(a):
    from .forward.datafeed import MarketDataService
    svc = MarketDataService()
    syms = ["BTCUSDT", "ETHUSDT"]; tfs = ["1h", "1d"]
    if a.loop:
        svc.run_loop(syms, tfs, interval_s=a.interval, max_iter=a.max_iter)
    else:
        st = svc.update(syms, tfs); _p({"ok": st.ok, "source": st.source, "stale": st.stale, "last_success_utc": st.last_success_utc, "per_tf": {k: {kk: v[kk] for kk in ("last_closed_bar_close_utc", "lag_hours", "stale", "n_fetched")} for k, v in st.per_tf.items()}, "consistency": st.consistency, "reference_check": st.reference_check, "errors": st.errors[-5:]})


def cmd_forward_run(a):
    import time as _t
    from .forward.accounts import ForwardRunner
    from .forward.datafeed import MarketDataService
    from pathlib import Path as _P
    r = ForwardRunner(log_dir=_P(a.log_dir) if a.log_dir else None)
    def once():
        if not a.no_update:
            st = MarketDataService().update(["BTCUSDT", "ETHUSDT"], ["1h", "1d"]); print(f"[forward] data ok={st.ok} source={st.source} stale={st.stale}", flush=True)
        out = r.run_once(); print(f"[forward] {out['now']} data_ok={out['data_check']['ok']} " + " | ".join(f"{n}: cost {v['nav']['cost']['equity_usdt']:.0f} ideal {v['nav']['ideal']['equity_usdt']:.0f} {v['risk_state']} fills {len(v['fills'])}" for n, v in out.get("accounts", {}).items()), flush=True)
        return out
    if a.loop:
        while True:
            try:
                once()
            except Exception as e:  # noqa: BLE001
                print(f"[forward] error {type(e).__name__}: {e}", flush=True)
            _t.sleep(a.interval)
    else:
        _p(once())


def cmd_forward_status(a):
    from .forward.accounts import ForwardRunner
    from pathlib import Path as _P
    r = ForwardRunner(log_dir=_P(a.log_dir) if a.log_dir else None)
    import json as _j
    st = _j.loads(r.status_path.read_text()) if r.status_path.exists() else {}
    _p({"status": st, "chains": r.verify_all()})


def cmd_forward_replay(a):
    from .forward.accounts import ForwardRunner
    from pathlib import Path as _P
    r = ForwardRunner(log_dir=_P(a.log_dir), mode="replay")
    _p(r.replay(a.start, a.end))


def cmd_forward_service(a):
    from .forward.service import ForwardService
    from pathlib import Path as _P
    svc = ForwardService(log_dir=_P(a.log_dir) if a.log_dir else None)
    if a.interval:
        svc.interval = a.interval
    if a.once:
        if not svc.acquire_lock():
            print("another forward-service instance is running; exit"); raise SystemExit(3)
        try:
            svc.startup_check(); _p(svc.cycle())
        finally:
            svc.release_lock()
    else:
        svc.run(max_cycles=a.max_cycles)


def cmd_forward_health(a):
    from .forward.service import ForwardService
    from pathlib import Path as _P
    h = ForwardService(log_dir=_P(a.log_dir) if a.log_dir else None).health(); _p(h)
    raise SystemExit(0 if h["ok"] else 1)


def cmd_forward_daily_report(a):
    import json as _j
    from .forward.report import daily_report
    from .config import cfg as _cfg, report_dir as _rd, own_data_dir as _od
    from pathlib import Path as _P
    c = _cfg(); d = _P(a.log_dir) if a.log_dir else (_rd(c) / "phase8_forward"); dsp = _od(c) / "forward_data_status.json"
    _p(daily_report(d, a.date, _j.loads(dsp.read_text()) if dsp.exists() else None, _j.loads((d / "health.json").read_text()) if (d / "health.json").exists() else None))


def cmd_forward_acceptance(a):
    from .forward.acceptance import acceptance_status
    from .config import cfg as _cfg, report_dir as _rd
    from pathlib import Path as _P
    c = _cfg(); d = _P(a.log_dir) if a.log_dir else (_rd(c) / "phase8_forward")
    _p(acceptance_status(d, int(c.get("forward", {}).get("interval_s", 600)), start=a.start))


def cmd_forward_data_audit(a):
    from .forward.datafeed import MarketDataService, cross_source_audit
    _p(cross_source_audit(MarketDataService(), ["BTCUSDT", "ETHUSDT"], a.tf, a.hours))


def cmd_dashboard(a):
    import subprocess
    app = Path(__file__).resolve().parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(a.port), "--server.headless", "true"], check=False)


def cmd_export(a):
    from .export import export_all
    _p(export_all(a.out))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brando_pa", description="Brando Price Action 量化交易引擎（研究/回测/模拟盘；默认禁止真实下单）")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--exec-tf", dest="exec_tf", choices=["15m", "1h", "4h"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("--timeframes", nargs="*"); p.set_defaults(f=cmd_ingest)
    p = sub.add_parser("status"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("analyze"); p.set_defaults(f=cmd_analyze)
    p = sub.add_parser("signal"); p.add_argument("--equity", type=float, default=None); p.set_defaults(f=cmd_signal)
    p = sub.add_parser("backtest"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--name", default="latest"); p.add_argument("--strategy"); p.add_argument("--no-filters", dest="no_filters", action="store_const", const=True, default=None); p.set_defaults(f=cmd_backtest)
    p = sub.add_parser("walkforward"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--name", default="latest"); p.add_argument("--strategy"); p.add_argument("--no-filters", dest="no_filters", action="store_true"); p.add_argument("--workers", type=int); p.add_argument("--train-months", dest="train_months", type=int); p.add_argument("--valid-months", dest="valid_months", type=int); p.add_argument("--test-months", dest="test_months", type=int); p.add_argument("--no-perturb", dest="no_perturb", action="store_true"); p.add_argument("--regime-gating", dest="regime_gating", action="store_true"); p.add_argument("--set", nargs="*", help="配置覆盖，如 mtf.mode=aligned"); p.set_defaults(f=cmd_walkforward)
    p = sub.add_parser("experiments"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--name", default="latest"); p.add_argument("--only"); p.add_argument("--set", nargs="*", help="配置覆盖，如 costs.funding.assumed_rate_8h=0.00004"); p.add_argument("--workers", type=int); p.set_defaults(f=cmd_experiments)
    p = sub.add_parser("audit"); p.set_defaults(f=cmd_audit)
    p = sub.add_parser("evaluate"); p.add_argument("--names"); p.set_defaults(f=cmd_evaluate)
    p = sub.add_parser("phase5", help="第五阶段：趋势状态 + 模型 A/B/C + 三种退出 × 十组对照 × 窗口 × 成本轮次（预登记，一次性运行）"); p.add_argument("--workers", type=int); p.add_argument("--windows", nargs="*"); p.add_argument("--set", nargs="*", help="事后消融/敏感性用的配置覆盖，如 trend.exits.hard_limits.max_drawdown_halt=1.0"); p.add_argument("--out-suffix", dest="out_suffix"); p.set_defaults(f=cmd_phase5)
    p = sub.add_parser("trend-signal"); p.set_defaults(f=cmd_trend_signal)
    p = sub.add_parser("phase6", help="第六阶段：SMA200 审计 + 策略 A/B × 熔断三模式 × BTC/ETH/等权 × 窗口 × 成本（预登记，一次性运行）"); p.add_argument("--workers", type=int); p.add_argument("--windows", nargs="*"); p.add_argument("--set", nargs="*"); p.add_argument("--out-suffix", dest="out_suffix"); p.set_defaults(f=cmd_phase6)
    p = sub.add_parser("sma200-audit"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--fee", type=float, default=0.0); p.add_argument("--slip", type=float, default=0.0); p.set_defaults(f=cmd_sma200_audit)
    p = sub.add_parser("trendsys-signal"); p.set_defaults(f=cmd_trendsys_signal)
    p = sub.add_parser("phase7", help="第七阶段：BTC/ETH/USDT 资金配置模型 A/B/C × 三风险模式 × 窗口 × 成本 + 六个固定参考 + USDT 排行榜"); p.add_argument("--workers", type=int); p.add_argument("--windows", nargs="*"); p.add_argument("--set", nargs="*"); p.add_argument("--out-suffix", dest="out_suffix"); p.set_defaults(f=cmd_phase7)
    p = sub.add_parser("usdt-signal"); p.set_defaults(f=cmd_usdt_signal)
    p = sub.add_parser("forward-data", help="第八阶段：真实行情更新（Binance → OKX → Bybit → Coinbase → 镜像；只保留已收盘 K 线）"); p.add_argument("--loop", action="store_true"); p.add_argument("--interval", type=int, default=300); p.add_argument("--max-iter", dest="max_iter", type=int); p.set_defaults(f=cmd_forward_data)
    p = sub.add_parser("forward-run", help="第八阶段：前瞻模拟账户（A BTC SMA200 / B 模型 B + 状态机 / 参考 ETH SMA200）运行一次或循环"); p.add_argument("--loop", action="store_true"); p.add_argument("--interval", type=int, default=600); p.add_argument("--no-update", dest="no_update", action="store_true"); p.add_argument("--log-dir", dest="log_dir"); p.set_defaults(f=cmd_forward_run)
    p = sub.add_parser("forward-status"); p.add_argument("--log-dir", dest="log_dir"); p.set_defaults(f=cmd_forward_status)
    p = sub.add_parser("forward-service", help="第九阶段：24 小时前瞻服务（行情→账户→心跳→日报→容量→告警；由 launchd/systemd/Docker 管理）"); p.add_argument("--once", action="store_true"); p.add_argument("--interval", type=int); p.add_argument("--max-cycles", dest="max_cycles", type=int); p.add_argument("--log-dir", dest="log_dir"); p.set_defaults(f=cmd_forward_service)
    p = sub.add_parser("forward-health", help="健康检查（退出码 0/1）"); p.add_argument("--log-dir", dest="log_dir"); p.set_defaults(f=cmd_forward_health)
    p = sub.add_parser("forward-daily-report"); p.add_argument("--date"); p.add_argument("--log-dir", dest="log_dir"); p.set_defaults(f=cmd_forward_daily_report)
    p = sub.add_parser("forward-acceptance", help="72 小时真实时间可用性验收状态（不足 72h 固定为未完成）"); p.add_argument("--log-dir", dest="log_dir"); p.add_argument("--start"); p.set_defaults(f=cmd_forward_acceptance)
    p = sub.add_parser("forward-data-audit", help="实时跨来源行情审计（只读）"); p.add_argument("--tf", default="1h"); p.add_argument("--hours", type=int, default=48); p.set_defaults(f=cmd_forward_data_audit)
    p = sub.add_parser("forward-replay", help="独立历史回放（mode=replay，单独目录，不是前瞻）"); p.add_argument("--start", required=True); p.add_argument("--end"); p.add_argument("--log-dir", dest="log_dir", required=True); p.set_defaults(f=cmd_forward_replay)
    p = sub.add_parser("usdt-paper", help="第七阶段前瞻模拟（资金配置；只读行情、追加记录、真实下单禁用）"); p.add_argument("--replay", action="store_true"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--verify", action="store_true"); p.add_argument("--log-dir", dest="log_dir"); p.add_argument("--model"); p.add_argument("--risk-mode", dest="risk_mode"); p.set_defaults(f=cmd_usdt_paper)
    p = sub.add_parser("trendsys-paper", help="前瞻模拟（只读行情、追加记录、真实下单禁用）"); p.add_argument("--replay", action="store_true"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--verify", action="store_true"); p.add_argument("--log-dir", dest="log_dir"); p.add_argument("--strategy", default="A_sma200"); p.add_argument("--risk-mode", dest="risk_mode", default="state_machine"); p.set_defaults(f=cmd_trendsys_paper)
    p = sub.add_parser("phase4", help="第四阶段纯现货：六组对照 × 窗口 × 成本模式（预登记，零自由度）"); p.add_argument("--no-charts", dest="no_charts", action="store_true"); p.set_defaults(f=cmd_phase4)
    p = sub.add_parser("spot-backtest"); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--with-cost", dest="with_cost", action="store_true"); p.set_defaults(f=cmd_spot_backtest)
    p = sub.add_parser("spot-signal"); p.set_defaults(f=cmd_spot_signal)
    p = sub.add_parser("phase3", help="第三阶段预登记流程：训练/验证选止损模式 → 锁定测试一次 → 压力测试 → 止损诊断"); p.set_defaults(f=cmd_phase3)
    p = sub.add_parser("paper"); p.add_argument("--once", action="store_true"); p.add_argument("--loop", action="store_true"); p.add_argument("--replay", action="store_true"); p.add_argument("--replay-bars", dest="replay_bars", type=int, default=3000); p.add_argument("--equity", type=float, default=None); p.set_defaults(f=cmd_paper)
    p = sub.add_parser("dashboard"); p.add_argument("--port", type=int, default=8502); p.set_defaults(f=cmd_dashboard)
    p = sub.add_parser("export"); p.add_argument("--out"); p.set_defaults(f=cmd_export)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
