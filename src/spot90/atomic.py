"""原子化文件写入：先写同目录临时文件再 os.replace，读者永远看到完整文件。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: Path, obj, **kw) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, **kw))


def atomic_to_parquet(df, path: Path) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
