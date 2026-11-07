"""最新信号报告（每个标的最后一根已收盘执行 K 线）→ reports_out/brando_pa/signals/latest.json + history.jsonl。
为还原策略状态机（例如策略 A 正在等待回踩），先在最近 warmup_bars 根 K 线上空跑引擎再取最后一根。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from spot90.atomic import atomic_write_json

from ..backtest.runner import load_contexts
from ..config import cfg, report_dir
from ..data.sources import bars_are_stale
from .signal_engine import SignalEngine


def latest_signals(symbols: list[str] | None = None, exec_tf: str | None = None, equity: float | None = None, c: dict | None = None, warmup_bars: int = 600, contexts=None, now=None) -> dict:
    c = c or cfg(); symbols = symbols or c["symbols"]; exec_tf = exec_tf or c["timeframes"]["execution"]
    equity = float(equity or c["risk"]["account_equity_usdt"])
    ctxs = contexts or load_contexts(symbols, exec_tf, c)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "exec_tf": exec_tf, "equity": equity, "symbols": {}}
    for s, mc in ctxs.items():
        eng = SignalEngine(c)
        i0 = max(1, mc.n - warmup_bars)
        for i in range(i0, mc.n - 1):
            eng.evaluate(mc, i, equity)
        stale, why = bars_are_stale(mc.ex, exec_tf, now=now)
        rep = eng.evaluate(mc, mc.n - 1, equity, data_ok=not stale)
        d = rep.to_dict(); d["freshness"] = why
        out["symbols"][s] = d
    sd = report_dir(c) / "signals"; sd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(sd / "latest.json", out, indent=2, default=str)
    with open(sd / "history.jsonl", "a", encoding="utf-8") as fh:
        for s, d in out["symbols"].items():
            fh.write(json.dumps({"generated_at": out["generated_at"], "symbol": s, "ts": d["ts"], "status": d["status"], "price": d["price"], "reasons": d["reasons"][:3]}, ensure_ascii=False, default=str) + "\n")
    out["path"] = str(sd / "latest.json")
    return out
