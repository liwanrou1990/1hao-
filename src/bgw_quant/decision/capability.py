"""品种真实能力登记：根据官方 API 确认每个品种支持的订单类型、交易方向、流动性与容量。

官方 RWA 行情/交易接口（SDK 枚举）只有链上现货代币的买入/卖出（swap）；没有保证金、杠杆或借券做空接口。
因此：
  * spot_long = 可买入持有、卖出平仓（现货低买高卖）；
  * leverage_long / short 默认不支持，除非 config/symbols.yaml 的 short_support.verified / leverage_support.verified
    登记了经人工核实的真实渠道；引擎只会使用登记为支持的方向，绝不把现货卖出当作开空。
live 模式下由 stockInfo 填充市场状态、单笔买卖上限、发行商；SIMULATED 模式下标记“未经 API 核实”。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import load_yaml


@dataclass
class ProductCapability:
    symbol: str
    spot_long: bool = True
    leverage_long: float = 1.0          # 1.0 = 无杠杆
    short: bool = False
    short_borrow_rate_annual: float = 0.0
    order_types: list[str] = field(default_factory=lambda: ["market_swap"])
    data_source: str | None = None
    chain: str | None = None
    contract: str | None = None
    market_status: str | None = None
    tx_max_buy_usd: float | None = None
    tx_max_sell_usd: float | None = None
    tx_min_buy_usd: float | None = None
    verified_by_api: bool = False
    verification_status: str = "unverified"     # verified | unverified | unsupported
    notes: list[str] = field(default_factory=list)

    @property
    def directions(self) -> list[str]:
        out = []
        if self.spot_long:
            out.append("long")
        if self.short:
            out.append("short")
        return out

    def to_dict(self) -> dict:
        return {**self.__dict__, "directions": self.directions}


def capability_for(symbol: str, api_asset: dict[str, Any] | None = None) -> ProductCapability:
    cfg = load_yaml("symbols.yaml")
    sym_cfg = next((s for s in cfg.get("symbols", []) if s["ticker"] == symbol), {})
    cap = ProductCapability(symbol=symbol)
    if not sym_cfg.get("tradable", False):
        cap.spot_long = False
        cap.verification_status = "unverified"
        cap.notes.append("资产身份/发行商/合约尚未通过官方 stockList/stockInfo 核实：在核实前不纳入可交易（既不判定支持，也不判定不支持）")
    ss = (cfg.get("short_support") or {})
    v = (ss.get("verified") or {}).get(symbol)
    if v and v.get("shortable"):
        cap.short = True
        cap.short_borrow_rate_annual = float(v.get("short_borrow_rate_annual", 0.05))
        cap.order_types.append(f"short:{v.get('short_venue', '未注明渠道')}")
        cap.notes.append(f"做空渠道由人工登记：{v.get('short_venue')}")
    else:
        cap.notes.append("官方 RWA 接口无借券/保证金做空；未登记可核实渠道 → 不做空")
    lv = ((cfg.get("leverage_support") or {}).get("verified") or {}).get(symbol)
    if lv and float(lv.get("max_leverage", 1)) > 1:
        cap.leverage_long = float(lv["max_leverage"])
        cap.order_types.append(f"leverage_long:{lv.get('venue', '未注明渠道')}")
    else:
        cap.notes.append("官方 RWA 接口无杠杆做多 → 不加杠杆")
    if api_asset:
        cap.verified_by_api = True
        cap.verification_status = "verified"
        if api_asset.get("listed", True):
            cap.spot_long = True            # API 核实上线 → 现货可买卖（以 stockInfo 市场状态为准）
        contracts = api_asset.get("contracts") or []
        online = [c for c in contracts if c.get("status") == "online"] or contracts
        if online:
            cap.chain, cap.contract, cap.data_source = online[0].get("chain"), online[0].get("contract"), online[0].get("data_source")
        info = api_asset.get("stock_info") or {}
        assets = info.get("chain_assets") or []
        a = next((x for x in assets if x.get("contract") == cap.contract), assets[0] if assets else None)
        if a:
            cap.market_status = a.get("market_status") or a.get("market_status_code")
            for k_src, k_dst in (("tx_maximum_buy_usd", "tx_max_buy_usd"), ("tx_maximum_sell_usd", "tx_max_sell_usd"), ("tx_minimum_buy_usd", "tx_min_buy_usd")):
                try:
                    setattr(cap, k_dst, float(a[k_src]) if a.get(k_src) is not None else None)
                except (TypeError, ValueError):
                    pass
        if not api_asset.get("listed", True):
            cap.spot_long = False
            cap.verification_status = "unsupported"
            cap.notes.append("官方 stockList 中不存在该 ticker → 产品不支持")
        elif cap.market_status in ("close", "closed", "paused"):
            cap.notes.append(f"市场状态 {cap.market_status}：当前不可交易")
    else:
        cap.notes.append("未经 API 核实（SIMULATED 或未连接）：现货能力按配置假设")
    return cap
