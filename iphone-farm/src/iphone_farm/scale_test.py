"""逐级实机测试（5/10/15/20/30 台）：统计 USB 识别数、可控制数、蓝牙稳定性、画面响应、故障次数，输出报告。

数据来源标签：
  REAL_DEVICE     在 Mac 上对真实 iPhone 运行
  SIMULATED_MOCK  对 mock_server 运行（只用于验证脚本本身，报告中明确标注，不得当作真实结果）
"""
from __future__ import annotations

import json
import platform
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .client import IPhoneUseClient, IPhoneUseError
from .config import FarmConfig
from .events import EventLog
from .locks import DeviceLocks
from .monitor import Monitor
from .registry import DeviceRegistry, enumerate_usb_iphones_macos, enumerate_usbmux_udids
from .single import SingleController, snapshot_from_status


@dataclass
class LevelResult:
    level: int
    source: str
    started_at: float
    finished_at: float = 0.0
    expected_slots: list[int] = field(default_factory=list)
    usb_enumerated: int = 0            # system_profiler 枚举到的 iPhone 数（Mac 专用；mock 下为 -1）
    usbmux_udids: int = 0              # usbmux/idevice_id 的 UDID 数（Mac 专用；mock 下为 -1）
    capture_identified: int = 0        # iPhone-use /devices 中出现的目标设备数
    ble_connected: int = 0             # bluetooth=true
    controllable: int = 0              # pointer.ready=true
    home_ok: int = 0                   # 广播 home 成功数
    home_fail: int = 0
    screenshot_ok: int = 0
    screenshot_fail: int = 0
    screenshot_latency_ms: dict[str, float] = field(default_factory=dict)   # p50/p95/max
    ble_drop_events: int = 0           # 稳定性观察期内 OK→蓝牙类异常 的次数
    state_transitions: int = 0
    recoveries: int = 0
    identity_mismatch: int = 0         # 回显设备不一致 / 名称漂移 / host 漂移 次数
    per_device: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ScaleTester:
    def __init__(self, cfg: FarmConfig, client: IPhoneUseClient, registry: DeviceRegistry, events: EventLog,
                 locks: DeviceLocks, source: str = "REAL_DEVICE"):
        self.cfg = cfg
        self.client = client
        self.registry = registry
        self.events = events
        self.locks = locks
        self.source = source
        self.single = SingleController(cfg, client, registry, events, locks)

    def run_level(self, level: int, stability_minutes: float | None = None, samples: int | None = None,
                  do_home: bool = True) -> LevelResult:
        slots = [s for s in self.registry.slots() if s <= level]
        res = LevelResult(level=level, source=self.source, started_at=time.time(), expected_slots=slots)
        if len(slots) < level:
            res.notes.append(f"注册表只有 {len(slots)} 台 ≤ 编号 {level}，本级按实际 {len(slots)} 台统计")

        # 1. USB 层面识别
        if platform.system() == "Darwin" and self.source == "REAL_DEVICE":
            res.usb_enumerated = len(enumerate_usb_iphones_macos())
            res.usbmux_udids = len(enumerate_usbmux_udids())
        else:
            res.usb_enumerated = -1
            res.usbmux_udids = -1
            res.notes.append("非 macOS 或模拟模式：未执行 system_profiler/usbmux 枚举")

        # 2. iPhone-use 识别 / 蓝牙 / 可控
        try:
            status = self.client.status()
        except IPhoneUseError as e:
            res.notes.append(f"/status 失败：{e}")
            res.finished_at = time.time()
            return res
        for s in slots:
            rec = self.registry.by_slot(s)
            snap = snapshot_from_status(status, rec.capture_id)
            row = {"slot": s, "device": rec.capture_id, "present": snap.present, "bluetooth": snap.bluetooth,
                   "pointer_ready": snap.pointer_ready, "name_ok": snap.name == rec.name,
                   "host_ok": (not rec.ble_host) or (snap.pointer_host in (None, rec.ble_host))}
            res.capture_identified += int(snap.present)
            res.ble_connected += int(snap.present and snap.bluetooth)
            res.controllable += int(snap.present and snap.pointer_ready)
            if snap.present and (not row["name_ok"] or not row["host_ok"]):
                res.identity_mismatch += 1
            res.per_device.append(row)

        # 3. 画面响应：每台 N 次截图，统计延迟
        n = samples if samples is not None else self.cfg.scale_test.screenshot_samples_per_device
        lat: list[float] = []
        for row in res.per_device:
            if not row["present"]:
                continue
            ok = 0
            for _ in range(n):
                try:
                    _, headers, ms = self.client.screenshot(row["device"], max_edge=600)
                    lat.append(ms)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    res.screenshot_fail += 1
                    if "回显" in str(e):
                        res.identity_mismatch += 1
                    self.events.error(f"截图失败：{e}", device=row["device"], slot=row["slot"])
            res.screenshot_ok += ok
            row["screenshot_ok"] = ok
        if lat:
            lat_sorted = sorted(lat)
            res.screenshot_latency_ms = {
                "p50": round(statistics.median(lat_sorted), 1),
                "p95": round(lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))], 1),
                "max": round(lat_sorted[-1], 1),
                "n": len(lat_sorted),
            }

        # 4. 安全动作：对所有可控设备广播 home（每台独立设备 ID）
        if do_home:
            for row in res.per_device:
                if not (row["present"] and row["pointer_ready"]):
                    continue
                r = self.single.run(row["slot"], "home")
                row["home_ok"] = r.ok
                if r.ok:
                    res.home_ok += 1
                else:
                    res.home_fail += 1
                    if r.error and r.error.startswith("identity"):
                        res.identity_mismatch += 1

        # 5. 蓝牙稳定性观察：轮询 /status 一段时间，统计掉线/恢复
        minutes = stability_minutes if stability_minutes is not None else self.cfg.scale_test.ble_stability_minutes
        if minutes > 0:
            mon = Monitor(self.cfg, self.client, self.registry, self.events, self.locks)
            mon.run(duration_s=minutes * 60, recover=True)
            for s in slots:
                h = mon.health.get(s)
                if h:
                    res.ble_drop_events += h.ble_drops
                    res.state_transitions += h.transitions
                    res.recoveries += h.recoveries
        res.finished_at = time.time()
        self.events.log("test", f"level {level} 完成", level=level, source=self.source,
                        capture=res.capture_identified, controllable=res.controllable)
        return res


