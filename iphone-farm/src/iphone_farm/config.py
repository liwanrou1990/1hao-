from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ApiConfig(BaseModel):
    base_url: str = "http://127.0.0.1:7390"
    timeout_s: float = 10
    action_timeout_s: float = 20


class PathsConfig(BaseModel):
    state_dir: str = "state"
    reports_dir: str = "reports_out"
    registry_file: str = "state/registry.json"


class RegistrationConfig(BaseModel):
    name_pattern: str = r"^IF(\d{2})$"
    require_udid_crosscheck: bool = True


class ControlConfig(BaseModel):
    require_echo_match: bool = True
    verify_host_before_action: bool = True
    per_device_lock_timeout_s: float = 30


class BroadcastConfig(BaseModel):
    max_parallel: int = 8
    stagger_ms: int = 50
    allowed_actions: list[str] = Field(default_factory=lambda: ["home", "open_app", "screenshot", "wake"])
    forbidden_in_broadcast: list[str] = Field(default_factory=lambda: ["sms_open", "sms_compose", "power"])


class RecoveryConfig(BaseModel):
    enable_match: bool = True
    enable_pair: bool = True
    enable_calibration: bool = True
    enable_app_restart: bool = False
    app_restart_cooldown_s: int = 600
    max_recovery_attempts_per_10min: int = 3


class MonitorConfig(BaseModel):
    poll_interval_s: float = 3
    usb_absent_alert_after_s: float = 60
    no_frame_alert_after_s: float = 15
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)


class ScaleTestConfig(BaseModel):
    levels: list[int] = Field(default_factory=lambda: [3, 5, 10, 15, 20, 30])
    ble_stability_minutes: float = 10
    screenshot_samples_per_device: int = 5


class FarmConfig(BaseModel):
    api: ApiConfig = Field(default_factory=ApiConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    registration: RegistrationConfig = Field(default_factory=RegistrationConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    broadcast: BroadcastConfig = Field(default_factory=BroadcastConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    scale_test: ScaleTestConfig = Field(default_factory=ScaleTestConfig)

    root: Path = Field(default=PROJECT_ROOT, exclude=True)

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def state_dir(self) -> Path:
        return self.resolve(self.paths.state_dir)

    @property
    def reports_dir(self) -> Path:
        return self.resolve(self.paths.reports_dir)

    @property
    def registry_file(self) -> Path:
        return self.resolve(self.paths.registry_file)


def load_config(path: str | os.PathLike[str] | None = None, root: Path | None = None) -> FarmConfig:
    root = root or PROJECT_ROOT
    cfg_path = Path(path) if path else root / "config" / "farm.yaml"
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    env_url = os.environ.get("IFARM_API_URL")
    if env_url:
        data.setdefault("api", {})["base_url"] = env_url
    cfg = FarmConfig(**data)
    cfg.root = root
    return cfg


def load_groups(path: str | os.PathLike[str] | None = None, root: Path | None = None) -> dict[str, list[int]]:
    root = root or PROJECT_ROOT
    p = Path(path) if path else root / "config" / "groups.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    groups = data.get("groups", {}) or {}
    return {str(k): [int(x) for x in v] for k, v in groups.items()}


def load_sms_policy(path: str | os.PathLike[str] | None = None, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    p = Path(path) if path else root / "config" / "sms_policy.yaml"
    if not p.exists():
        return {"authorized_contacts": [], "test_devices_only": True, "require_manual_delivery_confirmation": True}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
