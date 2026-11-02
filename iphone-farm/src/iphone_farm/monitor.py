"""故障诊断与恢复模块：轮询 /devices + /status，为每台已注册手机判定状态，记录错误，按策略恢复。

状态机（从严重到正常）：
  USB_ABSENT       /status 里没有该 capture_id（USB 未识别 / 画面设备未出现）
  NO_FRAME         设备在列表但 lockState/画面显示无帧（paused）
  BLE_UNPAIRED     bluetooth=false（AssistiveTouch 未连 Mac）
  BLE_UNMATCHED    蓝牙 host 已连但未与该屏幕匹配（bluetoothState=unmatched）
  HOST_DRIFT       pointer.host 与注册表 ble_host 不一致（交叉绑定风险）
  POINTER_NOT_READY pointer.ready=false（校准/订阅未完成）
  LOCKED           画面检测到锁屏（iPhone-use 的 OCR 启发式，仅提示）
  OK
恢复动作（均在设备互斥锁内执行，避免与广播动作交叉）：
  BLE_UNMATCHED → POST /match {force:true, probe:true}
  注册 host 出现在 unmatchedHosts → POST /pair {device, host}
  POINTER_NOT_READY 持续 → POST /calibration/auto {device}
  USB_ABSENT / NO_FRAME → 只能等待与告警（需要人工检查线缆/信任/解锁）；可选重启 iPhoneUse.app（默认关闭）
"""
from __future__ import annotations

import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .client import IPhoneUseClient, IPhoneUseError
from .config import FarmConfig
from .events import EventLog
from .locks import DeviceBusy, DeviceLocks
from .registry import DeviceRecord, DeviceRegistry
from .single import DeviceSnapshot, snapshot_from_status

STATES = ["INIT", "USB_ABSENT", "NO_FRAME", "BLE_UNPAIRED", "BLE_UNMATCHED", "HOST_DRIFT", "POINTER_NOT_READY", "LOCKED", "OK"]


def classify(snap: DeviceSnapshot, rec: DeviceRecord) -> str:
    if not snap.present:
        return "USB_ABSENT"
    if snap.lock_state in ("paused", "noFrame", "no-frame"):
        return "NO_FRAME"
    if not snap.bluetooth:
        return "BLE_UNPAIRED"
    if (snap.bluetooth_state or "").lower() in ("unmatched", "unverified"):
        return "BLE_UNMATCHED"
    if rec.ble_host and snap.pointer_host and snap.pointer_host != rec.ble_host:
        return "HOST_DRIFT"
    if not snap.pointer_ready:
        return "POINTER_NOT_READY"
    if (snap.lock_state or "").lower() in ("locked", "lockscreen", "lock-screen"):
        return "LOCKED"
    return "OK"


@dataclass
class DeviceHealth:
    slot: int
    device: str
    state: str = "INIT"
    since: float = field(default_factory=time.time)
    last_ok: float | None = None
    transitions: int = 0
    ble_drops: int = 0
    recoveries: int = 0
    recovery_times: deque = field(default_factory=lambda: deque(maxlen=50))
    last_error: str | None = None


