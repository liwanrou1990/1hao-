"""第十三阶段影子账户：复用第九阶段前瞻引擎（ForwardRunner / ForwardAccount / PortfolioAllocator / CircuitBreaker / 实时报价成交规则），
只增加：① 冻结的 6 个策略规格（A 原 B2 = Mac 账户 B 同规则同成本；B BTC SMA200 = Mac 账户 A 同规则；C/D/E 买入持有；F 实验对照 = B2 + 3 ATR 波动跟踪减仓）；
② 账户级分配器参数覆盖（买入持有不做漂移再平衡）；③ F 的波动跟踪状态（按已收盘日线计算，持久化在 state.json）。
独立目录 reports_out/brando_pa/phase13_forward、独立行情仓 data_store/brando_pa/phase13_bars、独立锁；不读写 Mac 三账户目录。"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import cfg, own_data_dir, report_dir
from ..data.store import BarStore
from ..forward.accounts import SYMS, ForwardAccount, ForwardRunner
from ..forward.datafeed import MarketDataService
from ..pa11.structure import atr as _atr, structure_state, swings
from ..usdt.allocator import PortfolioAllocator

PHASE = "phase13_forward"
BH_ALL = lambda w: {"both": dict(w), "btc_only": dict(w), "eth_only": dict(w), "none": dict(w)}   # 买入持有：与趋势状态无关


def shadow_specs(c: dict) -> dict:
    """冻结规格（见 docs/brando_pa_validation/phase13_forward/freeze_manifest.*）。"""
    return {
        "A_B2_state_machine": {"weights": c["usdt"]["models"]["B_full_single"], "risk_mode": "state_machine", "label": "A：原 B2 状态机（= Mac 账户 B_modelB_state_machine 同规则同成本）", "kind": "trend"},
        "B_BTC_SMA200": {"weights": {"both": {"BTCUSDT": 1.0}, "btc_only": {"BTCUSDT": 1.0}, "eth_only": {}, "none": {}}, "risk_mode": "none", "label": "B：BTC SMA200（= Mac 账户 A_BTC_SMA200 同规则）", "kind": "trend"},
        "C_BUYHOLD_BTC": {"weights": BH_ALL({"BTCUSDT": 1.0}), "risk_mode": "none", "label": "C：BTC 买入持有（首轮买入，不再交易）", "kind": "buyhold", "alloc_params": {"drift_band": 10.0}},
        "D_BUYHOLD_ETH": {"weights": BH_ALL({"ETHUSDT": 1.0}), "risk_mode": "none", "label": "D：ETH 买入持有", "kind": "buyhold", "alloc_params": {"drift_band": 10.0}},
        "E_BUYHOLD_5050": {"weights": BH_ALL({"BTCUSDT": 0.5, "ETHUSDT": 0.5}), "risk_mode": "none", "label": "E：BTC/ETH 50:50 买入持有（不再平衡）", "kind": "buyhold", "alloc_params": {"drift_band": 10.0}},
        "F_B2_vol_trail": {"weights": c["usdt"]["models"]["B_full_single"], "risk_mode": "state_machine", "label": "F：实验对照 = B2 + 3 ATR 波动跟踪减仓（Phase 12 E_B2_vol_trail 规则）", "kind": "trend", "vol_trail": {"atr_mult": 3.0, "atr_n": 14, "swing_k": 3, "reduce_to": 0.5}},
    }


class ShadowAllocator(PortfolioAllocator):
    """在 PortfolioAllocator 之上加“每资产倍数”（F 的减仓状态）；倍数全为 1 时与原分配器完全一致。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.mult = {s: 1.0 for s in self.symbols}

    def model_targets(self, trend: dict) -> dict:
        w, key = super().model_targets(trend); return {s: w[s] * self.mult.get(s, 1.0) for s in self.symbols}, key


