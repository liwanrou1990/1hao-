"""72 小时验收的加速模拟（云端，虚拟时钟 + 合成行情 + 故障注入）。只用于检验服务与验收逻辑本身，
**不能替代 Mac 上的真实 72 小时验收**，结果中的行情是合成数据，不是前瞻观察。"""
from __future__ import annotations

import contextlib
import fcntl
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.store import BarStore
from . import SEED_DIR, acceptance21, forward21, macguard21, service21


class FakeExchange:
    """模拟交易所公开日线：只返回 now 之前已收盘的 K 线；outage(now) 为真时抛出连接错误。"""
    name = "okx_spot"

    def __init__(self, seed_store: BarStore, until: str = "2027-01-31", outage=None, seed: int = 11):
        self.outage = outage or (lambda now: False); self.now = None; rng = np.random.default_rng(seed); self.data = {}
        for s in ("BTCUSDT", "ETHUSDT"):
            h = seed_store.load(s, "1d", allow_fallback=False); days = pd.date_range(h["ts"].max() + pd.Timedelta(days=1), pd.Timestamp(until, tz="UTC"), freq="D")
            c = float(h["close"].iloc[-1]) * np.exp(np.cumsum(rng.normal(0.0, 0.02, len(days)))); o = np.r_[float(h["close"].iloc[-1]), c[:-1]]
            self.data[s] = pd.DataFrame({"ts": days, "open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": float(h["volume"].tail(30).mean())})

    def fetch(self, symbol, tf, start, end):
        if self.outage(self.now):
            raise ConnectionError("simulated outage")
        d = self.data[symbol]; return d[(d["ts"] >= start) & (d["ts"] + pd.Timedelta(days=1) <= min(end, self.now))].reset_index(drop=True)


@contextlib.contextmanager
def _quiet_feed():
    from ..forward import datafeed
    old_ref, old_sleep = datafeed.MarketDataService.reference_check, datafeed.time.sleep
    datafeed.MarketDataService.reference_check = lambda self, symbols: {"skipped": "simulation"}; datafeed.time.sleep = lambda s: None
    try:
        yield
    finally:
        datafeed.MarketDataService.reference_check = old_ref; datafeed.time.sleep = old_sleep


def _fake_prod(root: Path) -> Path:
    d = root / "reports_out" / "brando_pa" / "phase8_forward"; d.mkdir(parents=True, exist_ok=True); (root / "config").mkdir(exist_ok=True)
    (d / "events.jsonl").write_text('{"n": 0}\n', encoding="utf-8"); (d / "health.json").write_text("{}", encoding="utf-8")
    (root / "config" / "brando_pa.yaml").write_text("data:\n  own_dir: data_store/brando_pa\nusdt:\n  initial_cash_usdt: 10000\n", encoding="utf-8"); return root


def run(base: Path, since: str = "2026-11-12 00:00", hours: int = 72, faults: bool = False, dashboard_last: bool = True) -> dict:
    base = Path(base); out, feed, prod = base / "out", base / "feed", _fake_prod(base / "prod"); store = BarStore(root=base / "bars", fallback_root=None)
    a = pd.Timestamp(since, tz="UTC"); bad = set(range(24, 30)) if faults else set()
    ex = FakeExchange(BarStore(root=SEED_DIR, fallback_root=None), outage=lambda now: int((now - a).total_seconds() // 3600) in bad)
    baseline = macguard21.snapshot(prod); log = []
    with _quiet_feed():
        for k in range(hours):
            now = a + pd.Timedelta(hours=k, minutes=5); ex.now = now
            with open(prod / "reports_out/brando_pa/phase8_forward/events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"n": k + 1}) + "\n")                                       # Mac 三账户服务照常追加
            if faults and k in range(55, 60):
                continue                                                                        # 电脑睡眠 5 小时：没有运行
            if faults and k == 30:
                orig = forward21.run_once; forward21.run_once = lambda *x, **y: (_ for _ in ()).throw(RuntimeError("simulated crash"))
                try:
                    log.append(service21.tick(now=now, store=store, out_dir=out, feed_dir=feed, sources=[ex], dashboard=False))
                finally:
                    forward21.run_once = orig
                continue
            if faults and k == 40:
                lk = open(out / "service.lock", "a+"); fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
                try:
                    log.append(service21.tick(now=now, store=store, out_dir=out, feed_dir=feed, sources=[ex], dashboard=False))
                finally:
                    fcntl.flock(lk.fileno(), fcntl.LOCK_UN); lk.close()
                continue
            if faults and k == 50:
                p = prod / "reports_out/brando_pa/phase8_forward/events.jsonl"; p.write_text(p.read_text(encoding="utf-8").replace('{"n": 0}', '{"n": -1}'), encoding="utf-8")   # 模拟历史被改写
            log.append(service21.tick(now=now, store=store, out_dir=out, feed_dir=feed, sources=[ex], dashboard=dashboard_last and k == hours - 1))
    res = acceptance21.accept72(a, out, now=a + pd.Timedelta(hours=hours, minutes=10), baseline=baseline, prod_root=prod, hours=hours, require_platform=None)
    res["simulation"] = {"faults_injected": ["第 2 天 00:05–05:05 行情中断（6 次，跨越日线收盘）", "第 31 次运行崩溃", "第 41 次运行遇到锁", "第 51 次运行前 Mac 账本历史被改写", "第 56–60 小时电脑睡眠"] if faults else [], "statuses": pd.Series([r["status"] for r in log]).value_counts().to_dict(),
                         "note": "加速模拟：虚拟时钟与合成行情，只检验服务与验收逻辑，不是前瞻观察，不能替代 Mac 上的真实 72 小时验收"}
    return res


if __name__ == "__main__":
    import sys
    base = Path(sys.argv[1]); R = {}
    for name, f in (("clean", False), ("faults", True)):
        R[name] = run(base / name, faults=f); print(name, R[name]["verdict"], {k: v["ok"] for k, v in R[name]["criteria"].items()}, R[name]["simulation"]["statuses"])
    (base / "sim72.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
