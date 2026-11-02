"""iPhone-use API 模拟服务器（仅用于离线验证群控脚本本身；一切结果标记 SIMULATED_MOCK）。

模拟了 /status /devices /config /screenshot /tap /swipe /move /type /key /home /power /match /pair
/calibration/auto 的字段形状（按 iPhone-use v1.0.x 源码调研整理），并提供故障注入：
  POST /_mock/disconnect {device}      模拟 USB 拔出
  POST /_mock/reconnect  {device}      模拟 USB 插回
  POST /_mock/ble_drop   {device}      模拟蓝牙断开（bluetooth=false）
  POST /_mock/ble_unmatch{device}      模拟蓝牙已连但未匹配（host 进入 unmatchedHosts）
  POST /_mock/reset
"""
from __future__ import annotations

import struct
import time
import uuid
import zlib
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class MockDevice:
    def __init__(self, idx: int, name: str | None = None):
        self.id = f"MOCK{idx:02d}-" + uuid.uuid5(uuid.NAMESPACE_DNS, f"mock-{idx}").hex.upper()[:32]
        self.name = name or f"IF{idx:02d}"
        self.present = True
        self.bluetooth = True
        self.host = str(uuid.uuid5(uuid.NAMESPACE_URL, f"host-{idx}")).upper()
        self.matched = True
        self.pointer_ready = True
        self.width, self.height = 750, 1334
        self.actions: list[dict[str, Any]] = []
        self.lock_state = "unlocked"


class MockState:
    def __init__(self, n: int = 3, latency_ms: float = 0.0):
        self.devices: dict[str, MockDevice] = {}
        self.latency_ms = latency_ms
        self.reset(n)

    def reset(self, n: int | None = None) -> None:
        n = n if n is not None else len(self.devices) or 3
        self.devices = {}
        for i in range(1, n + 1):
            d = MockDevice(i)
            self.devices[d.id] = d

    def visible(self) -> list[MockDevice]:
        return [d for d in self.devices.values() if d.present]

    def find(self, q: str | None) -> MockDevice:
        vis = self.visible()
        if not q:
            if not vis:
                raise LookupError("no screen")
            if len(vis) > 1:
                raise LookupError(f"multiple phones ({len(vis)}), specify device")
            return vis[0]
        ql = q.lower()
        for d in vis:
            if d.id.lower() == ql:
                return d
        for d in vis:
            if d.name.lower() == ql:
                return d
        for d in vis:
            if d.id.lower().startswith(ql):
                return d
        raise LookupError(f"no phone matches {q}")

    def device_json(self, d: MockDevice) -> dict[str, Any]:
        bt_state = "connected" if (d.bluetooth and d.matched) else ("unmatched" if d.bluetooth else "disconnected")
        info: dict[str, Any] = {"id": d.id, "name": d.name, "bluetooth": d.bluetooth, "lockState": d.lock_state,
                                "bluetoothState": bt_state, "width": d.width, "height": d.height}
        if d.bluetooth and d.matched:
            info["pointer"] = {"ready": d.pointer_ready, "state": "ready" if d.pointer_ready else "calibrating",
                               "host": d.host, "calibration": {"mode": "relative-calibrated", "loaded": True}}
        else:
            info["pointer"] = {"state": "unpaired", "ready": False}
        return info

    def capture(self) -> dict[str, Any]:
        return {"identityState": "verified", "permitsPointerAssociation": True,
                "screens": [{"physicalID": d.id, "sourceID": d.id, "identityVerified": True,
                             "permitsPointerAssociation": True} for d in self.visible()]}

    def status(self) -> dict[str, Any]:
        hosts = []
        unmatched = []
        for d in self.devices.values():
            if d.bluetooth:
                h: dict[str, Any] = {"host": d.host, "subscribed": ["keyboard", "mouse", "consumer"],
                                     "session": str(uuid.uuid4())}
                if d.matched and d.present:
                    h["screen"] = {"id": d.id, "name": d.name}
                else:
                    unmatched.append(d.host)
                hosts.append(h)
        return {"bluetooth": "advertising as iPhone Use", "bluetoothHosts": hosts, "capture": self.capture(),
                "devices": [self.device_json(d) for d in self.visible()], "unmatchedHosts": unmatched}


