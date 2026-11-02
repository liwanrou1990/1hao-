import pytest

from iphone_farm.registry import DeviceRegistry, RegistryError


def test_register_requires_matching_name_and_udid(tmp_path):
    reg = DeviceRegistry(tmp_path / "r.json")
    with pytest.raises(RegistryError):
        reg.register(1, "ID1", "iPhone", udid="a" * 40)          # 名称不符合 IFxx
    with pytest.raises(RegistryError):
        reg.register(1, "ID1", "IF02", udid="a" * 40)            # 名称编号与 slot 不一致
    with pytest.raises(RegistryError):
        reg.register(1, "ID1", "IF01")                           # 缺 UDID
    rec = reg.register(1, "ID1", "IF01", udid="a" * 40)
    assert rec.slot == 1
    with pytest.raises(RegistryError):
        reg.register(2, "ID1", "IF02", udid="b" * 40)            # 同一 capture_id 不能绑两个编号
    with pytest.raises(RegistryError):
        reg.register(2, "ID2", "IF02", udid="a" * 40)            # 同一 UDID 不能绑两个编号
    reg.register(2, "ID2", "IF02", udid="b" * 40)
    reg.set_ble_host(1, "HOST-A")
    with pytest.raises(RegistryError):
        reg.set_ble_host(2, "HOST-A")                            # 一个蓝牙 host 只能绑一台
    reg2 = DeviceRegistry(tmp_path / "r.json")
    assert reg2.by_slot(1).ble_host == "HOST-A"


def test_resolve_accepts_slot_or_full_id_only(tmp_path):
    reg = DeviceRegistry(tmp_path / "r.json")
    reg.register(7, "ABCDEF0123", "IF07", udid="c" * 40)
    assert reg.resolve(7).capture_id == "ABCDEF0123"
    assert reg.resolve("IF07").capture_id == "ABCDEF0123"
    assert reg.resolve("ABCDEF0123").slot == 7
    with pytest.raises(RegistryError):
        reg.resolve("ABCDEF")       # 前缀不接受
    with pytest.raises(RegistryError):
        reg.resolve(8)
