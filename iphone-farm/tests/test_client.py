import pytest

from iphone_farm.client import DeviceMismatch, IPhoneUseClient


def test_devices_and_status(client, mock_state):
    devs = client.devices()
    assert len(devs) == 5
    st = client.status()
    assert len(st["bluetoothHosts"]) == 5
    assert st["unmatchedHosts"] == []


def test_action_requires_device(client):
    with pytest.raises(ValueError):
        client.home("")
    with pytest.raises(ValueError):
        client.tap("", 0.5, 0.5)


def test_tap_echo_and_record(client, mock_state):
    d = next(iter(mock_state.devices.values()))
    r = client.tap(d.id, 0.5, 0.5)
    assert r.ok and r.device == d.id and r.payload["device"] == d.id
    assert d.actions[-1]["action"] == "tap" and d.actions[-1]["body"]["device"] == d.id


def test_prefix_is_rejected_by_echo_check(client, mock_state):
    # 服务端允许前缀匹配；客户端要求回显完整 id，前缀调用会被判定为目标不一致
    d = next(iter(mock_state.devices.values()))
    with pytest.raises(DeviceMismatch):
        client.home(d.id[:6])


def test_type_ascii_only(client, mock_state):
    d = next(iter(mock_state.devices.values()))
    with pytest.raises(ValueError):
        client.type_text(d.id, "中文")
    assert client.type_text(d.id, "Settings").ok


def test_screenshot_headers(client, mock_state):
    d = next(iter(mock_state.devices.values()))
    data, headers, ms = client.screenshot(d.id, max_edge=600)
    assert data.startswith(b"\x89PNG")
    assert headers["X-Device"] == d.id and ms >= 0


def test_pointer_not_ready_503(client, mock_state):
    d = list(mock_state.devices.values())[1]
    d.pointer_ready = False
    r = client.tap(d.id, 0.5, 0.5)
    assert not r.ok and r.status == 503
