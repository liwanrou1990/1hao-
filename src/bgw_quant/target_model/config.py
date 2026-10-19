"""固定收益目标模型配置与目标取值规则。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capital.tiers import TierConfig, load_tiers
from ..config import load_yaml


@dataclass
class TargetModelConfig:
    target_mode: str = "lower"
    overlap_rule: str = "first"
    position_fraction: float = 0.5
    stop_loss_pct: float = 0.5
    take_profit_net_pct: float = 0.3
    max_hold_bars: int = 60
    max_daily_loss_pct: float = 1.0
    max_trades_per_day: int = 60
    cooldown_bars: int = 2
    batch_bars: int = 5
    participation_rate: float = 0.10
    flat_before_close_bars: int = 5
    teaching: dict = field(default_factory=lambda: {"require_local_low_bars": 5, "min_edge_over_cost": 0.0})
    strategy_grid: dict = field(default_factory=dict)
    stability_objective: dict = field(default_factory=dict)
    validation_capitals: list[float] = field(default_factory=lambda: [100000, 300000, 1000000, 10000000])

    def with_overrides(self, **kw) -> "TargetModelConfig":
        d = self.__dict__.copy()
        d.update({k: v for k, v in kw.items() if v is not None})
        return TargetModelConfig(**d)


def load_target_config() -> TargetModelConfig:
    raw = load_yaml("target_model.yaml")
    fields = {k: v for k, v in raw.items() if k in TargetModelConfig.__dataclass_fields__}
    return TargetModelConfig(**fields)


def target_for_capital(capital_usd: float, tiers: TierConfig | None = None, mode: str = "lower",
                       overlap_rule: str = "first") -> tuple[float | None, str | None, str]:
    """返回 (目标日净收益率, 档位 id, 说明)。缺档返回 (None, None, 说明)。"""
    tiers = tiers or load_tiers()
    hits = tiers.find(capital_usd)
    if not hits:
        return None, None, f"本金 {capital_usd:,.0f} USD 无对应档位（缺档），需显式指定目标"
    t = hits[0] if overlap_rule == "first" else hits[-1]
    lo, hi = t.target_daily_pct
    pct = {"lower": lo, "mid": (lo + hi) / 2, "upper": hi}.get(mode, lo)
    note = f"档位 {t.id} [{t.min_usd:,.0f}-{t.max_usd:,.0f}] 目标区间 {lo}%–{hi}%，取 {mode} = {pct:.3f}%"
    if len(hits) > 1:
        note += f"；本金同时命中 {[h.id for h in hits]}，按 overlap_rule={overlap_rule} 取 {t.id}"
    return pct / 100, t.id, note