def create_app(n: int = 3, latency_ms: float = 0.0) -> FastAPI:
    app = FastAPI(title="iPhone-use mock (SIMULATED)")
    state = MockState(n, latency_ms)
    app.state.mock = state

    async def body_of(req: Request) -> dict[str, Any]:
        try:
            data = await req.json()
        except Exception:  # noqa: BLE001
            data = {}
        return data if isinstance(data, dict) else {}

    def err(msg: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg, "simulated": True}, status_code=status)

    @app.get("/")
    @app.get("/status")
    def status() -> dict[str, Any]:
        s = state.status()
        s["simulated"] = True
        return s

    @app.get("/devices")
    def devices() -> dict[str, Any]:
        return {"capture": state.capture(), "devices": [state.device_json(d) for d in state.visible()], "simulated": True}

    @app.get("/config")
    def config() -> dict[str, Any]:
        return {"language": "zh", "simulated": True}

    @app.get("/screenshot")
    def screenshot(device: str | None = None, maxEdge: int | None = None, format: str | None = None) -> Response:
        try:
            d = state.find(device)
        except LookupError as e:
            return err(str(e))
        if state.latency_ms:
            time.sleep(state.latency_ms / 1000)
        w, h = 30, 53
        png = _png(w, h, (0x33, 0x66, 0x99))
        headers = {"X-Device": d.id, "X-Screen-Width": str(d.width), "X-Screen-Height": str(d.height),
                   "X-Image-Width": str(w), "X-Image-Height": str(h), "X-Simulated": "1"}
        return Response(content=png, media_type="image/png", headers=headers)

    async def action(req: Request, name: str, required: tuple[str, ...] = ()) -> Response:
        body = await body_of(req)
        try:
            d = state.find(body.get("device"))
        except LookupError as e:
            return err(str(e))
        for k in required:
            if k not in body:
                return err(f"{k} required")
        if name in ("tap", "swipe", "move", "type", "key", "home", "power") and not d.pointer_ready and name != "home":
            return err("pointer not ready", 503)
        if name == "tap":
            for k in ("x", "y"):
                if not isinstance(body.get(k), (int, float)) or isinstance(body.get(k), bool):
                    return err(f"{k} must be number")
        if name == "swipe":
            pts = body.get("points")
            if not isinstance(pts, list) or len(pts) < 2:
                return err("points must have at least two entries")
        if name == "type" and not str(body.get("text", "")).isascii():
            return err("text must be ascii")
        if state.latency_ms:
            time.sleep(state.latency_ms / 1000)
        d.actions.append({"action": name, "body": body, "ts": time.time()})
        return JSONResponse({"ok": True, "device": d.id, "simulated": True})

    @app.post("/tap")
    async def tap(req: Request) -> Response:
        return await action(req, "tap", ("x", "y"))

    @app.post("/swipe")
    async def swipe(req: Request) -> Response:
        return await action(req, "swipe", ("points",))

    @app.post("/move")
    async def move(req: Request) -> Response:
        return await action(req, "move", ("x", "y"))

    @app.post("/nudge")
    async def nudge(req: Request) -> Response:
        return await action(req, "nudge", ("dx", "dy"))

    @app.post("/type")
    async def type_(req: Request) -> Response:
        return await action(req, "type", ("text",))

    @app.post("/key")
    async def key(req: Request) -> Response:
        return await action(req, "key", ("key",))

    @app.post("/home")
    async def home(req: Request) -> Response:
        return await action(req, "home")

    @app.post("/power")
    async def power(req: Request) -> Response:
        return await action(req, "power")

    @app.post("/match")
    async def match(req: Request) -> Response:
        body = await body_of(req)
        matched: dict[str, str] = {}
        for d in state.visible():
            if d.bluetooth and not d.matched and body.get("probe"):
                d.matched = True
                d.pointer_ready = True
                matched[d.id] = d.host
        s = state.status()
        return JSONResponse({"capture": s["capture"], "matched": matched, "devices": s["devices"],
                             "unmatchedHosts": s["unmatchedHosts"], "simulated": True})

    @app.post("/pair")
    async def pair(req: Request) -> Response:
        body = await body_of(req)
        host = body.get("host")
        if not host:
            return err("pairHostRequired")
        try:
            d = state.find(body.get("device"))
        except LookupError as e:
            return err(str(e))
        for other in state.devices.values():
            if other.host == host:
                other.host = str(uuid.uuid4()).upper() if other is not d else other.host
        d.host = host
        d.bluetooth = True
        d.matched = True
        d.pointer_ready = True
        return JSONResponse({"capture": state.capture(), "devices": [state.device_json(x) for x in state.visible()],
                             "simulated": True})

    @app.post("/calibration/auto")
    async def calibration_auto(req: Request) -> Response:
        body = await body_of(req)
        targets = [state.find(body["device"])] if body.get("device") else state.visible()
        if not body.get("verifyOnly"):
            for d in targets:
                if d.bluetooth and d.matched:
                    d.pointer_ready = True
        s = state.status()
        return JSONResponse(s)

    @app.post("/calibration/reload")
    async def calibration_reload(req: Request) -> Response:
        body = await body_of(req)
        try:
            d = state.find(body.get("device"))
        except LookupError as e:
            return err(str(e))
        return JSONResponse({"ok": True, "device": d.id, "simulated": True})

    # ---------- 故障注入 ----------
    @app.post("/_mock/disconnect")
    async def m_disconnect(req: Request) -> Response:
        d = state.devices[(await body_of(req))["device"]]
        d.present = False
        return JSONResponse({"ok": True})

    @app.post("/_mock/reconnect")
    async def m_reconnect(req: Request) -> Response:
        d = state.devices[(await body_of(req))["device"]]
        d.present = True
        return JSONResponse({"ok": True})

    @app.post("/_mock/ble_drop")
    async def m_ble_drop(req: Request) -> Response:
        d = state.devices[(await body_of(req))["device"]]
        d.bluetooth = False
        d.matched = False
        d.pointer_ready = False
        return JSONResponse({"ok": True})

    @app.post("/_mock/ble_unmatch")
    async def m_ble_unmatch(req: Request) -> Response:
        d = state.devices[(await body_of(req))["device"]]
        d.bluetooth = True
        d.matched = False
        d.pointer_ready = False
        return JSONResponse({"ok": True})

    @app.post("/_mock/swap_host")
    async def m_swap_host(req: Request) -> Response:
        """模拟交叉绑定：把 a 的 host 换成 b 的 host。"""
        b = await body_of(req)
        da, db = state.devices[b["a"]], state.devices[b["b"]]
        da.host, db.host = db.host, da.host
        return JSONResponse({"ok": True})

    @app.post("/_mock/reset")
    async def m_reset(req: Request) -> Response:
        b = await body_of(req)
        state.reset(int(b.get("n", len(state.devices))))
        return JSONResponse({"ok": True, "n": len(state.devices)})

    @app.get("/_mock/actions")
    def m_actions() -> dict[str, Any]:
        return {d.id: d.actions for d in state.devices.values()}

    return app


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="iPhone-use mock server（SIMULATED，仅离线验证脚本）")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--port", type=int, default=7390)
    ap.add_argument("--latency-ms", type=float, default=0.0)
    a = ap.parse_args()
    uvicorn.run(create_app(a.n, a.latency_ms), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
