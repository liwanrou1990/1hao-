"""六类策略定义（研究对比口径）：

  A fixed_stock   固定单只股票（每只分别评估；“事前选择”版本按前几日样本外表现挑选，见 backtest）
  B fixed_crypto  固定单种加密货币
  C stock_rotation   股票之间动态轮动（同时最多 max_assets 只）
  D crypto_rotation  加密货币之间动态轮动
  E cross_rotation   股票与加密货币跨市场动态轮动
  F portfolio        多资产组合并行交易（本金按资产均分，各自独立决策）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategySpec:
    key: str
    family: str                 # A..F
    name: str
    universe: tuple[str, ...]
    rotation: bool              # True: 动态选择/切换；False: 固定资产集合（单资产或均分组合）
    max_assets: int             # 同时持有的最大资产数
    note: str = ""


def build_specs(stocks: list[str], cryptos: list[str], max_assets_rotation: int = 2) -> list[StrategySpec]:
    specs: list[StrategySpec] = []
    for s in stocks:
        specs.append(StrategySpec(f"A:{s}", "A", f"固定股票 {s}", (s,), False, 1))
    for c in cryptos:
        specs.append(StrategySpec(f"B:{c}", "B", f"固定加密 {c}", (c,), False, 1))
    specs.append(StrategySpec("C:stock_rotation", "C", "股票动态轮动", tuple(stocks), True, max_assets_rotation))
    specs.append(StrategySpec("D:crypto_rotation", "D", "加密货币动态轮动", tuple(cryptos), True, max_assets_rotation))
    specs.append(StrategySpec("E:cross_rotation", "E", "股票×加密跨市场轮动", tuple(stocks + cryptos), True, max_assets_rotation))
    specs.append(StrategySpec("F:portfolio", "F", "多资产组合并行（均分）", tuple(stocks + cryptos), False, len(stocks + cryptos)))
    return specs
