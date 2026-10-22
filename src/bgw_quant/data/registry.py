"""数据源注册与选择：auto/live/simulated。live 不可用时明确退回 simulated 并给出阻塞说明。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import get_settings
from .base import ProbeResult
from .bitget_rwa import BitgetRwaClient
from .crypto import BitgetSpotPublicClient
from .massive import MassiveClient
from .simulated import SimulatedSource


@dataclass
class DataPlan:
    mode: str                      # live | simulated
    native_source: object
    token_source: object
    probes: list[ProbeResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    crypto_source: object = None          # 加密货币行情：BitgetSpotPublicClient（公开）或 SimulatedSource
    crypto_mode: str = "simulated"        # live | simulated（与股票代币模式独立判定）

    @property
    def is_simulated(self) -> bool:
        return self.mode == "simulated"

    def banner(self) -> str:
        c = f"加密货币行情={'Bitget 公开现货 K 线' if self.crypto_mode == 'live' else 'SIMULATED'}"
        if self.is_simulated:
            return f"⚠ 当前为 SIMULATED 模拟数据模式：所有价格、成交与绩效均来自确定性模拟，不是真实行情。{c}"
        return f"LIVE 数据模式：原生基准=Massive，代币成交=Bitget Wallet RWA；{c}。"


def _attach_crypto(plan: DataPlan, mode: str, do_probe: bool) -> DataPlan:
    """加密货币行情源独立判定：公开接口可达即 live，否则 SIMULATED 并记录阻塞。"""
    if mode in ("auto", "live"):
        c = BitgetSpotPublicClient()
        if do_probe:
            pc = c.probe()
            plan.probes.append(pc)
            if pc.ok:
                plan.crypto_source, plan.crypto_mode = c, "live"
                return plan
            plan.blockers.append(f"加密货币公开行情阻塞：{pc.detail}")
        else:
            plan.crypto_source, plan.crypto_mode = c, "live"
            return plan
        if mode == "live":
            raise RuntimeError("强制 live 模式但加密货币公开行情不可达")
    plan.crypto_source = plan.token_source if getattr(plan.token_source, "is_simulated", False) else SimulatedSource()
    plan.crypto_mode = "simulated"
    return plan


def build_data_plan(force_mode: str | None = None, do_probe: bool = True) -> DataPlan:
    return _attach_crypto(_build_plan(force_mode, do_probe), force_mode or get_settings().data_mode, do_probe)


def _build_plan(force_mode: str | None = None, do_probe: bool = True) -> DataPlan:
    s = get_settings()
    mode = force_mode or s.data_mode
    probes: list[ProbeResult] = []
    blockers: list[str] = []
    if mode in ("auto", "live"):
        bgw, mas = BitgetRwaClient(), MassiveClient()
        if do_probe:
            pb, pm = bgw.probe(), mas.probe()
            probes += [pb, pm]
            if pb.blocked:
                blockers.append(f"Bitget RWA 阻塞：{pb.detail}")
            if pm.blocked:
                blockers.append(f"Massive 基准不可用（可选）：{pm.detail}")
            live_ok = pb.ok                       # 以 Bitget 官方代币行情为准；Massive 原生基准可选
            native = mas if pm.ok else None
        else:
            live_ok = bgw.has_credentials
            native = mas if mas.has_credentials else None
        if live_ok:
            return DataPlan("live", native, bgw, probes, blockers)
        if mode == "live":
            raise RuntimeError("强制 live 模式但数据源不可用：" + "; ".join(blockers))
    sim = SimulatedSource()
    probes.append(sim.probe())
    return DataPlan("simulated", sim, sim, probes, blockers)
