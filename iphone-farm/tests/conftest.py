"""离线测试：用 httpx 的 ASGI transport 直连 mock_server（SIMULATED_MOCK），不需要真机。"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from iphone_farm.client import IPhoneUseClient
from iphone_farm.config import FarmConfig
from iphone_farm.events import EventLog
from iphone_farm.locks import DeviceLocks
from iphone_farm.mock_server import create_app
from iphone_farm.registry import DeviceRegistry


@pytest.fixture
def mock_app():
    return create_app(n=5)


@pytest.fixture
def mock_state(mock_app):
    return mock_app.state.mock


@pytest.fixture
def client(mock_app):
    # httpx 0.28：ASGITransport + 同步 Client 需要用 WSGI 风格；这里用线程内 uvicorn 更稳
    import threading
    import socket
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/status", timeout=0.5)
            break
        except Exception:
            time.sleep(0.05)
    c = IPhoneUseClient(f"http://127.0.0.1:{port}", timeout_s=5, action_timeout_s=5)
    yield c
    c.close()
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture
def cfg(tmp_path: Path) -> FarmConfig:
    c = FarmConfig()
    c.root = tmp_path
    c.broadcast.allowed_actions = ["home", "open_app", "open_settings", "screenshot", "wake", "tap", "swipe", "type", "key"]
    c.broadcast.stagger_ms = 0
    c.monitor.poll_interval_s = 0.05
    c.monitor.no_frame_alert_after_s = 0.1
    c.monitor.usb_absent_alert_after_s = 0.1
    return c


@pytest.fixture
def events(cfg) -> EventLog:
    return EventLog(cfg.state_dir / "events.jsonl", source="SIMULATED_MOCK")


@pytest.fixture
def locks(cfg) -> DeviceLocks:
    return DeviceLocks(cfg.state_dir / "locks")


@pytest.fixture
def registry(cfg, client, mock_state) -> DeviceRegistry:
    reg = DeviceRegistry(cfg.registry_file)
    for d in mock_state.devices.values():
        slot = int(d.name[2:])
        reg.register(slot, d.id, d.name, udid="f" * 40 if False else f"{slot:040d}", require_udid=True)
        reg.set_ble_host(slot, d.host)
    return reg
