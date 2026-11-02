"""事件与错误记录：JSONL 追加写入，供故障诊断、测试报告使用。"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Event:
    kind: str                      # action / error / state / recovery / test
    device: str | None = None      # capture_id（完整设备 ID）
    slot: int | None = None        # 编号
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    source: str = "REAL_DEVICE"    # REAL_DEVICE | SIMULATED_MOCK


class EventLog:
    """线程安全的 JSONL 事件日志。"""

    def __init__(self, path: Path, source: str = "REAL_DEVICE"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source = source
        self._lock = threading.Lock()

    def write(self, event: Event) -> Event:
        if not event.source:
            event.source = self.source
        line = json.dumps(asdict(event), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return event

    def log(self, kind: str, message: str, device: str | None = None, slot: int | None = None, **data: Any) -> Event:
        return self.write(Event(kind=kind, device=device, slot=slot, message=message, data=data, source=self.source))

    def error(self, message: str, device: str | None = None, slot: int | None = None, **data: Any) -> Event:
        return self.log("error", message, device=device, slot=slot, **data)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
