"""任务互斥：同一台设备同一时刻只允许一个任务（进程内 threading.Lock + 跨进程文件锁）。

iPhone-use 本身只对 MCP 客户端加锁，HTTP 调用之间不互斥；因此群控层必须自行保证
“监控恢复”与“广播动作”、两个并行 CLI 之间不会同时操作同一台手机。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import threading
import time
from pathlib import Path
from typing import Iterator


class DeviceBusy(RuntimeError):
    pass


class DeviceLocks:
    def __init__(self, lock_dir: Path):
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._local: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, device: str) -> threading.Lock:
        with self._guard:
            if device not in self._local:
                self._local[device] = threading.Lock()
            return self._local[device]

    def _lock_path(self, device: str) -> Path:
        h = hashlib.sha1(device.encode("utf-8")).hexdigest()[:16]
        return self.lock_dir / f"device-{h}.lock"

    @contextlib.contextmanager
    def acquire(self, device: str, timeout_s: float = 30, owner: str = "") -> Iterator[None]:
        if not device:
            raise ValueError("device 不能为空：互斥锁必须绑定真实设备 ID")
        local = self._lock_for(device)
        deadline = time.monotonic() + timeout_s
        if not local.acquire(timeout=timeout_s):
            raise DeviceBusy(f"设备 {device} 正在被本进程其他任务占用")
        fh = None
        try:
            fh = self._lock_path(device).open("a+")
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise DeviceBusy(f"设备 {device} 正在被其他进程占用（{self._lock_path(device)}）")
                    time.sleep(0.05)
            fh.seek(0)
            fh.truncate()
            fh.write(f"{owner or 'ifarm'} {time.time():.0f}\n")
            fh.flush()
            yield
        finally:
            if fh is not None:
                with contextlib.suppress(Exception):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
            local.release()
