"""配置：YAML 参数 + 环境变量（密钥只从环境变量读取，从不写入代码或日志）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "spot90.yaml"


@lru_cache(maxsize=1)
def cfg() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    return c


def resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def data_dir() -> Path:
    d = resolve(os.environ.get("SPOT90_DATA_DIR", cfg()["paths"]["data_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_dir() -> Path:
    d = resolve(os.environ.get("SPOT90_REPORT_DIR", cfg()["paths"]["report_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir() -> Path:
    d = resolve(os.environ.get("SPOT90_MODEL_DIR", cfg()["paths"]["model_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def execution_allowed() -> bool:
    """第一阶段永远为 False：系统没有任何下单代码路径。"""
    return False
