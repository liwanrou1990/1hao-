"""命令行：spot90 <command>。第一阶段只读行情，不含任何下单路径。"""
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
    st = run_ingest(a.symbols, a.timeframes, a.source, skip_crosscheck=a.no_crosscheck)
    for sym, tfs in st["symbols"].items():
        for tf, e in tfs.items():
            cr = e["continuity"]
            print(f"{sym} {tf}: {e['result']} 来源={cr.get('sources')} 范围 {cr['first']} → {cr['last']} ({cr['years']} 年, {cr['n_bars']} 根, 缺口 {cr['n_gap_bars']} 根) "
                  f"满足{int(st and 5)}年要求={e['meets_history_requirement']} 校验={e.get('cross_check', {}).get('median_abs_pct', 'n/a')}%")
    print("探测：", st["probes"])


def cmd_status(a):
    from .data.ingest import load_status
    _p(load_status() or {"error": "尚未运行 ingest"})


def cmd_train(a):
    from .pipeline import train_all
    _p(train_all(a.symbols))


def cmd_backtest(a):
    from .pipeline import backtest_all
    _p(backtest_all(a.symbols, quick=a.quick))


def cmd_predict(a):
    from .pipeline import predict_all
    _p(predict_all(a.symbols, as_of=a.as_of))


def cmd_daily(a):
    from .pipeline import daily
    _p(daily(a.symbols, retrain=a.retrain))


def cmd_dashboard(a):
    import subprocess
    app = Path(__file__).resolve().parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(a.port), "--server.headless", "true"], check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spot90", description="BTC/ETH 90 天现货投资 AI 预测系统（只读研究）")
    ap.add_argument("--symbols", nargs="*")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="采集/更新 K 线（Bitget 首选 → 备用公开源），写 data_status.json"); p.add_argument("--timeframes", nargs="*"); p.add_argument("--source", choices=["bitget", "static_klines"]); p.add_argument("--no-crosscheck", action="store_true"); p.set_defaults(f=cmd_ingest)
    p = sub.add_parser("status", help="查看数据状态"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("train", help="用全部可用历史训练最终模型（用于每日预测）"); p.set_defaults(f=cmd_train)
    p = sub.add_parser("backtest", help="Walk-Forward 样本外回测 + 校准 + 与买入持有对比"); p.add_argument("--quick", action="store_true"); p.set_defaults(f=cmd_backtest)
    p = sub.add_parser("predict", help="生成当日预测并记录历史"); p.add_argument("--as-of"); p.set_defaults(f=cmd_predict)
    p = sub.add_parser("daily", help="每日流程：ingest → (可选重训) → predict → 报告"); p.add_argument("--retrain", action="store_true"); p.set_defaults(f=cmd_daily)
    p = sub.add_parser("dashboard", help="启动 Streamlit 中文看板"); p.add_argument("--port", type=int, default=8501); p.set_defaults(f=cmd_dashboard)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
