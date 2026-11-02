"""iPhone-use HTTP API 客户端（对应 YueChen-C/iPhone-use v1.0.x，docs/user-guide.md#http-api）。

接口要点（来自源码 ControlAPI.swift / DeviceRegistry.swift 调研）：
- 设备选择：POST 用 body.device，GET 用 ?device=；服务端按 id 精确 → 名称精确 → id 前缀 解析。
  本客户端只允许传“完整 id”，并校验响应 device 回显，避免前缀/同名误选。
- 动作端点：/tap /swipe /move /nudge /type /key /home /power /match /pair /calibration/auto /calibration/reload
- 查询端点：/status /devices /config /screenshot /viewer
- 错误：404 路径错误；409 pointerBusy（窗口拖拽/校准中）；503 pointer 未就绪或无画面；其余为 400/500 文本错误。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class IPhoneUseError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class DeviceMismatch(IPhoneUseError):
    """响应回显的 device 与请求的设备 ID 不一致：目标不一致，必须中止。"""


@dataclass
class ActionResult:
    ok: bool
    device: str
    action: str
    latency_ms: float
    status: int | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class IPhoneUseClient:
    def __init__(self, base_url: str = "http://127.0.0.1:7390", timeout_s: float = 10,
                 action_timeout_s: float = 20, require_echo_match: bool = True,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.require_echo_match = require_echo_match
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)
        self._action_timeout = action_timeout_s

    def close(self) -> None:
        self._http.close()

    # ---------- 查询 ----------
    def status(self) -> dict[str, Any]:
        r = self._http.get("/status")
        self._raise_for(r)
        return r.json()

    def devices(self) -> list[dict[str, Any]]:
        r = self._http.get("/devices")
        self._raise_for(r)
        data = r.json()
        return list(data.get("devices", []))

    def capture_status(self) -> dict[str, Any]:
        r = self._http.get("/devices")
        self._raise_for(r)
        return dict(r.json().get("capture", {}))

    def config(self) -> dict[str, Any]:
        r = self._http.get("/config")
        self._raise_for(r)
        return r.json()

    def screenshot(self, device: str, fmt: str = "jpeg", max_edge: int | None = None) -> tuple[bytes, httpx.Headers, float]:
        """返回 (图像字节, 响应头, 延迟 ms)。响应头含 X-Device / X-Screen-Width / X-Screen-Height。"""
        self._require_device(device)
        params: dict[str, Any] = {"device": device}
        if fmt == "png":
            params["format"] = "png"
        if max_edge:
            params["maxEdge"] = int(max_edge)
        t0 = time.perf_counter()
        r = self._http.get("/screenshot", params=params, timeout=self._action_timeout)
        latency = (time.perf_counter() - t0) * 1000
        self._raise_for(r)
        echoed = r.headers.get("X-Device")
        if self.require_echo_match and echoed and echoed != device:
            raise DeviceMismatch(f"截图回显设备 {echoed} != 请求设备 {device}", payload=dict(r.headers.items()))
        return r.content, r.headers, latency

    # ---------- 动作（每个动作都必须带完整设备 ID） ----------
    def tap(self, device: str, x: float, y: float, fraction: bool = True, hold: float | None = None) -> ActionResult:
        body: dict[str, Any] = {"x": x, "y": y, "fraction": fraction}
        if hold is not None:
            body["hold"] = hold
        return self._action("tap", device, body)

    def swipe(self, device: str, points: list[tuple[float, float]], duration: float = 0.3,
              fraction: bool = True) -> ActionResult:
        if len(points) < 2:
            raise ValueError("swipe 至少需要两个点")
        body = {"points": [{"x": px, "y": py} for px, py in points], "duration": duration, "fraction": fraction}
        return self._action("swipe", device, body)

    def type_text(self, device: str, text: str) -> ActionResult:
        if not text.isascii():
            raise ValueError("iPhone-use /type 仅支持 ASCII 文本；中文请切换手机输入法或使用通用剪贴板")
        return self._action("type", device, {"text": text})

    def key(self, device: str, key: str, modifiers: list[str] | None = None) -> ActionResult:
        body: dict[str, Any] = {"key": key}
        if modifiers:
            body["modifiers"] = list(modifiers)
        return self._action("key", device, body)

    def home(self, device: str) -> ActionResult:
        return self._action("home", device, {})

    def power(self, device: str) -> ActionResult:
        # 文档明确：不保证锁定的幂等指令，调用方必须先看画面再决定是否重试
        return self._action("power", device, {})

    def match(self, device: str | None = None, force: bool = True, probe: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {"force": force, "probe": probe}
        if device:
            body["device"] = device
        r = self._http.post("/match", json=body, timeout=self._action_timeout)
        self._raise_for(r)
        return r.json()

    def pair(self, device: str, host: str) -> dict[str, Any]:
        self._require_device(device)
        r = self._http.post("/pair", json={"device": device, "host": host}, timeout=self._action_timeout)
        self._raise_for(r)
        return r.json()

    def calibration_auto(self, device: str | None = None, verify_only: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"verifyOnly": verify_only}
        if device:
            body["device"] = device
        r = self._http.post("/calibration/auto", json=body, timeout=max(self._action_timeout, 60))
        self._raise_for(r)
        return r.json()

    def calibration_reload(self, device: str) -> dict[str, Any]:
        self._require_device(device)
        r = self._http.post("/calibration/reload", json={"device": device}, timeout=self._action_timeout)
        self._raise_for(r)
        return r.json()

    # ---------- 内部 ----------
    @staticmethod
    def _require_device(device: str | None) -> None:
        if not device or not isinstance(device, str) or not device.strip():
            raise ValueError("每个动作必须指定完整的真实设备 ID（capture_id）；拒绝空设备或默认设备")

    def _action(self, name: str, device: str, body: dict[str, Any]) -> ActionResult:
        self._require_device(device)
        payload = dict(body)
        payload["device"] = device
        t0 = time.perf_counter()
        try:
            r = self._http.post(f"/{name}", json=payload, timeout=self._action_timeout)
        except httpx.HTTPError as e:
            return ActionResult(ok=False, device=device, action=name,
                                latency_ms=(time.perf_counter() - t0) * 1000, error=f"http: {e}")
        latency = (time.perf_counter() - t0) * 1000
        data: dict[str, Any]
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"raw": r.text}
        if r.status_code >= 400:
            return ActionResult(ok=False, device=device, action=name, latency_ms=latency,
                                status=r.status_code, error=str(data.get("error") or data.get("raw") or r.text),
                                payload=data)
        echoed = data.get("device")
        if self.require_echo_match and echoed is not None and echoed != device:
            raise DeviceMismatch(f"动作 {name} 回显设备 {echoed} != 请求设备 {device}", status=r.status_code,
                                 payload=data)
        return ActionResult(ok=bool(data.get("ok", True)), device=device, action=name, latency_ms=latency,
                            status=r.status_code, payload=data)

    @staticmethod
    def _raise_for(r: httpx.Response) -> None:
        if r.status_code >= 400:
            try:
                payload = r.json()
            except ValueError:
                payload = r.text
            raise IPhoneUseError(f"HTTP {r.status_code}: {payload}", status=r.status_code, payload=payload)