class ShadowAccount(ForwardAccount):
    def __init__(self, name: str, spec: dict, c: dict, log_dir: Path, initial: float, fee_bps: float, slip_bps: float):
        c2 = copy.deepcopy(c)
        if spec.get("alloc_params"):
            c2["usdt"]["allocator"] = {**c2["usdt"]["allocator"], **spec["alloc_params"]}
        super().__init__(name, spec, c2, log_dir, initial, fee_bps, slip_bps)
        self.allocs = {k: ShadowAllocator(spec["weights"], SYMS, c2["usdt"]["allocator"], 0.0 if k == "ideal" else fee_bps, 0.0 if k == "ideal" else slip_bps) for k in self.LEDGERS}
        for k in self.LEDGERS:
            self.allocs[k].last_target = self.state.get("alloc_last_target")
        self.state.setdefault("vol_trail", {s: {"reduced": False, "peak_close": None} for s in SYMS}); self.bars_ref = None

    def update_vol_trail(self, close_t: pd.Timestamp, on: dict) -> dict:
        """F：用 ≤ close_t 的已收盘日线更新每资产状态：持有中 close < peak − 3 ATR → reduced（目标 × 0.5）；reduced 且 close > 最近已确认摆动高点 → 恢复；趋势 OFF → 重置。"""
        vt = self.spec.get("vol_trail")
        if not vt or self.bars_ref is None:
            return {}
        notes = {}
        for s in SYMS:
            d1 = self.bars_ref[s]["1d"]; d1 = d1[(pd.to_datetime(d1["ts"], utc=True) + pd.Timedelta(days=1)) <= close_t].reset_index(drop=True)
            st = self.state["vol_trail"].setdefault(s, {"reduced": False, "peak_close": None})
            if len(d1) < vt["atr_n"] + 2 * vt["swing_k"] + 2:
                continue
            close = float(d1["close"].iloc[-1]); a = float(_atr(d1, vt["atr_n"]).iloc[-1])
            if not on.get(s):
                st["reduced"] = False; st["peak_close"] = None; self.allocs["cost"].mult[s] = self.allocs["ideal"].mult[s] = 1.0; notes[s] = "trend_off_reset"; continue
            st["peak_close"] = close if st["peak_close"] is None else max(float(st["peak_close"]), close)
            sw = swings(d1, vt["swing_k"]); ss = structure_state(sw, len(d1) - 1)
            if not st["reduced"] and np.isfinite(a) and close < st["peak_close"] - vt["atr_mult"] * a:
                st["reduced"] = True; notes[s] = f"vol_trail_reduce: close {close:.0f} < peak {st['peak_close']:.0f} − {vt['atr_mult']}×ATR {a:.0f}"
            elif st["reduced"] and ss["last_high"] and close > ss["last_high"]:
                st["reduced"] = False; st["peak_close"] = close; notes[s] = f"vol_trail_restore: close {close:.0f} > last confirmed swing high {ss['last_high']:.0f}"
            m = vt["reduce_to"] if st["reduced"] else 1.0; self.allocs["cost"].mult[s] = m; self.allocs["ideal"].mult[s] = m
        return notes

    def decide(self, close_t, on, prices, *a, **k):
        notes = self.update_vol_trail(close_t, on)
        if notes:
            self.log("vol_trail", signal_ts=str(close_t), notes=notes, state=self.state["vol_trail"])
        return super().decide(close_t, on, prices, *a, **k)


class ShadowRunner(ForwardRunner):
    """与 ForwardRunner 同一引擎（决策 / 成交 / 事件链 / 状态文件），只换账户集合与目录。"""

    def __init__(self, c: dict | None = None, log_dir: Path | None = None, store: BarStore | None = None, accounts: list[str] | None = None, mode: str = "forward", quote_provider=None):
        c = c or cfg(); log_dir = Path(log_dir or (report_dir(c) / PHASE)); store = store or BarStore(root=own_data_dir(c) / "phase13_bars", fallback_root=None)
        super().__init__(c, log_dir=log_dir, store=store, accounts=["A_BTC_SMA200"], mode=mode, quote_provider=quote_provider)      # 父类先建一个占位账户目录（不写文件），随后替换
        for p in [self.dir / "A_BTC_SMA200"]:
            if p.exists() and not any(p.iterdir()):
                p.rmdir()
        cost = self.c["usdt"]["costs"]["round2"]; initial = float(self.c["usdt"]["initial_cash_usdt"]); specs = shadow_specs(self.c); names = accounts or list(specs)
        self.accounts = {n: ShadowAccount(n, specs[n] | {"mode": mode}, self.c, self.dir / n, initial, float(cost["fee_bps"]), float(cost["slippage_bps"])) for n in names}
        for a in self.accounts.values():
            a.quote_provider = self.quote_provider

    def run_once(self, now=None, bars=None, quality_fault: bool = False, quality_note: str = "") -> dict:
        bars = bars or self.load_bars()
        for a in self.accounts.values():
            a.bars_ref = bars
        return super().run_once(now=now, bars=bars, quality_fault=quality_fault, quality_note=quality_note)


def shadow_dirs(c: dict | None = None) -> dict:
    c = c or cfg(); return {"log_dir": report_dir(c) / PHASE, "bars_root": own_data_dir(c) / "phase13_bars", "feed_dir": own_data_dir(c) / "phase13_feed", "production_dir": report_dir(c) / "phase8_forward"}


def build_service(c: dict | None = None, log_dir: Path | None = None, bars_root: Path | None = None, feed_dir: Path | None = None, sources=None, quote_provider=None, clock=None):
    """独立服务：独立 log_dir（锁 / 心跳 / 日志 / 账户）、独立行情仓（首次从生产前瞻仓只读复制预热）、独立 feed 状态目录。"""
    from ..forward.service import ForwardService
    c = c or cfg(); d = shadow_dirs(c); log_dir = Path(log_dir or d["log_dir"]); bars_root = Path(bars_root or d["bars_root"]); feed_dir = Path(feed_dir or d["feed_dir"])
    store = BarStore(root=bars_root, fallback_root=None); seed = BarStore(root=own_data_dir(c) / "forward_bars", fallback_root=None)
    runner = ShadowRunner(c, log_dir=log_dir, store=store, quote_provider=quote_provider)
    feed = MarketDataService(c, store=store, log_dir=feed_dir, sources=sources, seed_from=seed)
    return ForwardService(c, log_dir=log_dir, store=store, feed=feed, runner=runner, clock=clock)
