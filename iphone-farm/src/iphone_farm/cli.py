"""ifarm 命令行：安装检查、设备注册与编号、单机控制、多机广播、监控恢复、逐级测试。"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

from .broadcast import BroadcastForbidden, Broadcaster, select_targets
from .client import IPhoneUseClient, IPhoneUseError
from .config import FarmConfig, load_config, load_groups, load_sms_policy
from .events import EventLog
from .locks import DeviceLocks
from .monitor import Monitor
from .registry import DeviceRegistry, RegistryError, enumerate_usb_iphones_macos, enumerate_usbmux_udids
from .scale_test import ScaleTester, save_results
from .single import SingleController, snapshot_from_status


def _ctx(args: argparse.Namespace) -> tuple[FarmConfig, IPhoneUseClient, DeviceRegistry, EventLog, DeviceLocks]:
    cfg = load_config(args.config)
    if args.api:
        cfg.api.base_url = args.api
    source = "SIMULATED_MOCK" if args.simulated else "REAL_DEVICE"
    client = IPhoneUseClient(cfg.api.base_url, cfg.api.timeout_s, cfg.api.action_timeout_s,
                             require_echo_match=cfg.control.require_echo_match)
    reg_path = Path(args.registry) if args.registry else cfg.registry_file
    registry = DeviceRegistry(reg_path)
    events = EventLog(cfg.state_dir / "events.jsonl", source=source)
    locks = DeviceLocks(cfg.state_dir / "locks")
    return cfg, client, registry, events, locks


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# ---------- 子命令 ----------

def cmd_check(args: argparse.Namespace) -> int:
    """安装/环境检查：iPhone-use 服务、macOS 工具、USB 枚举。"""
    cfg, client, registry, events, _ = _ctx(args)
    report: dict[str, Any] = {"platform": platform.platform(), "api": cfg.api.base_url}
    try:
        st = client.status()
        report["iphone_use"] = {"reachable": True, "bluetooth": st.get("bluetooth"),
                                "devices": len(st.get("devices") or []),
                                "bluetoothHosts": len(st.get("bluetoothHosts") or []),
                                "unmatchedHosts": st.get("unmatchedHosts")}
    except Exception as e:  # noqa: BLE001
        report["iphone_use"] = {"reachable": False, "error": str(e),
                                "hint": "请先启动 iPhoneUse.app（或 ./scripts/run.sh），确认 IPHONE_USE_PORT"}
    if platform.system() == "Darwin":
        usb = enumerate_usb_iphones_macos()
        report["usb_iphones"] = {"count": len(usb), "items": usb}
        report["usbmux_udids"] = enumerate_usbmux_udids()
    else:
        report["usb_iphones"] = {"count": -1, "note": "非 macOS，无法枚举 USB"}
    report["registry"] = {"path": str(registry.path), "slots": registry.slots()}
    _print(report)
    return 0 if report.get("iphone_use", {}).get("reachable") else 1


def cmd_devices(args: argparse.Namespace) -> int:
    cfg, client, registry, _, _ = _ctx(args)
    st = client.status()
    rows = []
    for d in st.get("devices") or []:
        rec = registry.by_capture_id(d.get("id", ""))
        p = d.get("pointer") or {}
        rows.append({"slot": rec.slot if rec else None, "id": d.get("id"), "name": d.get("name"),
                     "bluetooth": d.get("bluetooth"), "bluetoothState": d.get("bluetoothState"),
                     "pointer": p.get("state"), "ready": p.get("ready"), "host": p.get("host"),
                     "lock": d.get("lockState"), "size": (d.get("width"), d.get("height"))})
    _print({"devices": rows, "unmatchedHosts": st.get("unmatchedHosts"), "bluetooth": st.get("bluetooth")})
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """逐台注册：把当前 /devices 中名为 IFxx 的设备绑定为编号 xx，并交叉核对 UDID、写入蓝牙 host。"""
    cfg, client, registry, events, _ = _ctx(args)
    st = client.status()
    devs = st.get("devices") or []
    usb = enumerate_usb_iphones_macos() if platform.system() == "Darwin" else []
    udids = set(enumerate_usbmux_udids()) if platform.system() == "Darwin" else set()
    pattern = cfg.registration.name_pattern
    want = args.slot
    results = []
    for d in devs:
        name = d.get("name") or ""
        m = re.match(pattern, name)
        if not m:
            continue
        slot = int(m.group(1))
        if want is not None and slot != want:
            continue
        cid = d.get("id")
        udid = args.udid
        loc = None
        if not udid:
            # 1) 若 capture_id 本身就是 40 位 UDID；2) 若 USB 枚举中仅有一台同名 iPhone，取其序列号
            if re.fullmatch(r"[0-9a-fA-F]{40}", cid or ""):
                udid = cid.lower()
            cands = [u for u in usb if (u.get("name") or "").strip().lower() == name.lower()]
            if len(cands) == 1:
                udid = udid or (cands[0].get("serial_num") or None)
                loc = cands[0].get("location_id")
            if udid and udids and udid.lower() not in {u.lower() for u in udids}:
                results.append({"slot": slot, "error": f"UDID {udid} 不在 usbmux 列表中，拒绝注册"})
                continue
        try:
            rec = registry.register(slot, cid, name, udid=udid, usb_location_id=loc, name_pattern=pattern,
                                    require_udid=cfg.registration.require_udid_crosscheck and not args.no_udid)
            host = (d.get("pointer") or {}).get("host")
            if host:
                registry.set_ble_host(slot, host)
            events.log("register", f"注册编号 {slot}", device=cid, slot=slot, name=name, udid=udid, host=host)
            results.append({"slot": slot, "device": cid, "name": name, "udid": udid, "ble_host": host,
                            "location_id": loc})
        except Exception as e:  # noqa: BLE001
            results.append({"slot": slot, "device": cid, "error": str(e)})
    if not results:
        print("没有发现名称符合 IFxx 模式的设备。请先在手机“设置→通用→关于本机→名称”改名，再重新插拔。", file=sys.stderr)
        return 1
    _print({"registered": results, "registry": str(registry.path)})
    return 0 if all("error" not in r for r in results) else 2


def cmd_unregister(args: argparse.Namespace) -> int:
    cfg, _, registry, events, _ = _ctx(args)
    rec = registry.by_slot(args.slot)
    registry.unregister(args.slot)
    events.log("register", f"注销编号 {args.slot}", device=rec.capture_id, slot=args.slot)
    _print({"unregistered": args.slot, "device": rec.capture_id})
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """核对注册表与当前实际状态（名称、host、在线、可控）。"""
    cfg, client, registry, events, locks = _ctx(args)
    st = client.status()
    rows = []
    bad = 0
    for s in registry.slots():
        rec = registry.by_slot(s)
        snap = snapshot_from_status(st, rec.capture_id)
        ok = snap.present and snap.name == rec.name and (not rec.ble_host or snap.pointer_host in (None, rec.ble_host))
        bad += int(not ok)
        rows.append({"slot": s, "present": snap.present, "name": snap.name, "name_ok": snap.name == rec.name,
                     "host": snap.pointer_host, "host_ok": (not rec.ble_host) or snap.pointer_host in (None, rec.ble_host),
                     "ready": snap.pointer_ready, "bluetoothState": snap.bluetooth_state})
        if args.bind_hosts and snap.present and snap.pointer_host and not rec.ble_host:
            registry.set_ble_host(s, snap.pointer_host)
            rows[-1]["bound_host"] = snap.pointer_host
    _print({"rows": rows, "mismatch": bad})
    return 0 if bad == 0 else 2


def _params(args: argparse.Namespace) -> dict[str, Any]:
    p: dict[str, Any] = {}
    if getattr(args, "params", None):
        p.update(json.loads(args.params))
    for k in ("x", "y", "text", "key", "query"):
        v = getattr(args, k, None)
        if v is not None:
            p[k] = v
    return p


def cmd_run(args: argparse.Namespace) -> int:
    """单机执行一个动作：ifarm run 7 tap --x 0.5 --y 0.5"""
    cfg, client, registry, events, locks = _ctx(args)
    single = SingleController(cfg, client, registry, events, locks)
    if args.action.startswith("sms_"):
        pol = load_sms_policy()
        if not pol.get("authorized_contacts") and args.action != "sms_open":
            print("短信动作需要 config/sms_policy.yaml 中登记授权联系人", file=sys.stderr)
            return 3
    res = single.run(args.device, args.action, _params(args), allow_name_drift=args.allow_name_drift)
    _print(res.__dict__)
    return 0 if res.ok else 2


def cmd_screenshot(args: argparse.Namespace) -> int:
    cfg, client, registry, events, locks = _ctx(args)
    single = SingleController(cfg, client, registry, events, locks)
    out = args.out or str(cfg.reports_dir / "screens" / f"IF{registry.resolve(args.device).slot:02d}_{int(time.time())}.jpg")
    _print(single.screenshot(args.device, out_path=out))
    return 0


def cmd_broadcast(args: argparse.Namespace) -> int:
    cfg, client, registry, events, locks = _ctx(args)
    groups = load_groups(args.groups)
    single = SingleController(cfg, client, registry, events, locks)
    bc = Broadcaster(cfg, single)
    slots = [int(s) for s in args.slots.split(",")] if args.slots else None
    targets = select_targets(registry, groups, group=args.group, slots=slots)
    rep = bc.run(targets, args.action, _params(args), dry_run=args.dry_run)
    out = rep.to_dict()
    _print(out)
    if args.save:
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        p = cfg.reports_dir / f"broadcast_{args.action}_{int(rep.started_at)}.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {p}")
    return 0 if rep.fail_count == 0 else 2


def cmd_monitor(args: argparse.Namespace) -> int:
    cfg, client, registry, events, locks = _ctx(args)
    mon = Monitor(cfg, client, registry, events, locks)

    def tick(m: Monitor) -> None:
        if args.quiet:
            return
        line = " ".join(f"{h.slot}:{h.state}" for h in sorted(m.health.values(), key=lambda x: x.slot))
        print(time.strftime("%H:%M:%S"), line, flush=True)

    mon.run(duration_s=args.duration, recover=not args.no_recover, on_tick=tick)
    _print(mon.summary())
    return 0


def cmd_scale_test(args: argparse.Namespace) -> int:
    cfg, client, registry, events, locks = _ctx(args)
    source = "SIMULATED_MOCK" if args.simulated else "REAL_DEVICE"
    tester = ScaleTester(cfg, client, registry, events, locks, source=source)
    levels = [int(x) for x in args.levels.split(",")] if args.levels else cfg.scale_test.levels
    results = []
    for lv in levels:
        print(f"== 级别 {lv} 台（{source}）==", flush=True)
        r = tester.run_level(lv, stability_minutes=args.stability_minutes, samples=args.samples,
                             do_home=not args.no_home)
        results.append(r)
        print(f"识别 {r.capture_identified}/{len(r.expected_slots)} 蓝牙 {r.ble_connected} 可控 {r.controllable} "
              f"home {r.home_ok}/{r.home_fail} 截图 {r.screenshot_ok}/{r.screenshot_fail} 掉线 {r.ble_drop_events}", flush=True)
        if args.pause_between and lv != levels[-1]:
            input(f"请把设备增加到下一级别后按回车继续…")
    meta = {"host": platform.platform(), "source": source, "levels": levels, "user": os.environ.get("USER", "")}
    md, js = save_results(results, cfg, meta)
    print(f"报告：{md}\n数据：{js}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    cfg, _, _, events, _ = _ctx(args)
    rows = events.read()
    if args.kind:
        rows = [r for r in rows if r.get("kind") == args.kind]
    for r in rows[-args.tail:]:
        print(json.dumps(r, ensure_ascii=False))
    return 0


# ---------- 入口 ----------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ifarm", description="Mac + iPhone-use 多机群控")
    ap.add_argument("--config", help="farm.yaml 路径")
    ap.add_argument("--api", help="覆盖 iPhone-use 地址，如 http://127.0.0.1:7390")
    ap.add_argument("--registry", help="注册表 JSON 路径")
    ap.add_argument("--simulated", action="store_true", help="声明目标是 mock 服务器：所有记录标记 SIMULATED_MOCK")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="环境与服务检查").set_defaults(fn=cmd_check)
    sub.add_parser("devices", help="列出 iPhone-use 当前设备与注册编号").set_defaults(fn=cmd_devices)

    p = sub.add_parser("register", help="按设备名 IFxx 注册编号（交叉核对 UDID、记录蓝牙 host）")
    p.add_argument("--slot", type=int, help="只注册该编号")
    p.add_argument("--udid", help="手工指定 UDID")
    p.add_argument("--no-udid", action="store_true", help="跳过 UDID 交叉核对（不推荐）")
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("unregister", help="注销编号（换机/换 SIM 后重新注册）")
    p.add_argument("slot", type=int)
    p.set_defaults(fn=cmd_unregister)

    p = sub.add_parser("verify", help="核对注册表与实际状态")
    p.add_argument("--bind-hosts", action="store_true", help="把当前匹配到的蓝牙 host 写入尚未登记的记录")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("run", help="单机动作：ifarm run <编号|完整ID> <动作> [--x --y --text --key --query --params JSON]")
    p.add_argument("device")
    p.add_argument("action")
    p.add_argument("--x", type=float)
    p.add_argument("--y", type=float)
    p.add_argument("--text")
    p.add_argument("--key")
    p.add_argument("--query")
    p.add_argument("--params")
    p.add_argument("--allow-name-drift", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("screenshot", help="单机截图")
    p.add_argument("device")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_screenshot)

    p = sub.add_parser("broadcast", help="多机广播：--group pilot 或 --slots 1,2,3")
    p.add_argument("action")
    p.add_argument("--group")
    p.add_argument("--slots")
    p.add_argument("--groups", help="groups.yaml 路径")
    p.add_argument("--x", type=float)
    p.add_argument("--y", type=float)
    p.add_argument("--text")
    p.add_argument("--key")
    p.add_argument("--query")
    p.add_argument("--params")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--save", action="store_true")
    p.set_defaults(fn=cmd_broadcast)

    p = sub.add_parser("monitor", help="状态检测与自动恢复循环")
    p.add_argument("--duration", type=float, help="秒；不填则一直运行")
    p.add_argument("--no-recover", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_monitor)

    p = sub.add_parser("scale-test", help="逐级实机测试并生成报告")
    p.add_argument("--levels", help="如 3,5,10,15,20,30")
    p.add_argument("--stability-minutes", type=float)
    p.add_argument("--samples", type=int)
    p.add_argument("--no-home", action="store_true")
    p.add_argument("--pause-between", action="store_true", help="每级之间暂停，等待增加设备")
    p.set_defaults(fn=cmd_scale_test)

    p = sub.add_parser("events", help="查看事件/错误日志")
    p.add_argument("--kind")
    p.add_argument("--tail", type=int, default=50)
    p.set_defaults(fn=cmd_events)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return int(args.fn(args))
    except IPhoneUseError as e:
        print(f"iPhone-use 错误：{e}", file=sys.stderr)
        return 1
    except BroadcastForbidden as e:
        print(f"拒绝广播：{e}", file=sys.stderr)
        return 3
    except (KeyError, RegistryError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
