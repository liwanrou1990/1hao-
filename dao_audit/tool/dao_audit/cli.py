"""dao_audit CLI — authorized static + permission analysis, local-chain PoC
verification, safe-vs-vulnerable benchmark, and report generation.

All dynamic verification runs on a LOCAL test chain against SIMULATED assets.
Any non-local RPC, and mainnet forking, are refused by the authorization layer.
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .authorization import Authorization, AuthorizationError
from .static_scan import scan_authorized, analyze_authorized, analyze_file
from .runner import run_forge_json, summarize, ForgeNotFound
from . import report as R
from . import benchmark as B
from . import fundflow_report as FF
from . import verify as VERIFY
from .market import events as MKT
from .market import dashboard as DASH
from .market import backtest as BT
from .market import netcheck as NET
from .market import edge_runner as EDGE
from .market import accept as ACCEPT
from .market import rpcverify as RPCV
from .market import events_study as ESTUDY
from .dex import scan as DEXSCAN
from .dex import graph as DEXGRAPH
from .dex import runner as DEXRUN
from .dex import perf as DEXPERF
from .rules import RULES


def _root(args) -> Path:
    return Path(args.project).resolve()


def _auth(args) -> Authorization:
    root = _root(args)
    cfg = Path(args.auth_config) if args.auth_config else root / "config/authorization.yaml"
    return Authorization(root, cfg)


def _meta(root, rpc_url, auth, files_analyzed=None):
    return {
        "project": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "authorized_by": auth.cfg.get("authorization", {}).get("authorized_by", "n/a"),
        "chain": rpc_url or "forge in-process local EVM (no network)",
        "files_analyzed": files_analyzed,
        "tool_version": "0.2.0",
    }


def cmd_list_rules(args):
    for r in RULES:
        print(f"{r.id}  [{r.severity:8}] {r.title}")
        print(f"        PoC={r.poc_test}  Fix={r.fix_test}")
    return 0


def cmd_scan(args):
    auth = _auth(args)
    findings = analyze_authorized(auth)
    print(f"分析授权源文件: {len(auth.authorized_sol_files())} 个 .sol")
    print(f"检出: {len(findings)} 处")
    for f in findings:
        print(f"  [{f.rule_id}] {f.contract}.{f.function} @ {f.file}:{f.line} "
              f"conf={f.confidence} fp={f.fp_likelihood}")
    return 0


def _run_forge(root, auth, rpc_url):
    auth.assert_local_rpc(rpc_url)
    auth.assert_no_mainnet_fork()
    return summarize(run_forge_json(root, auth, rpc_url=rpc_url))


def cmd_verify(args):
    auth = _auth(args); root = _root(args)
    try:
        summ = _run_forge(root, auth, args.rpc_url)
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr); return 2
    except ForgeNotFound as e:
        print(f"[错误] {e}", file=sys.stderr); return 3
    total = sum(len(v) for v in summ.values())
    passed = sum(1 for v in summ.values() for ok in v.values() if ok)
    print(f"本地链测试: {passed}/{total} 通过")
    for c, tests in sorted(summ.items()):
        for t, ok in tests.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {c}.{t}")
    return 0 if passed == total else 1


def cmd_audit(args):
    auth = _auth(args); root = _root(args)
    try:
        auth.assert_local_rpc(args.rpc_url)
        auth.assert_no_mainnet_fork()
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr); return 2

    print("[1/4] 授权静态 + 权限分析 ...")
    det_findings = analyze_authorized(auth)
    hits = scan_authorized(auth)
    print(f"      检出 {len(det_findings)} 处")

    print("[2/4] 本地测试链复现 PoC 与修复回归 ...")
    try:
        summ = summarize(run_forge_json(root, auth, rpc_url=args.rpc_url))
    except ForgeNotFound as e:
        print(f"[错误] {e}", file=sys.stderr); return 3

    print("[3/4] 聚合 + 详细发现 ...")
    meta = _meta(root, args.rpc_url, auth, files_analyzed=len(auth.authorized_sol_files()))
    agg = R.build_findings(hits, summ)
    detailed = R.build_detailed_findings(det_findings, summ, case_study=True)

    print("[4/4] 生成报告 ...")
    out_dir = Path(args.out) if args.out else root / "reports_out"
    paths = R.write_reports(out_dir, agg, meta)
    dpaths = R.write_detailed_reports(out_dir, detailed, meta)

    n_conf = sum(1 for f in agg if f["status"] == "CONFIRMED")
    n_fix = sum(1 for f in agg if f["evidence"]["fix_verified"])
    print(f"\n完成: 聚合 {n_conf}/{len(agg)} 已确认, {n_fix} 修复已验证; 详细发现 {len(detailed)} 处")
    for k, v in {**paths, **dpaths}.items():
        print(f"  {k}: {v}")
    return 0


def cmd_analyze(args):
    """Static + permission analysis of authorized files (no dynamic step).
    Use for imported real contracts that have no bundled PoC."""
    auth = _auth(args)
    root = _root(args)
    if args.file:
        target = Path(args.file).resolve()
        try:
            findings = analyze_file(target, auth)
        except AuthorizationError as e:
            print(f"[授权拒绝] {e}", file=sys.stderr); return 2
        files_n = 1
    else:
        findings = analyze_authorized(auth)
        files_n = len(auth.authorized_sol_files())

    meta = _meta(root, None, auth, files_analyzed=files_n)
    detailed = R.build_detailed_findings(findings, {}, case_study=False)
    out_dir = Path(args.out) if args.out else root / "reports_out"
    prefix = args.prefix or "analyze"
    dpaths = R.write_detailed_reports(out_dir, detailed, meta, prefix=prefix)
    print(f"分析 {files_n} 文件, 检出 {len(detailed)} 处")
    for f in detailed:
        loc = f["location"]
        print(f"  [{f['id']}] {loc['contract']}.{loc['function']}:{loc['line']} "
              f"sev={f['severity']} conf={f['confidence']:.2f} fp={f['false_positive']['likelihood']}")
    for k, v in dpaths.items():
        print(f"  {k}: {v}")
    return 0


def cmd_import(args):
    auth = _auth(args)
    try:
        dest = auth.import_contract(Path(args.file), args.attested_by, args.note or "")
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr); return 2
    print(f"已导入授权合约 -> {dest}")
    print(f"授权声明已记录: config/imported_attestations.jsonl")
    # analyze immediately
    findings = analyze_file(dest, auth)
    print(f"分析检出 {len(findings)} 处:")
    for f in findings:
        print(f"  [{f.rule_id}] {f.contract}.{f.function}:{f.line} "
              f"sev={f.severity} conf={f.confidence:.2f} fp={f.fp_likelihood}")
    return 0


def cmd_benchmark(args):
    root = _root(args)
    labels_path = Path(args.labels) if args.labels else root / "config/benchmark_labels.yaml"
    labels = B.load_labels(root, labels_path)
    bench = B.run_benchmark(root, labels)
    md = B.to_markdown(bench)
    out_dir = Path(args.out) if args.out else root / "reports_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    import json
    (out_dir / "benchmark.json").write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "benchmark.md").write_text(md, encoding="utf-8")
    o = bench["overall"]
    print(f"检出率 {o['recall']*100:.1f}%  误报率 {o['fpr']*100:.1f}%  "
          f"漏报率 {o['miss_rate']*100:.1f}%  精确率 {o['precision']*100:.1f}%")
    print(f"  benchmark.json / benchmark.md -> {out_dir}")
    # exit non-zero if regressed below thresholds
    if o["recall"] < args.min_recall or o["fpr"] > args.max_fpr:
        print(f"[基准未达标] recall<{args.min_recall} 或 fpr>{args.max_fpr}", file=sys.stderr)
        return 1
    return 0


def cmd_fundflow(args):
    auth = _auth(args); root = _root(args)
    try:
        r = FF.run(root, auth, rpc_url=args.rpc_url)
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr); return 2
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr); return 3
    paths = FF.write_report(root, r)
    nv = sum(1 for t in r["vuln_tests"] if t["status"] == "Success")
    nf = sum(1 for t in r["fixed_tests"] if t["status"] == "Success")
    print(f"金库资金流转验证: 判定={r['verdict']}  漏洞版 {nv}/{len(r['vuln_tests'])}  "
          f"修复版 {nf}/{len(r['fixed_tests'])}")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    return 0 if r["verdict"] == "PASS" else 1


def cmd_verify_fundflow(args):
    auth = _auth(args); root = _root(args)
    try:
        r = VERIFY.run(root, auth)
    except (AuthorizationError,) as e:
        print(f"[授权拒绝] {e}", file=sys.stderr); return 2
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr); return 3
    print(f"独立复核: 判定={r['verdict']}  全量 {r['passed_tests']}/{r['total_tests']}  "
          f"资金流转 {r['fundflow_passed']}/{r['fundflow_total']}  断言数 {r['assertion_count']}")
    print(f"  报告: reports_out/INDEPENDENT_VERIFICATION.md")
    print(f"  校验: reports_out/SHA256SUMS.txt")
    return 0 if r["verdict"] == "PASS" else 1


def _market_cfg(args):
    root = _root(args)
    import yaml as _y
    cfgp = Path(args.config) if getattr(args, "config", None) else root / "config/market.yaml"
    with open(cfgp, "r", encoding="utf-8") as fh:
        return root, _y.safe_load(fh)


def cmd_market_netcheck(args):
    import json
    root, cfg = _market_cfg(args)
    rep = NET.run(cfg)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "connection_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "connection_report.md").write_text(NET.to_markdown(rep), encoding="utf-8")
    print(f"连接验证: {rep['overall']}  ({rep['ok']}/{rep['total']} OK)")
    for c in rep["checks"]:
        print(f"  {c['category']:22} {c['name']}  {c.get('error') or ''}")
    if rep["denied_hosts"]:
        print("需放行主机:", ", ".join(rep["denied_hosts"]))
    print(f"  报告: {out_dir/'connection_report.md'}")
    return 0 if rep["overall"] == "OK" else 1


def cmd_market_scan(args):
    import json
    root, cfg = _market_cfg(args)
    em_path = root / "reports_out" / "edge_model.json"
    edge_model = {}
    if em_path.exists():
        try:
            edge_model = json.loads(em_path.read_text(encoding="utf-8")).get("edge_model", {})
        except Exception:
            edge_model = {}
    scan = MKT.scan(cfg, edge_model=edge_model)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "market_scan.json").write_text(
        json.dumps(scan, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = DASH.write(scan, out_dir / "market_dashboard.html")
    s = scan["summary"]
    print(f"市场扫描: 真实数据可得={s['any_real_data']}  TRADE={s['trade_signals']}  NO_TRADE={s['no_trade']}")
    for ch, rec in scan["chains"].items():
        print(f"  chain {ch}: {rec['status']}  {rec.get('error') or ('block '+str(rec.get('block')))}")
    for o in scan["opportunities"]:
        d = o["decision"]
        print(f"  {o['symbol']}: {d['decision']} — {d['reason']}")
    print(f"  JSON: {out_dir/'market_scan.json'}")
    print(f"  看板: {html_path}")
    return 0


def cmd_market_dashboard(args):
    import json
    root, cfg = _market_cfg(args)
    sj = root / "reports_out" / "market_scan.json"
    if not sj.exists():
        print("无 market_scan.json，请先运行 market-scan", file=sys.stderr); return 1
    scan = json.loads(sj.read_text(encoding="utf-8"))
    print("看板:", DASH.write(scan, root / "reports_out" / "market_dashboard.html"))
    return 0


def cmd_market_backtest(args):
    import json
    root, cfg = _market_cfg(args)
    from .market.sources import SnapshotSource
    from .market.backtest import Event, run_backtest, defillama_price_at
    snap = SnapshotSource()
    events = []
    # Build events from REAL snapshot proposals; map each DAO to its token key.
    tok_by_symbol = {t.get("symbol"): t["key"] for t in cfg.get("tokens", [])}
    sym_by_space = {d["snapshot_space"]: d["name"] for d in cfg.get("daos", [])}
    name_to_key = {"Aave": tok_by_symbol.get("AAVE"), "Uniswap": tok_by_symbol.get("UNI")}
    unreachable = 0
    for d in cfg.get("daos", []):
        rec = snap.recent_proposals(d["snapshot_space"], first=10)
        if not rec.ok or not isinstance(rec.data, dict):
            unreachable += 1; continue
        key = name_to_key.get(d["name"])
        for p_ in (rec.data.get("data", {}).get("proposals") or []):
            if key and p_.get("end"):
                events.append(Event(ts=int(p_["end"]), coin_key=key, label="proposal_end"))
    result = {"events_collected": len(events), "snapshot_unreachable": unreachable}
    if not events:
        result.update({"status": "INSUFFICIENT_DATA",
                       "reason": "无法获取真实治理事件（Snapshot 不可达）；不以模拟事件替代。"})
    else:
        bt = run_backtest(events, horizon_s=7*24*3600, cost_bps=50.0,
                          price_at=defillama_price_at())
        result["backtest"] = bt
        result["status"] = bt["status"]
    out = root / "reports_out" / "market_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"回测: status={result['status']} events={result['events_collected']} "
          f"snapshot_unreachable={result['snapshot_unreachable']}")
    print(f"  JSON: {out}")
    return 0


def cmd_market_edge(args):
    import json
    root, cfg = _market_cfg(args)
    rep = EDGE.build_and_estimate(cfg)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "edge_model.json").write_text(
        json.dumps({"status": rep["status"], "edge_model": rep["edge_model"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "edge_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "edge_report.md").write_text(EDGE.to_markdown(rep), encoding="utf-8")
    print(f"事件收益模型: status={rep['status']} events={rep['events']} "
          f"confirmed_edges={len(rep['edge_model'])}")
    for sym, e in rep["edge_model"].items():
        print(f"  {sym}: edge={e['edge_bps']:.1f}bps horizon={e['horizon_s']//3600}h "
              f"(in={e['in_sample_n']}, oos={e['oos_n']})")
    if not rep["edge_model"]:
        print(f"  原因: {rep['reason']}")
    print(f"  模型: {out_dir/'edge_model.json'}  报告: {out_dir/'edge_report.json'}")
    return 0


def cmd_market_accept(args):
    import json
    root = _root(args)
    rep = ACCEPT.evaluate(root / "reports_out")
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "acceptance_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "acceptance_report.md").write_text(ACCEPT.to_markdown(rep), encoding="utf-8")
    print(f"验收: {rep['overall']}  首阻塞={rep.get('first_blocking_phase') or '无'}")
    for ph in rep["phases"]:
        print(f"  [{ph['status']}] {ph['phase']}  {ph['evidence']}  {ph.get('blocker') or ''}")
    print(f"  报告: {out_dir/'acceptance_report.md'}")
    return 0 if rep["overall"] == "PASS" else 1


def cmd_market_rpcverify(args):
    import json
    root, cfg = _market_cfg(args)
    rep = RPCV.run(cfg, samples=int(args.samples))
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rpc_verify.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "rpc_verify.md").write_text(RPCV.to_markdown(rep), encoding="utf-8")
    for ch in rep["chains"]:
        print(f"[{ch['chain']}] expected chainId={ch['expected_chain_id']} "
              f"failover->{ch['failover_served_by'] or 'ALL FAIL'} primary={ch['healthy_primary']}")
        for e in ch["endpoints"]:
            print(f"    {e['url']:34} sr={e['success_rate']:.0%} cid={e['chain_id']} "
                  f"block={e['block']} lat={e['avg_latency_ms']}ms")
        if ch["alchemy_note"]:
            print(f"    NOTE: {ch['alchemy_note']}")
    print(f"  报告: {out_dir/'rpc_verify.md'}")
    return 0


def cmd_market_eventstudy(args):
    import json
    root, cfg = _market_cfg(args)
    rep = ESTUDY.run(cfg)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "event_study.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "event_study.md").write_text(ESTUDY.to_markdown(rep), encoding="utf-8")
    if rep["status"] != "OK":
        print(f"事件研究: {rep['status']} — {rep.get('reason','')}")
    else:
        print(f"事件研究: events={rep['n_events']} groups={rep['n_groups']} "
              f"bonferroni_t={rep['bonferroni_t']} confirmed={len(rep['confirmed_signals'])}")
        for r in rep["results"]:
            if r["confirmed"]:
                print(f"  CONFIRMED {r['category']} {r['event_type']} {r['horizon_h']}h "
                      f"OOS={r['oos_aligned_mean_bps']}bps")
        if not rep["confirmed_signals"]:
            print("  无确认信号 → 全部 NO_TRADE（未人为放宽标准）")
    print(f"  报告: {out_dir/'event_study.md'}")
    return 0


def _dex_cfg(args):
    import yaml as _y
    root = _root(args)
    cfgp = Path(args.config) if getattr(args, "config", None) else root / "config/dex.yaml"
    with open(cfgp, "r", encoding="utf-8") as fh:
        return root, _y.safe_load(fh)


def cmd_dex_scan(args):
    import json
    root, cfg = _dex_cfg(args)
    rep = DEXSCAN.scan(cfg)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dex_arb_scan.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dex_arb_scan.md").write_text(DEXSCAN.to_markdown(rep), encoding="utf-8")
    print(f"DEX套利扫描: status={rep.get('status')} ETH=${rep.get('eth_price_usd')} "
          f"候选={rep.get('n_candidates','-')}")
    for r in rep.get("same_chain", []):
        b = r.get("best")
        print(f"  {r['chain']:9} {r['pair']:10} {r['decision']:16} "
              f"best_net={b['net_usd'] if b else '-'}USD  {r['reason'][:60]}")
    print(f"  报告: {out_dir/'dex_arb_scan.md'}")
    return 0


def cmd_dex_monitor(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    state = Path(args.state) if args.state else out_dir / "dex_monitor_state.jsonl"
    rep = DEXSCAN.monitor_resumable(cfg, state, max_new=int(args.max_new),
                                    interval_s=int(args.interval), target_hours=float(args.target_hours))
    (out_dir / "dex_arb_monitor.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DEX监控(可恢复): 累计{rep['snapshots_total']}快照 (+{rep['added_this_run']}) "
          f"观测{rep['observation_hours']}h/{rep['target_hours']}h 候选频率={rep['candidate_frequency']}")
    print(f"  各对最佳净收益(USD): {rep['best_net_seen_by_pair']}")
    print(f"  状态文件(可续跑): {rep['state_file']}")
    return 0


def cmd_dex_report(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"
    sj = out_dir / "dex_arb_scan.json"
    if not sj.exists():
        print("无 dex_arb_scan.json，请先运行 dex-scan", file=sys.stderr); return 1
    scan_rep = json.loads(sj.read_text(encoding="utf-8"))
    # roundtrip fork verified flag: user runs the fork test separately; default False
    rep = DEXSCAN.rank_report(scan_rep, roundtrip_fork_verified=bool(args.fork_verified))
    lines = ["# 真实 DEX 套利机会排序报告", "",
             f"> 按扣全部成本后净收益排序。判定: **{rep['verdict']}**。{rep['note']}", "",
             "| 排名 | 链 | 交易对 | 买@ | 卖@ | 规模USD | 最佳净USD | 决策 | 模拟可行 |",
             "|------|----|--------|-----|-----|---------|-----------|------|----------|"]
    for i, r in enumerate(rep["ranked"], 1):
        lines.append("| {i} | {c} | {p} | {b} | {s} | {sz} | {n} | {d} | {f} |".format(
            i=i, c=r["chain"], p=r["pair"], b=r["buy_on"] or "-", s=r["sell_on"] or "-",
            sz=f"{r['size_usd']:,}" if r["size_usd"] else "-",
            n=r["best_net_usd"] if r["best_net_usd"] is not None else "-",
            d=r["decision"], f="YES" if r["simulated_feasible"] else "NO"))
    if rep["n_feasible"] == 0:
        lines += ["", "## 结论：NO_TRADE", "",
                  "所有机会扣除全部成本后净收益非正，或未通过完整双腿 fork 验证 → 无可标记为"
                  "模拟可行的套利。不制造正收益结论。"]
    (out_dir / "dex_opportunities.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "dex_opportunities.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"排序报告: 判定={rep['verdict']} 可行机会={rep['n_feasible']}")
    for r in rep["ranked"]:
        print(f"  {r['chain']:9} {r['pair']:10} net=${r['best_net_usd']} {r['decision']} feasible={r['simulated_feasible']}")
    print(f"  报告: {out_dir/'dex_opportunities.md'}")
    return 0


def cmd_dex_graph(args):
    import json
    root, cfg = _dex_cfg(args)
    rep = DEXGRAPH.run(cfg, max_hops=int(args.max_hops))
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dex_graph.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dex_graph.md").write_text(DEXGRAPH.to_markdown(rep), encoding="utf-8")
    print(f"图搜索: status={rep.get('status')} 闭环={rep.get('n_cycles')} 正净收益={rep.get('n_positive')} "
          f"判定={rep.get('verdict')}")
    for ch in rep.get("chains", []):
        top = ch["cycles"][0] if ch["cycles"] else None
        if top:
            print(f"  {ch['chain']}: best {top['route']} size=${top['size_usd']:,} net=${top['net_usd']} ROC={top['roc_pct']}%")
    if rep.get("root_cause"):
        print(f"  根因: {rep['root_cause']['primary_cause']}")
    print(f"  报告: {out_dir/'dex_graph.md'}")
    return 0


def cmd_dex_run(args):
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    db = Path(args.db) if args.db else out_dir / "dex_monitor.db"
    print(f"持续监控启动: db={db} max_rounds={args.max_rounds} max_seconds={args.max_seconds} "
          f"(0=无界，用于常驻部署)")
    res = DEXRUN.run_continuous(cfg, db, max_rounds=int(args.max_rounds),
                                max_seconds=int(args.max_seconds), poll_s=float(args.poll),
                                rate_limit_s=float(args.rate_limit), fast=bool(args.fast),
                                size_usd=float(args.size), tier2_gross_bar_bps=float(args.tier2_bar))
    mode = "fast(Tier-1批量)" if args.fast else "full(全扫描基准)"
    print(f"结束[{mode}]: rounds={res['rounds']} scans={res['scans']} elapsed={res['elapsed_s']}s db={res['db']}")
    return 0


def cmd_dex_acceptance(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"
    db = Path(args.db) if args.db else out_dir / "dex_monitor.db"
    if not db.exists():
        print(f"无 DB {db}，请先运行 dex-run", file=sys.stderr); return 1
    rep = DEXRUN.acceptance_from_db(db)
    (out_dir / "dex_acceptance.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dex_acceptance.md").write_text(DEXRUN.acceptance_markdown(rep), encoding="utf-8")
    print(f"验收: 连续{rep['continuous_seconds_observed']}s 区块{rep['blocks_observed']} 扫描{rep['valid_scans']} "
          f"正价差{rep['positive_spread_count']} 正净收益{rep['positive_net_count']} "
          f"Fork验证{rep['fork_verified_count']} 失败率{rep['failure_rate']} 判定={rep['verdict']}")
    print(f"  报告: {out_dir/'dex_acceptance.md'}")
    return 0


def cmd_dex_profile(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    chain = args.chain
    print(f"完整扫描耗时归因: chain={chain}（运行真实全扫描，可能较慢）...")
    rep = DEXPERF.profile_full_scan(cfg, chain, eth_price=float(args.eth_price))
    (out_dir / f"dex_profile_{chain}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    print(f"  全扫描 wall={rep['scan_wall_ms']}ms")
    print(f"  Quoter(cast): {rep['quoter_cast_ms']}ms / {rep['quoter_cast_calls']} 笔 "
          f"({rep['breakdown_pct']['quoter_cast']}%)")
    print(f"  block/gas RPC: {rep['block_gas_rpc_ms']}ms / {rep['block_gas_calls']} 笔 "
          f"({rep['breakdown_pct']['block_gas_rpc']}%)")
    print(f"  路径计算: {rep['path_compute_ms']}ms ({rep['breakdown_pct']['path_compute']}%)")
    print(f"  DB写入(估,{rep['db_rows']}行): {rep['db_write_ms_est']}ms")
    return 0


def cmd_dex_health(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    db = Path(args.db) if args.db else out_dir / "dex_monitor.db"
    rep = DEXRUN.health_check(cfg, db, max_scan_age_s=float(args.max_scan_age))
    (out_dir / "dex_health.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    print(f"健康检查: healthy={rep['healthy']}")
    for c in rep["checks"]:
        print(f"  [{'OK ' if c['ok'] else 'BAD'}] {c['check']}: {c['detail']}")
    return 0 if rep["healthy"] else 3


def cmd_dex_perf(args):
    import json
    root, cfg = _dex_cfg(args)
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    chain = args.chain
    print(f"性能基准(Tier-1 候选发现): chain={chain} blocks={args.blocks} size=${int(args.size):,} ...")
    bench = DEXPERF.benchmark(cfg, chain, n_blocks=int(args.blocks), size_usd=float(args.size),
                              poll_s=float(args.poll), max_seconds=int(args.max_seconds))
    equiv = {"data_status": "SKIPPED"}
    if not args.no_equiv:
        print("防漏检等价校验(批量 vs 顺序，同区块) ...")
        equiv = DEXPERF.equivalence_check(cfg, chain, size_usd=float(args.size))
    (out_dir / f"dex_perf_{chain}.json").write_text(
        json.dumps({"benchmark": bench, "equivalence": equiv}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / f"dex_perf_{chain}.md").write_text(DEXPERF.to_markdown(bench, equiv), encoding="utf-8")
    lm = bench["latency_ms"]
    print(f"  延迟(ms) P50={lm['p50']} P95={lm['p95']} P99={lm['p99']} mean={lm['mean']} "
          f"(区块{bench['blocks_measured']}, RPC/scan={bench['rpc_calls_per_scan']})")
    print(f"  Base P95<1s 目标: {'达标' if bench['p95_under_1s'] else '未达标'} (P95={lm['p95']}ms)")
    if equiv.get("data_status") == "OK":
        print(f"  等价校验: 比对{equiv['compared']}报价 一致={equiv['identical']} "
              f"不一致={len(equiv['mismatches'])}")
    print(f"  报告: {out_dir/('dex_perf_'+chain+'.md')}")
    return 0


def cmd_dex_tier1(args):
    import json
    root, cfg = _dex_cfg(args)
    chain = args.chain
    ccfg = cfg["chains"][chain]
    pairs = [tuple(p) for p in cfg.get("pairs", {}).get(chain, [])]
    r = DEXPERF.tier1_scan(chain, ccfg, pairs, cfg.get("fee_tiers", [100, 500, 3000]),
                           cfg.get("tick_spacings", [1, 50, 100, 200, 2000]), float(args.size))
    out_dir = root / "reports_out"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"dex_tier1_{chain}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
    print(f"Tier-1 扫描: chain={chain} block={r['block']} status={r['data_status']} "
          f"latency={r['latency_ms']}ms rpc={r['rpc_calls']} inner={r['n_inner_quotes']}")
    for fl in r.get("filter_log", []):
        print(f"  {fl['pair']:12} flagged={fl['flagged']} gross_bps={fl['gross_bps']}  {fl['reason']}")
    print(f"  候选(交 Tier-2)={len(r.get('candidates', []))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dao-audit",
        description="DAO 治理合约安全审计系统 (仅授权/本地链/模拟资产)")
    p.add_argument("--project", default=".", help="工程根目录 (含 foundry.toml)")
    p.add_argument("--auth-config", default=None, help="授权配置 (默认 config/authorization.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-rules", help="列出检测规则").set_defaults(func=cmd_list_rules)
    sub.add_parser("scan", help="静态+权限分析（列出检出）").set_defaults(func=cmd_scan)

    sv = sub.add_parser("verify", help="本地链运行 PoC/回归")
    sv.add_argument("--rpc-url", default=None, help="本地链 RPC (仅 localhost/127.0.0.1)")
    sv.set_defaults(func=cmd_verify)

    au = sub.add_parser("audit", help="完整审计: 分析 + 本地链复现 + 聚合/详细报告")
    au.add_argument("--rpc-url", default=None)
    au.add_argument("--out", default=None)
    au.set_defaults(func=cmd_audit)

    an = sub.add_parser("analyze", help="静态+权限分析授权合约（真实合约无需 PoC）")
    an.add_argument("--file", default=None, help="单个授权 .sol（默认分析全部授权文件）")
    an.add_argument("--out", default=None)
    an.add_argument("--prefix", default=None, help="报告文件名前缀")
    an.set_defaults(func=cmd_analyze)

    im = sub.add_parser("import", help="导入我拥有/授权审计的真实合约并分析")
    im.add_argument("file", help="真实合约 .sol 路径")
    im.add_argument("--attested-by", required=True, help="授权来源声明（你的身份/委托方）")
    im.add_argument("--note", default=None, help="授权备注")
    im.set_defaults(func=cmd_import)

    bm = sub.add_parser("benchmark", help="安全/漏洞对照基准：检出率/误报率/漏报率")
    bm.add_argument("--labels", default=None)
    bm.add_argument("--out", default=None)
    bm.add_argument("--min-recall", type=float, default=1.0, help="最低检出率阈值")
    bm.add_argument("--max-fpr", type=float, default=0.0, help="最高误报率阈值")
    bm.set_defaults(func=cmd_benchmark)

    ff = sub.add_parser("fundflow", help="金库资金流转验证（本地链真实执行 + trace/gas 报告）")
    ff.add_argument("--rpc-url", default=None, help="本地链 RPC (仅 localhost/127.0.0.1)")
    ff.set_defaults(func=cmd_fundflow)

    vf = sub.add_parser("recheck", help="干净环境独立复核：重建+全量测试+证据+SHA256")
    vf.set_defaults(func=cmd_verify_fundflow)

    nc = sub.add_parser("market-netcheck", help="Phase1 真实数据连接验证（分类403/API/429/5xx）")
    nc.add_argument("--config", default=None)
    nc.set_defaults(func=cmd_market_netcheck)

    ms = sub.add_parser("market-scan", help="真实数据市场扫描：事件/价格/成本/TRADE决策+看板")
    ms.add_argument("--config", default=None, help="market.yaml 路径")
    ms.set_defaults(func=cmd_market_scan)

    md = sub.add_parser("market-dashboard", help="由上次 market-scan 结果重建 HTML 看板")
    md.add_argument("--config", default=None)
    md.set_defaults(func=cmd_market_dashboard)

    mb = sub.add_parser("market-backtest", help="事件驱动回测（真实历史数据；防未来泄漏+训练/验证/样本外）")
    mb.add_argument("--config", default=None)
    mb.set_defaults(func=cmd_market_backtest)

    me = sub.add_parser("market-edge", help="Phase2 事件收益模型：真实事件+历史价→样本外确认的edge")
    me.add_argument("--config", default=None)
    me.set_defaults(func=cmd_market_edge)

    ma = sub.add_parser("market-accept", help="Phase4 验收门：逐阶段 PASS/BLOCKED + 阻塞根因")
    ma.set_defaults(func=cmd_market_accept)

    rv = sub.add_parser("market-rpcverify", help="RPC 多节点验证：chainId/区块/时间戳/延迟/成功率+失败切换")
    rv.add_argument("--config", default=None)
    rv.add_argument("--samples", default=5)
    rv.set_defaults(func=cmd_market_rpcverify)

    es = sub.add_parser("market-eventstudy", help="v0.4 小时级事件研究：分类+多窗口+walk-forward+分档净收益")
    es.add_argument("--config", default=None)
    es.set_defaults(func=cmd_market_eventstudy)

    dx = sub.add_parser("dex-scan", help="真实DEX同链跨市场套利扫描（可执行报价+全成本+分档容量）")
    dx.add_argument("--config", default=None)
    dx.set_defaults(func=cmd_dex_scan)

    dm = sub.add_parser("dex-monitor", help="可恢复连续监控(JSONL续跑)：频率/持续/成交，支持24h+累积")
    dm.add_argument("--config", default=None)
    dm.add_argument("--state", default=None, help="JSONL 状态文件(续跑)")
    dm.add_argument("--max-new", default=5, help="本次最多新增快照数")
    dm.add_argument("--interval", default=30, help="采样间隔秒(限流友好)")
    dm.add_argument("--target-hours", default=24, help="累计观测目标小时")
    dm.set_defaults(func=cmd_dex_monitor)

    dr = sub.add_parser("dex-report", help="按净利/容量/模拟成功率排序的机会报告")
    dr.add_argument("--config", default=None)
    dr.add_argument("--fork-verified", action="store_true", help="完整双腿 fork 已验证时置位")
    dr.set_defaults(func=cmd_dex_report)

    dg = sub.add_parser("dex-graph", help="图搜索三角/多跳闭环套利 + 根因分析")
    dg.add_argument("--config", default=None)
    dg.add_argument("--max-hops", default=3)
    dg.set_defaults(func=cmd_dex_graph)

    dn = sub.add_parser("dex-run", help="持续监控(新区块触发+SQLite持久化+续跑)；常驻部署入口")
    dn.add_argument("--config", default=None); dn.add_argument("--db", default=None)
    dn.add_argument("--max-rounds", default=0); dn.add_argument("--max-seconds", default=0)
    dn.add_argument("--poll", default=10); dn.add_argument("--rate-limit", default=2)
    dn.add_argument("--fast", action="store_true", help="用Tier-1批量候选发现(2 eth_call/链)跟上出块节奏")
    dn.add_argument("--size", default=1000, help="fast模式Tier-1参考本金USD")
    dn.add_argument("--tier2-bar", default=0.0, help="fast模式触发Tier-2精确全扫描的毛收益bps下限(默认>0才触发)")
    dn.set_defaults(func=cmd_dex_run)

    da = sub.add_parser("dex-acceptance", help="从监控DB生成连续监控验收报告")
    da.add_argument("--config", default=None); da.add_argument("--db", default=None)
    da.set_defaults(func=cmd_dex_acceptance)

    dp = sub.add_parser("dex-perf", help="性能基准(Tier-1候选发现P50/P95/P99)+防漏检等价校验")
    dp.add_argument("--config", default=None)
    dp.add_argument("--chain", default="base")
    dp.add_argument("--blocks", default=20, help="测量的不同区块数")
    dp.add_argument("--size", default=1000, help="参考本金USD")
    dp.add_argument("--poll", default=1.0, help="轮询出块间隔秒")
    dp.add_argument("--max-seconds", default=120, help="基准最长运行秒")
    dp.add_argument("--no-equiv", action="store_true", help="跳过等价校验")
    dp.set_defaults(func=cmd_dex_perf)

    dt = sub.add_parser("dex-tier1", help="单次 Tier-1 批量候选发现(2 RPC)+逐对过滤原因")
    dt.add_argument("--config", default=None)
    dt.add_argument("--chain", default="base")
    dt.add_argument("--size", default=1000)
    dt.set_defaults(func=cmd_dex_tier1)

    dh = sub.add_parser("dex-health", help="健康检查(RPC失败切换/DB可写/扫描时效)；非零退出=不健康")
    dh.add_argument("--config", default=None); dh.add_argument("--db", default=None)
    dh.add_argument("--max-scan-age", default=300, help="最近一次扫描允许的最大秒龄")
    dh.set_defaults(func=cmd_dex_health)

    dpr = sub.add_parser("dex-profile", help="完整扫描耗时归因(RPC/Quoter cast/路径计算/DB写入)")
    dpr.add_argument("--config", default=None)
    dpr.add_argument("--chain", default="base")
    dpr.add_argument("--eth-price", default=3000.0, help="归因用固定ETH计价(避免DefiLlama波动)")
    dpr.set_defaults(func=cmd_dex_profile)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