class Monitor:
    def __init__(self, cfg: FarmConfig, client: IPhoneUseClient, registry: DeviceRegistry,
                 events: EventLog, locks: DeviceLocks):
        self.cfg = cfg
        self.client = client
        self.registry = registry
        self.events = events
        self.locks = locks
        self.health: dict[int, DeviceHealth] = {}
        self._last_app_restart = 0.0
        self.polls = 0
        self.poll_errors = 0

    # ---------- 单次巡检 ----------
    def poll_once(self, recover: bool = True) -> dict[int, DeviceHealth]:
        self.polls += 1
        try:
            status = self.client.status()
        except (IPhoneUseError, Exception) as e:  # noqa: BLE001
            self.poll_errors += 1
            self.events.error(f"iPhone-use 服务不可达：{e}", api_down=True)
            for rec in self.registry.records.values():
                self._transition(rec, "USB_ABSENT", "api_down")
            return self.health
        unmatched = set(status.get("unmatchedHosts") or [])
        for rec in self.registry.records.values():
            snap = snapshot_from_status(status, rec.capture_id)
            state = classify(snap, rec)
            self._transition(rec, state, snap.pointer_state or "")
            if recover and state != "OK":
                self._maybe_recover(rec, state, snap, unmatched)
        return self.health

    def _transition(self, rec: DeviceRecord, state: str, detail: str) -> None:
        h = self.health.setdefault(rec.slot, DeviceHealth(slot=rec.slot, device=rec.capture_id))
        now = time.time()
        if state == "OK":
            h.last_ok = now
        if state != h.state:
            prev = h.state
            h.transitions += 1
            if prev == "OK" and state in ("BLE_UNPAIRED", "BLE_UNMATCHED", "HOST_DRIFT", "POINTER_NOT_READY"):
                h.ble_drops += 1
            h.state = state
            h.since = now
            self.events.log("state", f"{prev} → {state} ({detail})", device=rec.capture_id, slot=rec.slot,
                            prev=prev, state=state)
            if state != "OK":
                h.last_error = f"{state}:{detail}"
                self.events.error(f"状态 {state} ({detail})", device=rec.capture_id, slot=rec.slot, state=state)

    # ---------- 恢复 ----------
    def _recovery_budget_ok(self, h: DeviceHealth) -> bool:
        now = time.time()
        recent = [t for t in h.recovery_times if now - t < 600]
        return len(recent) < self.cfg.monitor.recovery.max_recovery_attempts_per_10min

    def _maybe_recover(self, rec: DeviceRecord, state: str, snap: DeviceSnapshot, unmatched: set[str]) -> None:
        rc = self.cfg.monitor.recovery
        h = self.health[rec.slot]
        if not self._recovery_budget_ok(h):
            return
        step: str | None = None
        try:
            with self.locks.acquire(rec.capture_id, timeout_s=1, owner="monitor"):
                if state in ("BLE_UNMATCHED", "HOST_DRIFT") and rec.ble_host and rec.ble_host in unmatched and rc.enable_pair:
                    step = "pair"
                    self.client.pair(rec.capture_id, rec.ble_host)
                elif state in ("BLE_UNMATCHED", "HOST_DRIFT") and rc.enable_match:
                    step = "match"
                    self.client.match(rec.capture_id, force=True, probe=True)
                elif state == "POINTER_NOT_READY" and rc.enable_calibration and time.time() - h.since > 10:
                    step = "calibration_auto"
                    self.client.calibration_auto(rec.capture_id)
                elif state in ("USB_ABSENT", "NO_FRAME"):
                    waited = time.time() - h.since
                    limit = (self.cfg.monitor.usb_absent_alert_after_s if state == "USB_ABSENT"
                             else self.cfg.monitor.no_frame_alert_after_s)
                    if waited > limit:
                        self.events.error(f"{state} 已持续 {waited:.0f}s，需人工检查线缆/信任/解锁", device=rec.capture_id,
                                          slot=rec.slot, alert=True)
                        if rc.enable_app_restart and time.time() - self._last_app_restart > rc.app_restart_cooldown_s:
                            step = "app_restart"
                            self.restart_app()
        except DeviceBusy:
            return
        except IPhoneUseError as e:
            self.events.error(f"恢复步骤 {step} 失败：{e}", device=rec.capture_id, slot=rec.slot, step=step)
            return
        if step:
            h.recoveries += 1
            h.recovery_times.append(time.time())
            self.events.log("recovery", f"执行恢复 {step}（状态 {state}）", device=rec.capture_id, slot=rec.slot, step=step)

    def restart_app(self) -> None:
        """最后手段：重启 iPhoneUse.app（会中断全部设备窗口/服务，默认关闭）。"""
        self._last_app_restart = time.time()
        self.events.log("recovery", "重启 iPhoneUse.app（全局中断）", step="app_restart")
        try:
            subprocess.run(["pkill", "-x", "iphone-use"], check=False, timeout=10)
            time.sleep(2)
            subprocess.run(["open", "-a", "iPhoneUse", "--args", "--resident-quiet"], check=False, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.events.error(f"重启失败：{e}", step="app_restart")

    # ---------- 循环 ----------
    def run(self, duration_s: float | None = None, recover: bool = True,
            on_tick: Any | None = None) -> dict[int, DeviceHealth]:
        t_end = time.time() + duration_s if duration_s else None
        while True:
            self.poll_once(recover=recover)
            if on_tick:
                on_tick(self)
            if t_end is not None and time.time() >= t_end:
                break
            time.sleep(self.cfg.monitor.poll_interval_s)
        return self.health

    def summary(self) -> list[dict[str, Any]]:
        out = []
        for slot in sorted(self.health):
            h = self.health[slot]
            out.append({"slot": slot, "device": h.device, "state": h.state, "since_s": round(time.time() - h.since, 1),
                        "transitions": h.transitions, "ble_drops": h.ble_drops, "recoveries": h.recoveries,
                        "last_error": h.last_error})
        return out
