import httpx

from iphone_farm.monitor import Monitor
from iphone_farm.scale_test import ScaleTester, render_markdown, save_results


def test_monitor_detects_and_recovers_unmatched(cfg, client, registry, events, locks, mock_state):
    mon = Monitor(cfg, client, registry, events, locks)
    mon.poll_once()
    assert all(h.state == "OK" for h in mon.health.values())
    rec = registry.by_slot(2)
    httpx.post(client.base_url + "/_mock/ble_unmatch", json={"device": rec.capture_id})
    mon.poll_once()
    assert mon.health[2].state == "BLE_UNMATCHED"
    assert mon.health[2].ble_drops == 1
    assert mon.health[2].recoveries == 1          # 自动 /pair（注册 host 出现在 unmatchedHosts）
    mon.poll_once()
    assert mon.health[2].state == "OK"
    kinds = [e["kind"] for e in events.read()]
    assert "recovery" in kinds and "error" in kinds


def test_monitor_usb_absent_alert(cfg, client, registry, events, locks, mock_state):
    import time
    mon = Monitor(cfg, client, registry, events, locks)
    rec = registry.by_slot(5)
    httpx.post(client.base_url + "/_mock/disconnect", json={"device": rec.capture_id})
    mon.poll_once()
    assert mon.health[5].state == "USB_ABSENT"
    time.sleep(0.15)
    mon.poll_once()
    assert any(e.get("data", {}).get("alert") for e in events.read())
    httpx.post(client.base_url + "/_mock/reconnect", json={"device": rec.capture_id})
    mon.poll_once()
    assert mon.health[5].state == "OK"


def test_monitor_api_down(cfg, registry, events, locks):
    from iphone_farm.client import IPhoneUseClient
    dead = IPhoneUseClient("http://127.0.0.1:1", timeout_s=0.3)
    mon = Monitor(cfg, dead, registry, events, locks)
    mon.poll_once()
    assert mon.poll_errors == 1
    assert all(h.state == "USB_ABSENT" for h in mon.health.values())


def test_scale_test_simulated_report_is_labelled(cfg, client, registry, events, locks, mock_state):
    t = ScaleTester(cfg, client, registry, events, locks, source="SIMULATED_MOCK")
    r = t.run_level(5, stability_minutes=0.01, samples=2)
    assert r.capture_identified == 5 and r.controllable == 5 and r.home_ok == 5
    assert r.screenshot_ok == 10 and r.identity_mismatch == 0
    md = render_markdown([r], cfg)
    assert "SIMULATED_MOCK" in md and "不得作为实机结论" in md
    p_md, p_js = save_results([r], cfg, {"host": "test"})
    assert "SIMULATED" in p_md.name and p_js.exists()


def test_scale_test_counts_missing_devices(cfg, client, registry, events, locks, mock_state):
    rec = registry.by_slot(3)
    httpx.post(client.base_url + "/_mock/disconnect", json={"device": rec.capture_id})
    t = ScaleTester(cfg, client, registry, events, locks, source="SIMULATED_MOCK")
    r = t.run_level(5, stability_minutes=0, samples=1)
    assert r.capture_identified == 4 and r.controllable == 4
