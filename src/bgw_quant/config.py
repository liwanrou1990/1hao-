"""全局配置：环境变量 + YAML 配置文件。密钥只从本地环境变量读取。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    bgw_api_key: str = Field(default="", alias="BGW_API_KEY")
    bgw_api_secret: str = Field(default="", alias="BGW_API_SECRET")
    bgw_base_url: str = Field(default="https://bopenapi.bgwapi.io", alias="BGW_BASE_URL")

    massive_api_key: str = Field(default="", alias="MASSIVE_API_KEY")
    massive_base_url: str = Field(default="https://api.massive.com", alias="MASSIVE_BASE_URL")

    data_mode: Literal["auto", "live", "simulated"] = Field(default="auto", alias="BGWQ_DATA_MODE")
    db_path: str = Field(default="data_store/bgwq.duckdb", alias="BGWQ_DB_PATH")
    report_dir: str = Field(default="reports_out", alias="BGWQ_REPORT_DIR")
    execution_mode: str = Field(default="research_only", alias="BGWQ_EXECUTION_MODE")

    @property
    def has_bgw_keys(self) -> bool:
        return bool(self.bgw_api_key and self.bgw_api_secret)

    @property
    def has_massive_key(self) -> bool:
        return bool(self.massive_api_key)

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(name: str, payload: dict[str, Any]) -> Path:
    path = CONFIG_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return path


def research_symbols(tradable_only: bool = False, asset_class: str | None = "stock") -> list[dict[str, Any]]:
    """研究标的。asset_class: "stock"（默认，保持旧行为）| "crypto" | None（全部）。"""
    cfg = load_yaml("symbols.yaml")
    stocks = [{**s, "asset_class": s.get("asset_class", "stock")} for s in cfg.get("symbols", [])]
    crypto = [{**s, "asset_class": "crypto"} for s in cfg.get("crypto_symbols", [])]
    syms = {"stock": stocks, "crypto": crypto}.get(asset_class, stocks + crypto) if asset_class else stocks + crypto
    if tradable_only:
        syms = [s for s in syms if s.get("tradable", False)]
    return syms


def asset_meta(symbol: str) -> dict[str, Any]:
    for s in research_symbols(asset_class=None):
        if s["ticker"] == symbol:
            return s
    return {"ticker": symbol, "asset_class": "stock", "tradable": False}


def asset_class_of(symbol: str) -> str:
    return str(asset_meta(symbol).get("asset_class", "stock"))


def env_flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
