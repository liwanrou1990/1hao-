"""订单/经纪抽象。PaperBroker 用参考价成交并记账；LiveBroker 是可替换适配器的占位——没有任何真实交易所下单代码，且三重闸门未全部打开时直接拒绝。"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..config import live_trading_allowed


class ExecutionForbidden(RuntimeError):
    pass


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: str                 # buy / sell
    qty: float
    instrument: str
    ref_price: float
    reduce_only: bool = False
    status: str = "new"       # new / filled / rejected / duplicate
    filled_price: float | None = None
    fee: float = 0.0
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def make_client_order_id(*parts) -> str:
    """确定性订单号：同一设置/同一 K 线/同一动作 → 同一 ID，重复提交被识别为 duplicate。"""
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


class Broker:
    name = "abstract"

    def place_order(self, order: Order) -> Order:  # pragma: no cover
        raise NotImplementedError

    def seen(self, client_order_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, costs: dict, order_ids: set[str] | None = None):
        self.costs = costs
        self.order_ids: set[str] = set(order_ids or [])
        self.orders: list[Order] = []

    def seen(self, client_order_id: str) -> bool:
        return client_order_id in self.order_ids

    def place_order(self, order: Order) -> Order:
        if self.seen(order.client_order_id):
            order.status, order.note = "duplicate", "订单号已存在，拒绝重复下单"
            return order
        slip = float(self.costs[order.instrument]["slippage_bps"]) / 1e4
        fee = float(self.costs[order.instrument]["taker_fee_bps"]) / 1e4
        order.filled_price = order.ref_price * (1 + slip) if order.side == "buy" else order.ref_price * (1 - slip)
        order.fee = order.filled_price * order.qty * fee
        order.status = "filled"
        self.order_ids.add(order.client_order_id)
        self.orders.append(order)
        return order


class LiveBroker(Broker):
    """真实交易占位。即便实现了交易所适配器，也必须：config.execution.live_trading=true + 环境变量 BRANDO_LIVE_CONFIRM=I_UNDERSTAND + 命令行 --confirm-live。
    本仓库不包含任何真实下单实现；place_order 永远抛出 ExecutionForbidden。"""
    name = "live_disabled"

    def __init__(self, c: dict, cli_confirm: bool = False):
        if not live_trading_allowed(c, cli_confirm):
            self.reason = "真实交易未启用（需配置开关 + 环境变量确认 + 命令行确认三重闸门）"
        else:
            self.reason = "真实交易适配器未实现：本版本不含任何交易所私有接口调用"

    def seen(self, client_order_id: str) -> bool:
        return False

    def place_order(self, order: Order) -> Order:
        raise ExecutionForbidden(self.reason)
