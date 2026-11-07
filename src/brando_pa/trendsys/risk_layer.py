"""风险层：回撤熔断。三种模式全部实现、全部运行、全部报告。

  original_permanent  第五阶段原规则：全程高水位回撤 ≥ halt_dd 暂停买入，收窄到 resume_dd 恢复（现货空仓时回撤不会收窄 → 事实上永久停机）。
  state_machine       预登记的有限状态机 NORMAL / RISK_REDUCED / COOLDOWN / RECOVERY / EMERGENCY_HALT（见 preregistration.md §2.4）。
  none                无熔断（诊断用）。

接口：evaluate(ts, equity, daily_drop) → Decision(state, max_exposure, reduce_to, force_exit, changed)。
每个日线收盘调用一次；动作由仓位层在下一根执行 K 线开盘执行。数据失败安全态由 set_data_fault 控制：禁止买入、不强制卖出。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

NORMAL, RISK_REDUCED, COOLDOWN, RECOVERY, EMERGENCY_HALT = "NORMAL", "RISK_REDUCED", "COOLDOWN", "RECOVERY", "EMERGENCY_HALT"


@dataclass
class Decision:
    state: str
    max_exposure: float          # 新买入允许的最大总仓位（0 = 禁止买入）
    reduce_to: float | None      # 进入限仓状态时把持仓降到的比例（None = 不减仓）
    force_exit: bool             # 全部卖出
    changed: bool
    dd_ref: float
    dd_all: float
    data_fault: bool = False
    note: str = ""


class _Base:
    mode = "none"

    def __init__(self, params: dict | None = None):
        self.p = params or {}
        self.hwm_all = None; self.data_fault = False; self.log: list[dict] = []

    def set_data_fault(self, fault: bool, why: str = "") -> None:
        self.data_fault = bool(fault); self.fault_why = why

    def snapshot(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("p", "log") and not k.startswith("_")}

    def restore(self, d: dict) -> None:
        for k, v in d.items():
            if k in ("state_since", "cooldown_until", "recovery_since", "normal_since") and v is not None:
                v = pd.Timestamp(v)
            setattr(self, k, v)

    def _track(self, equity: float) -> float:
        self.hwm_all = equity if self.hwm_all is None else max(self.hwm_all, equity)
        return equity / self.hwm_all - 1 if self.hwm_all > 0 else 0.0

    def _emit(self, ts, equity, d: Decision, **extra) -> Decision:
        d.data_fault = self.data_fault
        if self.data_fault:
            d.max_exposure = 0.0; d.note = (d.note + "; " if d.note else "") + "data_fault:no_new_buys"
        self.log.append({"ts": str(ts), "equity": equity, "state": d.state, "max_exposure": d.max_exposure, "reduce_to": d.reduce_to, "force_exit": d.force_exit, "dd_ref": d.dd_ref, "dd_all": d.dd_all, "data_fault": d.data_fault, "note": d.note} | extra)
        return d


class NoHalt(_Base):
    mode = "none"

    def evaluate(self, ts, equity: float, daily_drop: float = 0.0, exposure: float = 1.0) -> Decision:
        dd = self._track(equity)
        return self._emit(ts, equity, Decision(NORMAL, 1.0, None, False, False, dd, dd))


class OriginalPermanentHalt(_Base):
    """第五阶段规则：dd ≤ −halt_dd → halted（禁止买入，不卖出）；halted 且 dd > −resume_dd → 恢复。全程高水位。"""
    mode = "original_permanent"

    def __init__(self, params: dict | None = None):
        super().__init__(params); self.halted = False; self.halt_count = 0

    def evaluate(self, ts, equity: float, daily_drop: float = 0.0, exposure: float = 1.0) -> Decision:
        dd = self._track(equity); prev = self.halted
        if dd <= -float(self.p.get("halt_dd", 0.20)):
            self.halted = True
        elif self.halted and dd > -float(self.p.get("resume_dd", 0.10)):
            self.halted = False
        if self.halted and not prev:
            self.halt_count += 1
        st = "HALTED" if self.halted else NORMAL
        return self._emit(ts, equity, Decision(st, 0.0 if self.halted else 1.0, None, False, prev != self.halted, dd, dd), halt_count=self.halt_count)


class CircuitBreaker(_Base):
    """预登记的有限状态机。所有阈值来自 config trendsys.risk.state_machine，不在运行中调整。"""
    mode = "state_machine"

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.state = NORMAL; self.hwm_ref = None; self.halt_count = 0
        self.state_since = None; self.cooldown_until = None; self.recovery_since = None; self.recovery_peak = None; self.normal_since = None
        self.transitions: list[dict] = []

    # ---- 参数 ----
    def _f(self, k, default):
        return float(self.p.get(k, default))

    def _go(self, ts, new: str, equity: float, why: str) -> None:
        self.transitions.append({"ts": str(ts), "from": self.state, "to": new, "equity": equity, "why": why, "halt_count": self.halt_count})
        self.state = new; self.state_since = pd.Timestamp(ts)
        if new == RECOVERY:
            self.recovery_since = pd.Timestamp(ts); self.recovery_peak = equity; self.hwm_ref = equity      # 高水位重置：避免永久停机
        if new == NORMAL:
            self.normal_since = pd.Timestamp(ts)
        if new == EMERGENCY_HALT:
            self.halt_count += 1
        if new == COOLDOWN:
            days = self._f("cooldown_days_per_halt", 30) * min(self.halt_count, int(self._f("cooldown_max_multiplier", 3)))
            self.cooldown_until = pd.Timestamp(ts) + pd.Timedelta(days=days)

    def evaluate(self, ts, equity: float, daily_drop: float = 0.0, exposure: float = 1.0) -> Decision:
        ts = pd.Timestamp(ts); dd_all = self._track(equity)
        if self.hwm_ref is None:
            self.hwm_ref = equity; self.state_since = ts; self.normal_since = ts
        prev = self.state; reduce_to = None; force_exit = False; why = ""
        if self.state in (NORMAL, RISK_REDUCED):
            self.hwm_ref = max(self.hwm_ref, equity)
        dd = equity / self.hwm_ref - 1 if self.hwm_ref else 0.0
        crash = daily_drop <= -self._f("crash_exit_daily_drop", 0.15) and exposure > 0.01      # 崩盘退出是退出规则：空仓时不触发（预登记澄清 2026-11-07）
        if self.state == NORMAL:
            if self.normal_since is not None and (ts - self.normal_since).days >= self._f("halt_count_reset_days", 180) and self.halt_count:
                self.halt_count = 0; why = "halt_count_reset"
            if dd <= -self._f("halt_dd", 0.20) or crash:
                self._go(ts, EMERGENCY_HALT, equity, "crash" if crash else f"dd {dd:.3f} ≤ −halt_dd"); force_exit = True
            elif dd <= -self._f("reduce_dd", 0.10):
                self._go(ts, RISK_REDUCED, equity, f"dd {dd:.3f} ≤ −reduce_dd"); reduce_to = self._f("reduce_cap", 0.5)
        elif self.state == RISK_REDUCED:
            if dd <= -self._f("halt_dd", 0.20) or crash:
                self._go(ts, EMERGENCY_HALT, equity, "crash" if crash else f"dd {dd:.3f} ≤ −halt_dd"); force_exit = True
            elif dd > -self._f("recover_dd", 0.05):
                self._go(ts, NORMAL, equity, f"dd {dd:.3f} > −recover_dd")
        elif self.state == EMERGENCY_HALT:
            self._go(ts, COOLDOWN, equity, "halt executed → cooldown")
        elif self.state == COOLDOWN:
            if self.cooldown_until is not None and ts >= self.cooldown_until:
                self._go(ts, RECOVERY, equity, "cooldown expired")
        elif self.state == RECOVERY:
            self.recovery_peak = max(self.recovery_peak or equity, equity); self.hwm_ref = self.recovery_peak
            dd = equity / self.hwm_ref - 1
            if dd <= -self._f("recovery_retrigger_dd", 0.10) or crash:
                self._go(ts, EMERGENCY_HALT, equity, "crash" if crash else f"recovery dd {dd:.3f} ≤ −retrigger"); force_exit = True
            elif (ts - self.recovery_since).days >= self._f("recovery_days", 60):
                self._go(ts, NORMAL, equity, "recovery completed")
        cap = {NORMAL: 1.0, RISK_REDUCED: self._f("reduce_cap", 0.5), RECOVERY: self._f("reduce_cap", 0.5), COOLDOWN: 0.0, EMERGENCY_HALT: 0.0}[self.state]
        d = Decision(self.state, cap, reduce_to, force_exit, prev != self.state, dd, dd_all, note=why or (self.transitions[-1]["why"] if prev != self.state else ""))
        return self._emit(ts, equity, d, halt_count=self.halt_count, hwm_ref=self.hwm_ref, hwm_all=self.hwm_all, cooldown_until=str(self.cooldown_until) if self.cooldown_until is not None else None)


def make_risk(mode: str, rc: dict) -> _Base:
    if mode == "none":
        return NoHalt({})
    if mode == "original_permanent":
        return OriginalPermanentHalt(rc.get("original_permanent", {}))
    if mode == "state_machine":
        return CircuitBreaker(rc.get("state_machine", {}))
    raise ValueError(mode)