# ---------- 报告 ----------

def render_markdown(results: list[LevelResult], cfg: FarmConfig, meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    src = {r.source for r in results}
    banner = ""
    if "SIMULATED_MOCK" in src:
        banner = ("> **⚠ 本报告包含 SIMULATED_MOCK 数据：来自 mock_server 的模拟结果，只证明脚本可运行，"
                  "不代表任何真实 iPhone/USB/蓝牙表现，不得作为实机结论引用。**\n\n")
    lines = [f"# 逐级实机测试报告（生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}）", "", banner]
    lines.append(f"- 主机：{meta.get('host', platform.platform())}")
    lines.append(f"- iPhone-use 地址：{cfg.api.base_url}")
    lines.append(f"- 数据来源：{', '.join(sorted(src))}")
    lines.append("")
    lines.append("| 级别(台) | 来源 | USB枚举 | usbmux UDID | iPhone-use识别 | 蓝牙已连 | 可控制 | Home成功/失败 | 截图成功/失败 | 截图延迟p50/p95(ms) | 蓝牙掉线 | 状态变化 | 自动恢复 | 身份不一致 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lat = r.screenshot_latency_ms
        lat_s = f"{lat.get('p50', '-')}/{lat.get('p95', '-')}" if lat else "-"
        usb = "n/a" if r.usb_enumerated < 0 else str(r.usb_enumerated)
        mux = "n/a" if r.usbmux_udids < 0 else str(r.usbmux_udids)
        lines.append(f"| {r.level} | {r.source} | {usb} | {mux} | {r.capture_identified}/{len(r.expected_slots)} | "
                     f"{r.ble_connected} | {r.controllable} | {r.home_ok}/{r.home_fail} | "
                     f"{r.screenshot_ok}/{r.screenshot_fail} | {lat_s} | {r.ble_drop_events} | {r.state_transitions} | "
                     f"{r.recoveries} | {r.identity_mismatch} |")
    lines.append("")
    for r in results:
        lines.append(f"## 级别 {r.level} 台（{r.source}）")
        lines.append(f"- 开始 {time.strftime('%H:%M:%S', time.localtime(r.started_at))}，"
                     f"耗时 {r.finished_at - r.started_at:.0f}s")
        for n in r.notes:
            lines.append(f"- 备注：{n}")
        lines.append("")
        lines.append("| 编号 | 设备ID | 在列 | 蓝牙 | 可控 | 名称一致 | host一致 | 截图成功 | Home |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in r.per_device:
            lines.append(f"| {d['slot']} | `{d['device'][:12]}…` | {'✓' if d['present'] else '✗'} | "
                         f"{'✓' if d['bluetooth'] else '✗'} | {'✓' if d['pointer_ready'] else '✗'} | "
                         f"{'✓' if d['name_ok'] else '✗'} | {'✓' if d['host_ok'] else '✗'} | "
                         f"{d.get('screenshot_ok', '-')} | {d.get('home_ok', '-')} |")
        lines.append("")
    return "\n".join(lines)


def save_results(results: list[LevelResult], cfg: FarmConfig, meta: dict[str, Any] | None = None) -> tuple[Path, Path]:
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    tag = "SIMULATED" if any(r.source == "SIMULATED_MOCK" for r in results) else "REAL"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md = cfg.reports_dir / f"scale_test_{tag}_{stamp}.md"
    js = cfg.reports_dir / f"scale_test_{tag}_{stamp}.json"
    md.write_text(render_markdown(results, cfg, meta), encoding="utf-8")
    js.write_text(json.dumps({"meta": meta or {}, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return md, js
