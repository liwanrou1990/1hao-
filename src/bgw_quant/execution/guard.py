"""执行安全守卫（V1）。

V1 只允许 research_only：任何“真实下单/签名/私钥/钱包连接”的调用都会被拒绝并记录。
未来 V2 真实交易接口必须同时满足：显式授权令牌、人工确认、单笔/日限额、紧急停机开关；此处预留接口与校验逻辑，但默认全部关闭。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import get_settings

FORBIDDEN_KEYS = ("PRIVATE_KEY", "MNEMONIC", "SEED_PHRASE", "WALLET_SECRET")


class ExecutionForbidden(RuntimeError):
    pass


@dataclass
class ExecutionGuard:
    mode: str = "research_only"
    kill_switch: bool = True                 # 默认停机
    explicit_authorization: str | None = None
    human_confirmed: bool = False
    max_order_usd: float = 0.0
    max_daily_usd: float = 0.0
    audit: list[dict] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "ExecutionGuard":
        s = get_settings()
        g = cls(mode=s.execution_mode or "research_only")
        if g.mode != "research_only":
            g.audit.append({"ts": datetime.now(timezone.utc).isoformat(), "event": "V1 不支持非研究模式，已强制回退 research_only"})
            g.mode = "research_only"
        return g

    def assert_no_private_material_in_env(self) -> list[str]:
        """提示：环境中不应出现私钥类变量；V1 绝不读取其值。"""
        found = [k for k in os.environ if any(f in k.upper() for f in FORBIDDEN_KEYS)]
        if found:
            self.audit.append({"ts": datetime.now(timezone.utc).isoformat(), "event": f"检测到疑似私钥环境变量 {found}，V1 不会读取"})
        return found

    def check_live_order(self, notional_usd: float) -> None:
        """V2 预留：真实下单前的全部闸门。V1 永远抛出 ExecutionForbidden。"""
        self.audit.append({"ts": datetime.now(timezone.utc).isoformat(), "event": "live_order_attempt", "notional_usd": notional_usd})
        if self.mode == "research_only":
            raise ExecutionForbidden("V1 为仅研究模式：不连接钱包、不请求私钥、不签名、不下单")
        if self.kill_switch:
            raise ExecutionForbidden("紧急停机开关处于开启状态")
        if not self.explicit_authorization:
            raise ExecutionForbidden("缺少显式授权")
        if not self.human_confirmed:
            raise ExecutionForbidden("缺少人工确认")
        if notional_usd > self.max_order_usd or self.max_order_usd <= 0:
            raise ExecutionForbidden("超过单笔限额")

    def status(self) -> dict:
        return {"mode": self.mode, "kill_switch": self.kill_switch, "live_trading_possible": False,
                "wallet_connected": False, "private_key_requested": False,
                "note": "V1 仅研究/回测/模拟；真实交易需 V2 显式授权+人工确认+限额+紧急停机"}
