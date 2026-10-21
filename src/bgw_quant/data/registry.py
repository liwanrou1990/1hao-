"""数据源注册与选择：auto/live/simulated。live 不可用时明确退回 simulated 并给出阻塞说明。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import get_settings
from .base import ProbeResult
from .bitget_rwa import BitgetRwaClient
from .massive import MassiveClient
from .simulated import SimulatedSource


@dataclass
class DataPlan:
    mode: str                      # live | simulated
    native_source: object
    token_source: object
    probes: list[ProbeResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def is_simulated(self) -> bool:
        return self.mode == "simulated"

    def banner(self) -> str:
        if self.is_simulated:
            return "⚠ 当前为 SIMULATED 模拟数据模式：所有价格、成交与绩效均来自确定性模拟，不是真实行情。"
        return "LIVE 数据模式：原生基准=Massive，代币成交=Bitget Wallet RWA。"


def build_data_plan(force_mode: str | None = None, do_probe: bool = True) -> DataPlan:
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
