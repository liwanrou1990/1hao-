"""配置加载：config/brando_pa.yaml + 环境变量。密钥只从环境变量读取，绝不写入日志。"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "brando_pa.yaml"


@lru_cache(maxsize=1)
def _raw_cfg() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cfg() -> dict[str, Any]:
    """返回配置深拷贝（调用方可安全修改，用于参数网格）。"""
    return copy.deepcopy(_raw_cfg())


def deep_update(base: dict, upd: dict) -> dict:
    for k, v in (upd or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


def resolve(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def own_data_dir(c: dict | None = None) -> Path:
    c = c or cfg()
    d = resolve(os.environ.get("BRANDO_DATA_DIR", c["data"]["own_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def spot90_data_dir(c: dict | None = None) -> Path:
    c = c or cfg()
    return resolve(os.environ.get("SPOT90_DATA_DIR", c["data"]["spot90_dir"]))


def report_dir(c: dict | None = None) -> Path:
    c = c or cfg()
    d = resolve(os.environ.get("BRANDO_REPORT_DIR", c["paths"]["report_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def live_trading_allowed(c: dict | None = None, cli_confirm: bool = False) -> bool:
    """真实下单三重闸门：配置开关 + 环境变量确认 + 命令行参数。任何一个缺失都返回 False。"""
    c = c or cfg()
    ex = c.get("execution", {})
    if not bool(ex.get("live_trading", False)):
        return False
    if os.environ.get(ex.get("live_confirm_env", "BRANDO_LIVE_CONFIRM"), "") != "I_UNDERSTAND":
        return False
    return bool(cli_confirm)
