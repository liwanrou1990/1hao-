"""设备注册与编号。

三重身份必须一致，才允许把“编号 N”绑定到一台手机：
1. capture_id：iPhone-use /devices 返回的完整 USB 设备 ID（AVFoundation uniqueID，用于一切动作）；
2. name：手机“关于本机 → 名称”，注册前由操作员改成 IF01…IF30（/devices 的 name 字段）；
3. udid：macOS `system_profiler SPUSBDataType -json` 或 `pymobiledevice3 usbmux list` 枚举到的 USB 序列号/UDID
   （iPhone 6s/7/8 的 USB 序列号即 40 位 UDID）。
蓝牙 host（CBCentral UUID）在 AssistiveTouch 配对并由 iPhone-use 探测匹配后，从 /status.bluetoothHosts[].screen.id 反查写入。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DeviceRecord:
    slot: int
    capture_id: str
    name: str
    udid: str | None = None
    usb_location_id: str | None = None   # system_profiler 的 location_id，对应 hub 物理口
    ble_host: str | None = None          # iPhone-use bluetoothHosts[].host（UUID）
    model: str | None = None
    ios_version: str | None = None
    registered_at: float = field(default_factory=time.time)
    last_verified_at: float | None = None
    notes: str = ""

    @property
    def key(self) -> str:
        return self.capture_id


class RegistryError(RuntimeError):
    pass


class DeviceRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: dict[int, DeviceRecord] = {}
        self.load()

    # ---------- 持久化 ----------
    def load(self) -> None:
        self.records = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            for item in data.get("devices", []):
                rec = DeviceRecord(**item)
                self.records[rec.slot] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "saved_at": time.time(),
                   "devices": [asdict(r) for r in sorted(self.records.values(), key=lambda r: r.slot)]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 查询 ----------
    def by_slot(self, slot: int) -> DeviceRecord:
        if slot not in self.records:
            raise RegistryError(f"编号 {slot} 未注册")
        return self.records[slot]

    def by_capture_id(self, capture_id: str) -> DeviceRecord | None:
        for r in self.records.values():
            if r.capture_id == capture_id:
                return r
        return None

    def slots(self) -> list[int]:
        return sorted(self.records)

    def resolve(self, ref: str | int) -> DeviceRecord:
        """接受编号（int / '7' / 'IF07'）或完整 capture_id。不接受前缀。"""
        if isinstance(ref, int):
            return self.by_slot(ref)
        s = str(ref).strip()
        m = re.fullmatch(r"(?:IF)?(\d{1,2})", s, flags=re.IGNORECASE)
        if m:
            return self.by_slot(int(m.group(1)))
        rec = self.by_capture_id(s)
        if rec is None:
            raise RegistryError(f"'{ref}' 既不是已注册编号，也不是已注册的完整设备 ID（不接受前缀）")
        return rec

    # ---------- 注册 ----------
    def register(self, slot: int, capture_id: str, name: str, udid: str | None = None,
                 usb_location_id: str | None = None, name_pattern: str = r"^IF(\d{2})$",
                 require_udid: bool = True, **extra: Any) -> DeviceRecord:
        if not capture_id:
            raise RegistryError("capture_id 为空，拒绝注册")
        m = re.match(name_pattern, name or "")
        if not m:
            raise RegistryError(f"设备名 '{name}' 不符合 {name_pattern}，请先在手机“关于本机→名称”改为 IF{slot:02d}")
        if int(m.group(1)) != slot:
            raise RegistryError(f"设备名 '{name}' 的编号与要注册的编号 {slot} 不一致，拒绝绑定")
        if require_udid and not udid:
            raise RegistryError("未取得 UDID（system_profiler/usbmux 未枚举到），拒绝注册；可用 --no-udid 跳过（不推荐）")
        other = self.by_capture_id(capture_id)
        if other is not None and other.slot != slot:
            raise RegistryError(f"该设备 ID 已注册为编号 {other.slot}，不能重复绑定到 {slot}")
        for r in self.records.values():
            if udid and r.udid == udid and r.slot != slot:
                raise RegistryError(f"UDID {udid} 已注册为编号 {r.slot}")
        rec = DeviceRecord(slot=slot, capture_id=capture_id, name=name, udid=udid,
                           usb_location_id=usb_location_id, **extra)
        self.records[slot] = rec
        self.save()
        return rec

    def set_ble_host(self, slot: int, host: str | None) -> DeviceRecord:
        rec = self.by_slot(slot)
        for r in self.records.values():
            if host and r.ble_host == host and r.slot != slot:
                raise RegistryError(f"蓝牙 host {host} 已绑定编号 {r.slot}，一个 host 只能对应一台手机")
        rec.ble_host = host
        rec.last_verified_at = time.time()
        self.save()
        return rec

    def unregister(self, slot: int) -> None:
        self.records.pop(slot, None)
        self.save()


# ---------- macOS USB 枚举（只在 Mac 上可用；其他平台返回空列表） ----------

def enumerate_usb_iphones_macos(timeout_s: float = 30) -> list[dict[str, Any]]:
    """用 system_profiler SPUSBDataType -json 枚举 iPhone：返回 [{name, serial_num, location_id, vendor_id, product_id}]。"""
    try:
        out = subprocess.run(["system_profiler", "SPUSBDataType", "-json"], capture_output=True, text=True,
                             timeout=timeout_s, check=False).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return []
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = str(node.get("_name", ""))
            vendor = str(node.get("vendor_id", ""))
            if "iPhone" in name or "apple_vendor_id" in vendor or vendor.startswith("0x05ac"):
                if "iPhone" in name or str(node.get("product_id", "")).lower() in {"0x12a8", "0x12ab"}:
                    found.append({
                        "name": name,
                        "serial_num": node.get("serial_num"),
                        "location_id": node.get("location_id"),
                        "vendor_id": vendor,
                        "product_id": node.get("product_id"),
                    })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def enumerate_usbmux_udids(timeout_s: float = 15) -> list[str]:
    """通过 pymobiledevice3 usbmux list 或 idevice_id -l 取 UDID 列表（二者任一存在即可）。"""
    cmds = [
        ["pymobiledevice3", "usbmux", "list", "--usb", "--no-color"],
        ["idevice_id", "-l"],
    ]
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if p.returncode != 0:
            continue
        text = p.stdout.strip()
        if cmd[0] == "idevice_id":
            return [ln.strip() for ln in text.splitlines() if ln.strip()]
        try:
            items = json.loads(text)
            return [str(i.get("UniqueDeviceID") or i.get("Identifier") or "") for i in items if isinstance(i, dict)]
        except json.JSONDecodeError:
            return re.findall(r"\b[0-9a-f]{40}\b|\b[0-9A-F]{8}-[0-9A-F]{16}\b", text)
    return []
