"""本金档位（管理员可改 config/tiers.yaml）。系统只标记问题，不擅自补造规则。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import load_yaml, save_yaml


@dataclass
class Tier:
    id: str
    min_usd: float
    max_usd: float
    target_daily_pct: tuple[float, float]
    period_days: int

    def contains(self, capital: float) -> bool:
        return self.min_usd <= capital <= self.max_usd

    def representative_capitals(self, points=("min", "mid", "max")) -> dict[str, float]:
        m = {"min": self.min_usd, "mid": (self.min_usd + self.max_usd) / 2, "max": self.max_usd}
        return {p: m[p] for p in points}


@dataclass
class TierConfig:
    tiers: list[Tier]
    accrual_basis: str
    period_unit: str
    representative_points: tuple[str, ...]
    issues: list[str] = field(default_factory=list)

    def find(self, capital: float) -> list[Tier]:
        return [t for t in self.tiers if t.contains(capital)]

    def to_dict(self) -> dict:
        return {"accrual_basis": self.accrual_basis, "period_unit": self.period_unit,
                "tiers": [t.__dict__ | {"target_daily_pct": list(t.target_daily_pct)} for t in self.tiers],
                "issues": self.issues}


def validate_tiers(tiers: list[Tier], accrual_basis: str, period_unit: str) -> list[str]:
    issues = []
    if accrual_basis == "unknown":
        issues.append("目标百分比的计息口径未知（单利/复利、按日还是按周期），未做任何假设。")
    if period_unit.startswith("unknown"):
        issues.append("周期天数的含义未知（交易日/自然日），未做任何假设。")
    ordered = sorted(tiers, key=lambda t: t.min_usd)
    for a, b in zip(ordered, ordered[1:]):
        if b.min_usd <= a.max_usd:
            issues.append(f"档位重叠：{a.id}[{a.min_usd:,.0f}-{a.max_usd:,.0f}] 与 {b.id}[{b.min_usd:,.0f}-{b.max_usd:,.0f}] "
                          f"在 {b.min_usd:,.0f}-{a.max_usd:,.0f} 重叠，该区间本金同时命中两档。")
        elif b.min_usd > a.max_usd + 1:
            issues.append(f"缺档：{a.max_usd + 1:,.0f}-{b.min_usd - 1:,.0f} 美元无对应档位。")
    for t in tiers:
        if t.target_daily_pct[0] > t.target_daily_pct[1]:
            issues.append(f"{t.id} 目标区间下限大于上限。")
    return issues


def load_tiers() -> TierConfig:
    raw = load_yaml("tiers.yaml")
    tiers = [Tier(t["id"], float(t["min_usd"]), float(t["max_usd"]), tuple(t["target_daily_pct"]), int(t["period_days"]))
             for t in raw["tiers"]]
    cfg = TierConfig(tiers, raw.get("accrual_basis", "unknown"), raw.get("period_unit", "unknown"),
                     tuple(raw.get("representative_points", ["min", "mid", "max"])))
    cfg.issues = validate_tiers(tiers, cfg.accrual_basis, cfg.period_unit)
    return cfg


def admin_update_tiers(new_tiers: list[dict], accrual_basis: str | None = None, period_unit: str | None = None) -> TierConfig:
    """管理员修改档位：写回 YAML，并重新校验（校验只提示，不阻止保存）。"""
    raw = load_yaml("tiers.yaml")
    raw["tiers"] = new_tiers
    if accrual_basis:
        raw["accrual_basis"] = accrual_basis
    if period_unit:
        raw["period_unit"] = period_unit
    save_yaml("tiers.yaml", raw)
    return load_tiers()
