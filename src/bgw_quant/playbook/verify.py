"""逐笔成交条件校验与结果分级（SIMULATED / REAL_HISTORICAL / EXECUTION_ESTIMATED）。

对模板中的每一笔交易检查：
  1) 时间顺序：入场分钟 < 出场分钟，两分钟均有 K 线；
  2) 成交量：该分钟全部交易的合计投入 ≤ 参与率 × 该分钟实际成交额（优先逐笔成交聚合；否则 K 线成交额）；
  3) 盘口深度：若有该分钟（±窗口）的深度快照，投入 ≤ 距中价 25 bp 内对应一侧的可成交金额（买用 ask、卖用 bid）；无快照 → 标记“深度未校验”；
  4) 成交价：买入价 ≥ 收盘价×(1+单边比例成本)，卖出价 ≤ 收盘价×(1−单边比例成本)，且成交参考价在该分钟 [low, high] 内；不使用最高/最低价；
  5) 手续费/滑点：单笔成本 ≥ 投入×2×(手续费+半价差+滑点)/1e4 + Gas（含冲击时更高）。
分级：任一资产 SIMULATED → SIMULATED；全部真实且逐笔成交与深度快照覆盖充分 → REAL_HISTORICAL；真实 K 线但缺逐笔/深度 → EXECUTION_ESTIMATED。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..rotation.universe import AssetDay

STATUS_SIM, STATUS_REAL, STATUS_EST = "SIMULATED", "REAL_HISTORICAL", "EXECUTION_ESTIMATED"


@dataclass
class TradeCheck:
    no: int
    symbol: str
    time_order_ok: bool
    volume_ok: bool
    volume_ratio_entry: float | None       # 该分钟合计投入 / 可成交量
    volume_ratio_exit: float | None
    depth_checked: bool
    depth_ok: bool | None
    price_in_range_ok: bool
    no_high_low_fill_ok: bool
    cost_ok: bool
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.time_order_ok and self.volume_ok and self.price_in_range_ok and self.no_high_low_fill_ok and self.cost_ok and (self.depth_ok is not False)


@dataclass
class VerifyResult:
    status: str
    n_trades: int
    n_ok: int
    n_depth_checked: int
    n_depth_failed: int
    n_volume_failed: int
    n_price_failed: int
    n_cost_failed: int
    liquidity_source: dict
    depth_coverage: dict                   # symbol → 有快照的分钟占比
    notes: list[str]
    checks: list[TradeCheck]

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "checks"}
        d["checks"] = [c.__dict__ | {"ok": c.ok} for c in self.checks]
        return d


def _minute_amount(a: AssetDay, trades_df: pd.DataFrame | None) -> tuple[np.ndarray, str]:
    """每根 K 线的实际成交额：优先逐笔成交聚合。"""
    if trades_df is not None and not trades_df.empty:
        minute = pd.to_datetime(trades_df["ts_ny"]).dt.floor("min")
        agg = trades_df.groupby(minute)["value"].sum()
        idx = pd.to_datetime(a.bars["ts_ny"]).dt.floor("min")
        return agg.reindex(idx).fillna(0.0).to_numpy(float), "transactions"
    amt = a.bars["amount"].to_numpy(float) if "amount" in a.bars else np.full(a.n, np.nan)
    if np.isfinite(amt).any() and np.nansum(amt) > 0:
        return np.where(np.isfinite(amt), amt, 0.0), "kline_amount"
    return np.full(a.n, np.inf), "unknown"


def verify_trades(result_trades: list[dict], assets: dict[str, AssetDay], participation: float, trades_by_symbol: dict[str, pd.DataFrame] | None = None,
                  depth_by_symbol: dict[str, pd.DataFrame] | None = None, depth_window_min: int = 5, depth_bp: int = 25) -> VerifyResult:
    trades_by_symbol = trades_by_symbol or {}
    depth_by_symbol = depth_by_symbol or {}
    amounts, liq_src, used = {}, {}, {}
    for s, a in assets.items():
        amounts[s], liq_src[s] = _minute_amount(a, trades_by_symbol.get(s))
        used[s] = np.zeros(a.n)
    # 同一分钟多笔共享：先累计
    for t in result_trades:
        a = assets.get(t["symbol"])
        if a is None:
            continue
        bi, si = int(a.bar_at_grid[t["entry_grid"]]), int(a.bar_at_grid[t["exit_grid"]])
        if bi >= 0:
            used[t["symbol"]][bi] += t["size_usd"]
        if si >= 0:
            used[t["symbol"]][si] += t["qty"] * t["exit_ref"] if "exit_ref" in t else t["size_usd"]
    checks: list[TradeCheck] = []
    depth_cov = {}
    for s, a in assets.items():
        dp = depth_by_symbol.get(s)
        if dp is None or dp.empty:
            depth_cov[s] = 0.0
        else:
            mins = set(pd.to_datetime(dp["ts_ny"]).dt.floor("min"))
            bar_min = pd.to_datetime(a.bars["ts_ny"]).dt.floor("min")
            cov = np.mean([any(abs((m - x).total_seconds()) <= depth_window_min * 60 for x in mins) for m in bar_min]) if len(mins) else 0.0
            depth_cov[s] = float(cov)
    n_vol_f = n_px_f = n_cost_f = n_dchk = n_dfail = 0
    for t in result_trades:
        s = t["symbol"]
        a = assets.get(s)
        notes = []
        if a is None:
            checks.append(TradeCheck(t["no"], s, False, False, None, None, False, None, False, False, False, ["资产无数据"]))
            continue
        bi, si = int(a.bar_at_grid[t["entry_grid"]]), int(a.bar_at_grid[t["exit_grid"]])
        time_ok = t["entry_grid"] < t["exit_grid"] and bi >= 0 and si >= 0
        close, low, high = a.bars["close"].to_numpy(float), a.bars["low"].to_numpy(float), a.bars["high"].to_numpy(float)
        cap_e = amounts[s][bi] * participation if bi >= 0 else 0.0
        cap_x = amounts[s][si] * participation if si >= 0 else 0.0
        vr_e = (used[s][bi] / cap_e) if (bi >= 0 and np.isfinite(cap_e) and cap_e > 0) else None
        vr_x = (used[s][si] / cap_x) if (si >= 0 and np.isfinite(cap_x) and cap_x > 0) else None
        vol_ok = time_ok and (vr_e is None or vr_e <= 1 + 1e-6) and (vr_x is None or vr_x <= 1 + 1e-6)
        if vr_e is None or vr_x is None:
            notes.append("该分钟成交额未知（流动性未校验）")
        if not vol_ok:
            n_vol_f += 1
        side_c = a.cost.proportional_side_cost()
        long = t["direction"] == "long"
        if time_ok:
            ref_e, ref_x = close[bi], close[si]
            in_range = (low[bi] - 1e-9 <= ref_e <= high[bi] + 1e-9) and (low[si] - 1e-9 <= ref_x <= high[si] + 1e-9)
            if long:
                nohl = t["entry_price"] >= ref_e * (1 + side_c) - 1e-9 and t["exit_price"] <= ref_x * (1 - side_c) + 1e-9
            else:
                nohl = t["entry_price"] <= ref_e * (1 - side_c) + 1e-9 and t["exit_price"] >= ref_x * (1 + side_c) - 1e-9
        else:
            in_range = nohl = False
        if not (in_range and nohl):
            n_px_f += 1
        min_cost = t["size_usd"] * 2 * side_c + t.get("gas_usd", 0.0) - 1e-6
        cost_ok = t["cost_usd"] >= min_cost
        if not cost_ok:
            n_cost_f += 1
        # 深度
        dp = depth_by_symbol.get(s)
        d_checked, d_ok = False, None
        if dp is not None and not dp.empty and time_ok:
            ts_e = pd.Timestamp(a.bars["ts_ny"].iloc[bi])
            near = dp[(pd.to_datetime(dp["ts_ny"]) - ts_e).abs() <= pd.Timedelta(minutes=depth_window_min)]
            if not near.empty:
                col = f"ask_usd_{depth_bp}bp" if long else f"bid_usd_{depth_bp}bp"
                avail = float(pd.to_numeric(near[col], errors="coerce").median())
                d_checked, d_ok = True, bool(t["size_usd"] <= avail + 1e-6)
                n_dchk += 1
                if not d_ok:
                    n_dfail += 1
                    notes.append(f"投入 {t['size_usd']:,.0f} > 距中价 {depth_bp}bp 内深度 {avail:,.0f}")
        if not d_checked:
            notes.append("无该分钟盘口快照：深度未校验（成交估算）")
        checks.append(TradeCheck(t["no"], s, time_ok, vol_ok, vr_e, vr_x, d_checked, d_ok, in_range, nohl, cost_ok, notes))
    sim = any(a.simulated for a in assets.values())
    all_real = not sim and all(not a.simulated for a in assets.values())
    tx_ok = all(liq_src[s] == "transactions" for s in assets)
    depth_ok = all(depth_cov.get(s, 0.0) >= 0.9 for s in assets) and len(assets) > 0
    status = STATUS_SIM if sim else (STATUS_REAL if (all_real and tx_ok and depth_ok) else STATUS_EST)
    notes = []
    if status == STATUS_EST:
        miss = [s for s in assets if liq_src[s] != "transactions"]
        if miss:
            notes.append("缺逐笔成交（用 K 线成交额估算流动性）：" + ", ".join(miss))
        lowc = [f"{s}({depth_cov.get(s, 0):.0%})" for s in assets if depth_cov.get(s, 0.0) < 0.9]
        if lowc:
            notes.append("盘口深度快照覆盖不足（成交估算，深度未校验）：" + ", ".join(lowc))
    if status == STATUS_SIM:
        notes.append("SIMULATED 模拟行情：所有校验只验证模型内部一致性，不代表真实可成交")
    return VerifyResult(status, len(checks), sum(c.ok for c in checks), n_dchk, n_dfail, n_vol_f, n_px_f, n_cost_f, liq_src, depth_cov, notes, checks)
