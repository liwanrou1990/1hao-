"""配置与路径。真实数据与合成数据使用不同的 SQLite 文件，物理隔离。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.json"
DATA_DIR = Path(os.environ.get("MARKSIX_DATA_DIR", ROOT / "data"))
# 使用自定义数据目录（测试/实验）时，报告写到该目录下，避免覆盖正式报告
REPORTS_DIR = DATA_DIR / "reports" if "MARKSIX_DATA_DIR" in os.environ else ROOT / "reports"

DATASET_KINDS = ("real", "synthetic")
KIND_LABEL = {"real": "真实数据", "synthetic": "合成演示数据"}


def db_path(kind: str) -> Path:
    if kind not in DATASET_KINDS:
        raise ValueError(f"未知数据集类型: {kind}")
    return DATA_DIR / kind / f"marksix_{kind}.sqlite"


def raw_dir(kind: str) -> Path:
    return DATA_DIR / kind / "raw"


def load_settings(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def settings_hash(settings: dict) -> str:
    blob = json.dumps(settings, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def code_version() -> str:
    """代码版本：git 提交号（若可得）＋ 本包全部源文件内容哈希。"""
    h = hashlib.sha256()
    for p in sorted((ROOT / "marksix").rglob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    src = h.hexdigest()[:12]
    commit = "nogit"
    try:
        import subprocess

        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip() or "nogit"
    except Exception:
        pass
    return f"{commit}+src{src}"
