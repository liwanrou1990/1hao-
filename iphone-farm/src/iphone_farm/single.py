"""单机控制模块：对一台已注册手机执行动作，执行前后做身份一致性校验。

一致性规则（目标 7：设备 ID 与操作目标一致）：
1. 动作请求里 device = 注册表中的完整 capture_id（不允许前缀/名称）；
2. 执行前读取 /status：该 capture_id 的 pointer.host 必须等于注册表里的 ble_host（若已登记）；
   若 host 变了（漂移/交叉绑定），拒绝执行并记录 error；
3. 响应 device 回显必须等于请求 device（client 已强制）；
4. /devices 中该设备的 name 必须仍等于注册表 name（IFxx），否则记 warning 并拒绝（可 --allow-name-drift 放行）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .actions import get_action
from .client import ActionResult, DeviceMismatch, IPhoneUseClient, IPhoneUseError
from .config import FarmConfig
from .events import EventLog
from .locks import DeviceBusy, DeviceLocks
from .registry import DeviceRecord, DeviceRegistry


@dataclass
class DeviceSnapshot:
    present: bool = False
    name: str | None = None
    bluetooth: bool = False
    bluetooth_state: str | None = None
    lock_state: str | None = None
    pointer_ready: bool = False
    pointer_state: str | None = None
    pointer_host: str | None = None
    width: int | None = None
    height: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def snapshot_from_status(status: dict[str, Any], capture_id: str) -> DeviceSnapshot:
    devs = status.get("devices")
    items: list[dict[str, Any]] = []
    if isinstance(devs, list):
        items = devs
    elif isinstance(devs, dict):
        items = list(devs.get("devices", [])) if "devices" in devs else [v for v in devs.values() if isinstance(v, dict)]
    for d in items:
        if d.get("id") == capture_id:
            pointer = d.get("pointer") or {}
            return DeviceSnapshot(
                present=True, name=d.get("name"), bluetooth=bool(d.get("bluetooth")),
                bluetooth_state=d.get("bluetoothState"), lock_state=d.get("lockState"),
                pointer_ready=bool(pointer.get("ready")), pointer_state=pointer.get("state"),
                pointer_host=pointer.get("host"), width=d.get("width"), height=d.get("height"), raw=d)
    return DeviceSnapshot(present=False)


class IdentityError(RuntimeError):
    pass


class SingleController:
    def __init__(self, cfg: FarmConfig, client: IPhoneUseClient, registry: DeviceRegistry,
                 events: EventLog, locks: DeviceLocks):
        self.cfg = cfg
        self.client = client
        self.registry = registry
        self.events = events
        self.locks = locks

    # ---------- 校验 ----------
    def verify_identity(self, rec: DeviceRecord, status: dict[str, Any] | None = None,
                        allow_name_drift: bool = False) -> DeviceSnapshot:
        status = status or self.client.status()
        snap = snapshot_from_status(status, rec.capture_id)
        if not snap.present:
            raise IdentityError(f"编号 {rec.slot}：设备 {rec.capture_id} 当前不在 /status 中（USB 未识别或画面未建立）")
        if snap.name != rec.name and not allow_name_drift:
            raise IdentityError(f"编号 {rec.slot}：设备名漂移 '{snap.name}' != 注册名 '{rec.name}'，拒绝操作")
        if self.cfg.control.verify_host_before_action and rec.ble_host:
            if snap.pointer_host and snap.pointer_host != rec.ble_host:
                raise IdentityError(
                    f"编号 {rec.slot}：蓝牙 host 漂移 {snap.pointer_host} != 注册 {rec.ble_host}（可能交叉绑定），拒绝操作")
        return snap

    # ---------- 执行 ----------
    def run(self, ref: str | int, action: str, params: dict[str, Any] | None = None,
            allow_name_drift: bool = False, require_pointer: bool = True) -> ActionResult:
        rec = self.registry.resolve(ref)
        spec = get_action(action)
        params = dict(params or {})
        t0 = time.time()
        try:
            with self.locks.acquire(rec.capture_id, timeout_s=self.cfg.control.per_device_lock_timeout_s,
                                    owner=f"single:{action}"):
                snap = self.verify_identity(rec, allow_name_drift=allow_name_drift)
                if require_pointer and action not in ("screenshot",) and not snap.pointer_ready:
                    res = ActionResult(ok=False, device=rec.capture_id, action=action, latency_ms=0,
                                       error=f"pointer 未就绪（state={snap.pointer_state}, bt={snap.bluetooth_state}）")
                    self.events.error(res.error or "", device=rec.capture_id, slot=rec.slot, action=action)
                    return res
                res = spec.run(self.client, rec.capture_id, params)
                res.action = action
        except DeviceBusy as e:
            res = ActionResult(ok=False, device=rec.capture_id, action=action, latency_ms=0, error=f"busy: {e}")
        except (IdentityError, DeviceMismatch) as e:
            res = ActionResult(ok=False, device=rec.capture_id, action=action, latency_ms=0, error=f"identity: {e}")
        except (IPhoneUseError, ValueError) as e:
            res = ActionResult(ok=False, device=rec.capture_id, action=action, latency_ms=0, error=str(e))
        res.latency_ms = res.latency_ms or (time.time() - t0) * 1000
        self.events.log("action" if res.ok else "error", f"{action}: {'ok' if res.ok else res.error}",
                        device=rec.capture_id, slot=rec.slot, action=action, params=params,
                        latency_ms=round(res.latency_ms, 1), status=res.status)
        return res

    def screenshot(self, ref: str | int, out_path: str | None = None, max_edge: int | None = 1200) -> dict[str, Any]:
        rec = self.registry.resolve(ref)
        data, headers, latency = self.client.screenshot(rec.capture_id, max_edge=max_edge)
        if out_path:
            from pathlib import Path
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        info = {"slot": rec.slot, "device": rec.capture_id, "bytes": len(data), "latency_ms": round(latency, 1),
                "screen": (headers.get("X-Screen-Width"), headers.get("X-Screen-Height")), "path": out_path}
        self.events.log("action", "screenshot", device=rec.capture_id, slot=rec.slot, **info)
        return info
