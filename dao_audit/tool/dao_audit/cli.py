"""dao_audit CLI — orchestrates authorized static scan + local-chain PoC/fix
verification + report generation for DAO governance contracts.
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .authorization import Authorization, AuthorizationError
from .static_scan import scan_authorized, hits_as_dicts
from .runner import run_forge_json, summarize, ForgeNotFound
from .report import build_findings, write_reports, to_markdown
from .rules import RULES


def _project_root(args) -> Path:
    return Path(args.project).resolve()


def _auth(args) -> Authorization:
    root = _project_root(args)
    cfg = Path(args.auth_config) if args.auth_config else root / "config/authorization.yaml"
    return Authorization(root, cfg)


def cmd_list_rules(args):
    for r in RULES:
        print(f"{r.id}  [{r.severity:8}] {r.title}")
        print(f"        PoC={r.poc_test}  Fix={r.fix_test}")
    return 0


def cmd_scan(args):
    auth = _auth(args)
    hits = scan_authorized(auth)
    print(f"扫描授权源文件: {len(auth.authorized_sol_files())} 个 .sol")
    print(f"命中候选: {len(hits)}")
    for h in hits:
        if "/targets/" in h.file or not h.mitigated_hint:
            print(f"  [{h.rule_id}] {h.file}:{h.line}  {h.reason}")
    return 0


def cmd_verify(args):
    auth = _auth(args)
    root = _project_root(args)
    try:
        auth.assert_local_rpc(args.rpc_url)
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr)
        return 2
    data = run_forge_json(root, auth, rpc_url=args.rpc_url)
    summ = summarize(data)
    total = sum(len(v) for v in summ.values())
    passed = sum(1 for v in summ.values() for ok in v.values() if ok)
    print(f"本地链测试: {passed}/{total} 通过")
    for c, tests in sorted(summ.items()):
        for t, ok in tests.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {c}.{t}")
    return 0 if passed == total else 1


def cmd_audit(args):
    auth = _auth(args)
    root = _project_root(args)
    try:
        auth.assert_local_rpc(args.rpc_url)
        auth.assert_no_mainnet_fork()
    except AuthorizationError as e:
        print(f"[授权拒绝] {e}", file=sys.stderr)
        return 2

    print("[1/3] 授权静态扫描 ...")
    hits = scan_authorized(auth)
    print(f"      命中候选 {len(hits)} 处")

    print("[2/3] 本地测试链复现 PoC 与修复回归 ...")
    try:
        data = run_forge_json(root, auth, rpc_url=args.rpc_url)
    except ForgeNotFound as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3
    summ = summarize(data)

    print("[3/3] 生成报告 ...")
    findings = build_findings(hits, summ)
    meta = {
        "project": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "authorized_by": auth.cfg.get("authorization", {}).get("authorized_by", "n/a"),
        "chain": args.rpc_url or "forge in-process local EVM (no network)",
        "tool_version": "0.1.0",
    }
    out_dir = Path(args.out) if args.out else root / "reports_out"
    paths = write_reports(out_dir, findings, meta)

    n_conf = sum(1 for f in findings if f["status"] == "CONFIRMED")
    n_fix = sum(1 for f in findings if f["evidence"]["fix_verified"])
    print(f"\n完成: {n_conf}/{len(findings)} 项已确认, {n_fix} 项修复已验证")
    print(f"  JSON:     {paths['json']}")
    print(f"  Markdown: {paths['markdown']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dao-audit",
                                description="DAO 治理合约安全审计系统 (X100 案例, 仅授权/本地链/模拟资产)")
    p.add_argument("--project", default=".", help="审计工程根目录 (含 foundry.toml)")
    p.add_argument("--auth-config", default=None, help="授权配置 (默认 config/authorization.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list-rules", help="列出检测规则")
    sp.set_defaults(func=cmd_list_rules)

    sp = sub.add_parser("scan", help="仅静态扫描授权合约")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("verify", help="在本地链运行 PoC/回归测试")
    sp.add_argument("--rpc-url", default=None, help="本地链 RPC (仅允许 localhost/127.0.0.1)")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("audit", help="完整审计: 扫描 + 本地链复现 + 报告")
    sp.add_argument("--rpc-url", default=None, help="本地链 RPC (仅允许 localhost/127.0.0.1)")
    sp.add_argument("--out", default=None, help="报告输出目录 (默认 reports_out/)")
    sp.set_defaults(func=cmd_audit)
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
