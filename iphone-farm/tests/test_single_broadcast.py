import httpx
import pytest

from iphone_farm.broadcast import BroadcastForbidden, Broadcaster, select_targets
from iphone_farm.single import SingleController


def _single(cfg, client, registry, events, locks):
    return SingleController(cfg, client, registry, events, locks)


def test_single_home_with_identity_check(cfg, client, registry, events, locks, mock_state):
    s = _single(cfg, client, registry, events, locks)
    r = s.run(3, "home")
    assert r.ok
    d = registry.by_slot(3)
    assert mock_state.devices[d.capture_id].actions[-1]["action"] == "home"
    assert any(e["kind"] == "action" and e["slot"] == 3 for e in events.read())


def test_single_refuses_host_drift(cfg, client, registry, events, locks, mock_state):
    a, b = registry.by_slot(1), registry.by_slot(2)
    httpx.post(client.base_url + "/_mock/swap_host", json={"a": a.capture_id, "b": b.capture_id})
    s = _single(cfg, client, registry, events, locks)
    r = s.run(1, "home")
    assert not r.ok and "identity" in (r.error or "")
    assert not mock_state.devices[a.capture_id].actions


def test_single_refuses_name_drift(cfg, client, registry, events, locks, mock_state):
    rec = registry.by_slot(4)
    mock_state.devices[rec.capture_id].name = "iPhone"
    s = _single(cfg, client, registry, events, locks)
    assert not s.run(4, "home").ok
    assert s.run(4, "home", allow_name_drift=True).ok


def test_open_settings_sequence(cfg, client, registry, events, locks, mock_state):
    s = _single(cfg, client, registry, events, locks)
    r = s.run(2, "open_settings", {"settle_s": 0})
    assert r.ok
    acts = [a["action"] for a in mock_state.devices[registry.by_slot(2).capture_id].actions]
    assert acts == ["home", "key", "type", "key"]


def test_broadcast_group_and_forbidden(cfg, client, registry, events, locks, mock_state):
    s = _single(cfg, client, registry, events, locks)
    bc = Broadcaster(cfg, s)
    groups = {"pilot": [1, 2, 3]}
    targets = select_targets(registry, groups, group="pilot")
    rep = bc.run(targets, "home")
    assert rep.ok_count == 3 and rep.fail_count == 0
    assert sorted(r["slot"] for r in rep.results) == [1, 2, 3]
    for r in rep.results:
        assert r["device"] == registry.by_slot(r["slot"]).capture_id
    with pytest.raises(BroadcastForbidden):
        bc.run(targets, "sms_open")
    with pytest.raises(BroadcastForbidden):
        bc.run(targets, "power")
    with pytest.raises(KeyError):
        select_targets(registry, groups, slots=[99])


def test_broadcast_partial_failure_reported(cfg, client, registry, events, locks, mock_state):
    s = _single(cfg, client, registry, events, locks)
    bc = Broadcaster(cfg, s)
    victim = registry.by_slot(2)
    httpx.post(client.base_url + "/_mock/ble_drop", json={"device": victim.capture_id})
    rep = bc.run(select_targets(registry, {}, slots=[1, 2, 3]), "home")
    assert rep.ok_count == 2 and rep.fail_count == 1
    bad = [r for r in rep.results if not r["ok"]][0]
    assert bad["slot"] == 2
