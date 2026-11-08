"""24 小时前瞻服务（第九阶段）：独立后台进程，由 launchd / systemd / Docker 管理。

主循环（interval_s）：行情更新（来源回退、只用已收盘 K 线、质量门控）→ 账户推进（挂单补成交、新日线信号、错过信号标记）→ 心跳 health.json →
UTC 日切时生成日报 → 容量检查 → 告警（alerts.jsonl + 可选 webhook，只从环境变量读取）。SIGTERM/SIGINT 优雅退出；任何异常只记录并进入下一轮，不退出。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json

from ..config import cfg, report_dir
from ..data.sources import utcnow
from ..trendsys.paper import _append
from .accounts import ForwardRunner
from .datafeed import MarketDataService
from .report import daily_report


class ForwardService:
    def __init__(self, c: dict | None = None, log_dir: Path | None = None, store=None, feed: MarketDataService | None = None, runner: ForwardRunner | None = None, clock=None):
        self.c = c or cfg(); fc = self.c.get("forward", {}); self.fc = fc
        self.dir = Path(log_dir or (report_dir(self.c) / "phase8_forward")); self.dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner or ForwardRunner(self.c, log_dir=self.dir, store=store)
        self.feed = feed or MarketDataService(self.c, store=self.runner.store)
        self.clock = clock or utcnow
        self.symbols = list(fc.get("symbols", ["BTCUSDT", "ETHUSDT"])); self.tfs = list(fc.get("tfs", ["1h", "1d"]))
        self.interval = int(fc.get("interval_s", 600)); self.health_path = self.dir / "health.json"; self.alerts_path = self.dir / "alerts.jsonl"; self.daily_dir = self.dir / "daily"
        self.log = logging.getLogger("brando_pa.forward.service")
        if not self.log.handlers:
            lf = self.dir / "service.log"; h = RotatingFileHandler(lf, maxBytes=int(fc.get("log", {}).get("max_bytes", 10 * 1024 * 1024)), backupCount=int(fc.get("log", {}).get("backups", 5)))
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); self.log.addHandler(h); self.log.setLevel(logging.INFO)
        self._stop = False; self.last_report_day = None; self.n_cycles = 0; self.last_ok_cycle_at = None; self.consecutive_failures = 0; self._lock_fh = None
        if self.health_path.exists():
            try:
                self.last_report_day = json.loads(self.health_path.read_text()).get("last_report_day")
            except Exception:  # noqa: BLE001
                pass

    # ---- 单实例锁（同一目录同一时间只能有一个模拟实例）----
    def acquire_lock(self) -> bool:
        import fcntl
        self._lock_fh = open(self.dir / "service.lock", "a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_fh.close(); self._lock_fh = None; return False
        self._lock_fh.seek(0); self._lock_fh.truncate(); self._lock_fh.write(str(os.getpid())); self._lock_fh.flush(); return True

    def release_lock(self) -> None:
        if self._lock_fh:
            import fcntl
            fcntl.flock(self._lock_fh, fcntl.LOCK_UN); self._lock_fh.close(); self._lock_fh = None

    # ---- 启动完整性检查 ----
    def startup_check(self) -> dict:
        """启动时：事件链校验、状态文件可载入且与账本一致、行情仓可读与新鲜度、时钟偏差；结果写 startup_check.json，任何一项失败 → 告警（服务仍启动但不新增订单直到数据正常）。"""
        out = {"checked_at_utc": str(self.clock()), "chains": self.runner.verify_all(), "accounts": {}, "data": {}, "clock_skew_s": None, "ok": True}
        for n, a in self.runner.accounts.items():
            try:
                st = a.state; L = a.ledger("cost"); out["accounts"][n] = {"state_loaded": True, "forward_start_utc": st.get("forward_start_utc"), "last_daily_ts": st.get("last_daily_ts"), "pending": len(st.get("pending", [])), "processed_orders": len(st.get("processed_orders", [])), "cash_cost": L.cash, "holdings_cost": {s: L.qty(s) for s in L.lots}, "n_events": sum(1 for _ in open(a.events)) if a.events.exists() else 0}
                if L.cash < -1e-9 or any(L.qty(s) < 0 for s in L.lots):
                    out["ok"] = False; out["accounts"][n]["error"] = "negative cash/holdings"
            except Exception as e:  # noqa: BLE001
                out["ok"] = False; out["accounts"][n] = {"state_loaded": False, "error": f"{type(e).__name__}: {e}"}
        try:
            bars = self.runner.load_bars(); chk = self.runner.data_check(bars, self.clock()); out["data"] = chk
        except Exception as e:  # noqa: BLE001
            out["ok"] = False; out["data"] = {"error": f"{type(e).__name__}: {e}"}
        out["clock_skew_s"] = self.clock_skew()
        if not all(v["ok"] for v in out["chains"].values()):
            out["ok"] = False
        atomic_write_json(self.dir / "startup_check.json", out, indent=1, default=str)
        if not out["ok"]:
            self.alert("startup_check_failed", json.dumps({k: v for k, v in out.items() if k != "accounts"}, default=str)[:500])
        self.log.info("startup_check ok=%s", out["ok"]); return out

    def clock_skew(self) -> float | None:
        """本机 UTC 与交易所服务器时间偏差（秒）；不可达时 None。"""
        try:
            import httpx
            r = httpx.get("https://www.okx.com/api/v5/public/time", timeout=8); srv = int(r.json()["data"][0]["ts"]) / 1000
            return float(time.time() - srv)
        except Exception:  # noqa: BLE001
            return None

    # ---- 告警 ----
    def alert(self, kind: str, msg: str, **kw) -> None:
        self.log.warning("ALERT %s %s", kind, msg); _append(self.alerts_path, {"kind": kind, "msg": msg, **kw})
        env = self.fc.get("health", {}).get("alert_webhook_env", "BRANDO_ALERT_WEBHOOK"); url = os.environ.get(env, "")
        if url:
            try:
                import httpx; httpx.post(url, json={"text": f"[brando_pa forward] {kind}: {msg}"}, timeout=10)
            except Exception as e:  # noqa: BLE001
                self.log.warning("webhook failed: %s", e)

    # ---- 容量检查 ----
    def storage_check(self) -> dict:
        hc = self.fc.get("health", {}); free_mb = shutil.disk_usage(self.dir).free / 1e6; size_mb = sum(p.stat().st_size for p in self.dir.rglob("*") if p.is_file()) / 1e6
        out = {"free_disk_mb": round(free_mb), "log_dir_mb": round(size_mb, 1), "ok": free_mb >= float(hc.get("min_free_disk_mb", 500)) and size_mb <= float(hc.get("max_log_dir_mb", 2000))}
        if not out["ok"]:
            self.alert("storage", f"free {free_mb:.0f} MB, log dir {size_mb:.0f} MB", **out)
        return out

    # ---- 一轮 ----
    def cycle(self, now: pd.Timestamp | None = None) -> dict:
        now = now or self.clock(); t0 = time.time(); out = {"now": str(now)}
        try:
            st = self.feed.update(self.symbols, self.tfs, now=now)
            out["data"] = {"ok": st.ok, "source": st.source, "stale": st.stale, "quality_fault": st.quality_fault, "quality_note": st.quality_note, "errors": st.errors[-3:], "source_switch": st.source_switch}
            if not st.ok:
                self.alert("data_unavailable", "全部行情来源失败；账户不新增订单", errors=st.errors[-3:])
            if st.source_switch:
                self.alert("source_switch", f"{st.source_switch['from']} → {st.source_switch['to']}", overlap=st.source_switch.get("overlap"))
            if st.quality_fault:
                self.alert("data_quality", st.quality_note)
        except Exception as e:  # noqa: BLE001
            st = None; out["data"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}; self.alert("data_exception", str(e))
        try:
            r = self.runner.run_once(now=now, quality_fault=bool(st and st.quality_fault), quality_note=(st.quality_note if st else ""))
            out["accounts"] = {n: {"cost_equity": a["nav"]["cost"]["equity_usdt"], "ideal_equity": a["nav"]["ideal"]["equity_usdt"], "risk_state": a["risk_state"], "fills": len(a["fills"]), "pending": len(a["pending"]), "last_daily_ts": a["last_daily_ts"]} for n, a in r.get("accounts", {}).items()}
            out["data_check"] = r.get("data_check")
            if r.get("data_check") and not r["data_check"]["ok"]:
                self.alert("data_check", "; ".join(r["data_check"]["issues"] + r["data_check"]["anomalies"]))
            self.last_ok_cycle_at = str(now); self.consecutive_failures = 0 if (st and st.ok) else self.consecutive_failures + 1
        except Exception as e:  # noqa: BLE001
            out["accounts_error"] = f"{type(e).__name__}: {e}"; self.consecutive_failures += 1; self.alert("runner_exception", str(e)); self.log.exception("runner failed")
        if self.consecutive_failures >= 3:
            self.alert("consecutive_failures", f"{self.consecutive_failures} 轮连续失败（行情或运行）")
        # 日报：UTC 日切后第一轮生成前一日报告
        day = str((now - pd.Timedelta(days=1)).date())
        if self.last_report_day != day and now.hour >= 0:
            try:
                ds = json.loads(self.feed.status_path.read_text()) if self.feed.status_path.exists() else None
                daily_report(self.dir, day, ds, {"n_cycles": self.n_cycles, "last_ok_cycle_at": self.last_ok_cycle_at}, self.daily_dir); self.last_report_day = day; out["daily_report"] = day
            except Exception as e:  # noqa: BLE001
                self.alert("daily_report_failed", str(e))
        try:
            from .report import leaderboard
            leaderboard(self.dir)
        except Exception as e:  # noqa: BLE001
            self.alert("leaderboard_failed", str(e))
        out["storage"] = self.storage_check(); self.n_cycles += 1; out["elapsed_s"] = round(time.time() - t0, 1)
        self.heartbeat(out); self.log.info("cycle %s", json.dumps({k: v for k, v in out.items() if k != "data_check"}, default=str, ensure_ascii=False)[:1500])
        return out

    def heartbeat(self, last: dict) -> None:
        atomic_write_json(self.health_path, {"heartbeat_utc": str(self.clock()), "pid": os.getpid(), "n_cycles": self.n_cycles, "last_ok_cycle_at": self.last_ok_cycle_at, "consecutive_failures": self.consecutive_failures, "last_report_day": self.last_report_day, "interval_s": self.interval, "last_cycle": last, "real_trading": "disabled (no implementation)"}, indent=1, default=str)

    def health(self) -> dict:
        """外部健康检查：心跳年龄、数据新鲜度、哈希链、容量。返回 ok 与原因列表。"""
        hc = self.fc.get("health", {}); reasons = []
        if not self.health_path.exists():
            return {"ok": False, "reasons": ["no heartbeat file"]}
        h = json.loads(self.health_path.read_text()); age = (self.clock() - pd.Timestamp(h["heartbeat_utc"])).total_seconds()
        if age > float(hc.get("max_heartbeat_age_s", 1800)):
            reasons.append(f"heartbeat age {age:.0f}s")
        lc = h.get("last_cycle", {}); d = lc.get("data", {})
        if not d.get("ok"):
            reasons.append("data update failed")
        if d.get("stale"):
            reasons.append("data stale")
        if d.get("quality_fault"):
            reasons.append("data quality fault")
        if lc.get("accounts_error"):
            reasons.append("runner exception")
        if lc.get("storage") and not lc["storage"].get("ok"):
            reasons.append("storage")
        chains = self.runner.verify_all()
        if not all(v["ok"] for v in chains.values()):
            reasons.append("hash chain broken")
        if int(h.get("consecutive_failures", 0)) >= 3:
            reasons.append(f"consecutive failures {h.get('consecutive_failures')}")
        # 进程存活（pid 来自心跳；锁文件被持有 = 实例在运行）
        alive = False
        try:
            os.kill(int(h.get("pid", -1)), 0); alive = True
        except Exception:  # noqa: BLE001
            alive = False
        # 最新 K 线年龄
        bar_age = {}
        try:
            bars = self.runner.load_bars()
            for s, bb in bars.items():
                for tf, b in bb.items():
                    if not b.empty:
                        bar_age[f"{s}_{tf}_h"] = round((self.clock() - (b["ts"].iloc[-1] + pd.Timedelta(seconds={"1h": 3600, "1d": 86400}[tf]))).total_seconds() / 3600, 2)
        except Exception as e:  # noqa: BLE001
            bar_age["error"] = str(e)
        accounts = {n: {"cost_equity": a["nav"]["cost"]["equity_usdt"], "ideal_equity": a["nav"]["ideal"]["equity_usdt"], "risk_state": a["risk_state"], "last_daily_ts": a["last_daily_ts"], "pending": len(a.get("pending", []))} for n, a in (json.loads(self.runner.status_path.read_text()).get("accounts", {}) if self.runner.status_path.exists() else {}).items()}
        log_mtime = None
        lf = self.dir / "service.log"
        if lf.exists():
            log_mtime = str(pd.Timestamp(lf.stat().st_mtime, unit="s", tz="UTC"))
        return {"ok": not reasons, "reasons": reasons, "heartbeat_age_s": age, "process_alive": alive, "pid": h.get("pid"), "latest_bar_age_hours": bar_age, "last_ok_cycle_at": h.get("last_ok_cycle_at"), "consecutive_failures": h.get("consecutive_failures", 0), "data_source": d.get("source"), "data_stale": d.get("stale"), "quality_fault": d.get("quality_fault"), "accounts": accounts, "latest_log_utc": log_mtime, "n_cycles": h.get("n_cycles"), "chains": chains, "real_trading": "disabled (no implementation)"}

    def run(self, max_cycles: int | None = None) -> None:
        def _stop(*_):
            self._stop = True; self.log.info("stop signal received")
        try:
            signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
        except ValueError:
            pass
        if not self.acquire_lock():
            self.log.error("another instance holds %s; exiting", self.dir / "service.lock"); print(f"[forward-service] another instance is running (lock {self.dir / 'service.lock'}); exit", flush=True); return
        self.log.info("service start interval=%ss dir=%s pid=%s", self.interval, self.dir, os.getpid()); k = 0
        self.startup_check()
        while not self._stop and (max_cycles is None or k < max_cycles):
            self.cycle(); k += 1
            for _ in range(self.interval):
                if self._stop or (max_cycles is not None and k >= max_cycles):
                    break
                time.sleep(1)
        self.log.info("service stopped after %s cycles", k); self.release_lock()
