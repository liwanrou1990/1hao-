"""安全动作目录：第二阶段广播只允许这些动作（Home、打开设置等），每个动作都必须带完整设备 ID。

动作 = 一串对 iPhone-use 的调用；返回最后一步的 ActionResult。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .client import ActionResult, IPhoneUseClient


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    safe_for_broadcast: bool
    run: Callable[[IPhoneUseClient, str, dict[str, Any]], ActionResult]


def _home(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.home(dev)


def _wake(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    # Home 会唤醒但不解锁（文档）。解锁需要人工或已关闭锁屏密码的测试机。
    return c.home(dev)


def _open_app(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    """Home → Spotlight(cmd+space) → 输入应用名(ASCII) → 回车。
    中文系统下 Spotlight 对英文名的匹配不保证；测试机建议英文系统，或把 query 设为可被 Spotlight 命中的 ASCII 名。"""
    query = str(p.get("query") or "Settings")
    settle = float(p.get("settle_s", 0.6))
    r = c.home(dev)
    if not r.ok:
        return r
    time.sleep(settle)
    r = c.key(dev, "space", ["cmd"])
    if not r.ok:
        return r
    time.sleep(settle)
    r = c.type_text(dev, query)
    if not r.ok:
        return r
    time.sleep(settle)
    return c.key(dev, "enter")


def _open_settings(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    q = dict(p)
    q.setdefault("query", "Settings")
    return _open_app(c, dev, q)


def _tap(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.tap(dev, float(p["x"]), float(p["y"]), fraction=bool(p.get("fraction", True)), hold=p.get("hold"))


def _swipe(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    pts = [(float(q["x"]), float(q["y"])) for q in p["points"]]
    return c.swipe(dev, pts, duration=float(p.get("duration", 0.3)), fraction=bool(p.get("fraction", True)))


def _swipe_up(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.swipe(dev, [(0.5, 0.8), (0.5, 0.3)], duration=float(p.get("duration", 0.3)))


def _type(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.type_text(dev, str(p["text"]))


def _key(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.key(dev, str(p["key"]), list(p.get("modifiers") or []))


def _power(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    return c.power(dev)


def _sms_open(c: IPhoneUseClient, dev: str, p: dict[str, Any]) -> ActionResult:
    """只打开原生“信息”App（单机专用，禁止广播）。不输入、不发送。"""
    q = dict(p)
    q.setdefault("query", "Messages")
    return _open_app(c, dev, q)


CATALOG: dict[str, ActionSpec] = {
    "home": ActionSpec("home", "回到主屏（HID Consumer 0x0223）", True, _home),
    "wake": ActionSpec("wake", "唤醒屏幕（等同 home，不解锁）", True, _wake),
    "open_app": ActionSpec("open_app", "Spotlight 打开应用：params.query（ASCII）", True, _open_app),
    "open_settings": ActionSpec("open_settings", "打开“设置”App（Spotlight 搜 Settings）", True, _open_settings),
    "tap": ActionSpec("tap", "点击：params.x/y（默认 0–1 相对坐标）", True, _tap),
    "swipe": ActionSpec("swipe", "滑动：params.points=[{x,y},...]", True, _swipe),
    "swipe_up": ActionSpec("swipe_up", "从下往上滑", True, _swipe_up),
    "type": ActionSpec("type", "键盘输入 ASCII：params.text", True, _type),
    "key": ActionSpec("key", "按键：params.key / modifiers", True, _key),
    "power": ActionSpec("power", "电源键短按（锁屏，非幂等，禁止广播）", False, _power),
    "sms_open": ActionSpec("sms_open", "打开原生信息 App（单机专用，禁止广播）", False, _sms_open),
}


def get_action(name: str) -> ActionSpec:
    if name not in CATALOG:
        raise KeyError(f"未知动作 '{name}'，可用：{', '.join(sorted(CATALOG))}")
    return CATALOG[name]
