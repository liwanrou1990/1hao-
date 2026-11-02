"""多机广播控制：对一组设备并行执行同一动作，每台单独指定真实设备 ID、单独加锁、单独记录结果。

安全规则：
- 只允许 CATALOG 中 safe_for_broadcast=True 且在 farm.yaml broadcast.allowed_actions 内的动作；
- 短信类（sms_*）与 power 永远禁止广播；
- max_parallel 限制并发，stagger_ms 错开发送（iPhone-use 的 HID 待发队列是全局 FIFO，
  一台手机的蓝牙背压会阻塞后面所有报文，所以并发不宜太大）。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any

from .actions import get_action
from .client import ActionResult
from .config import FarmConfig
from .registry import DeviceRecord, DeviceRegistry
from .single import SingleController


class BroadcastForbidden(RuntimeError):
    pass


@dataclass
class BroadcastReport:
    action: str
    params: dict[str, Any]
    targets: list[int]
    started_at: float
    finished_at: float = 0.0
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.get("ok"))

    @property
    def fail_count(self) -> int:
        return len(self.results) - self.ok_count

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok_count"] = self.ok_count
        d["fail_count"] = self.fail_count
        d["duration_s"] = round(self.finished_at - self.started_at, 3)
        return d


def select_targets(registry: DeviceRegistry, groups: dict[str, list[int]], group: str | None = None,
                   slots: list[int] | None = None) -> list[DeviceRecord]:
    chosen: list[int] = []
    if group:
        if group not in groups:
            raise KeyError(f"分组 '{group}' 不存在，可用：{', '.join(sorted(groups))}")
        chosen.extend(groups[group])
    if slots:
        chosen.extend(slots)
    seen: set[int] = set()
    out: list[DeviceRecord] = []
    missing: list[int] = []
    for s in chosen:
        if s in seen:
            continue
        seen.add(s)
        try:
            out.append(registry.by_slot(s))
        except Exception:
            missing.append(s)
    if missing:
        raise KeyError(f"以下编号未注册，拒绝广播：{missing}")
    if not out:
        raise KeyError("没有选中任何设备")
    return out


class Broadcaster:
    def __init__(self, cfg: FarmConfig, single: SingleController):
        self.cfg = cfg
        self.single = single

    def check_allowed(self, action: str) -> None:
        spec = get_action(action)
        bc = self.cfg.broadcast
        if action in bc.forbidden_in_broadcast or not spec.safe_for_broadcast:
            raise BroadcastForbidden(f"动作 '{action}' 禁止广播（短信/电源类只能单机执行）")
        if action not in bc.allowed_actions:
            raise BroadcastForbidden(f"动作 '{action}' 不在 farm.yaml broadcast.allowed_actions 白名单内")

    def run(self, targets: list[DeviceRecord], action: str, params: dict[str, Any] | None = None,
            dry_run: bool = False) -> BroadcastReport:
        self.check_allowed(action)
        params = dict(params or {})
        rep = BroadcastReport(action=action, params=params, targets=[t.slot for t in targets], started_at=time.time())
        if dry_run:
            for t in targets:
                rep.results.append({"slot": t.slot, "device": t.capture_id, "ok": True, "dry_run": True})
            rep.finished_at = time.time()
            return rep

        stagger = self.cfg.broadcast.stagger_ms / 1000.0

        def work(idx: int, rec: DeviceRecord) -> dict[str, Any]:
            if stagger:
                time.sleep(stagger * (idx % max(1, self.cfg.broadcast.max_parallel)))
            res: ActionResult = self.single.run(rec.slot, action, params)
            return {"slot": rec.slot, "device": rec.capture_id, "ok": res.ok, "error": res.error,
                    "latency_ms": round(res.latency_ms, 1), "status": res.status}

        with ThreadPoolExecutor(max_workers=max(1, self.cfg.broadcast.max_parallel)) as ex:
            futs = {ex.submit(work, i, rec): rec for i, rec in enumerate(targets)}
            for fut in as_completed(futs):
                rec = futs[fut]
                try:
                    rep.results.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    rep.results.append({"slot": rec.slot, "device": rec.capture_id, "ok": False, "error": repr(e)})
        rep.results.sort(key=lambda r: r["slot"])
        rep.finished_at = time.time()
        self.single.events.log("broadcast", f"{action}: ok={rep.ok_count} fail={rep.fail_count}",
                               targets=rep.targets, params=params, duration_s=round(rep.finished_at - rep.started_at, 3))
        return rep
