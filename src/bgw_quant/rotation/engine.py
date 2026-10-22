"""跨市场动态资产轮动——因果决策引擎（C 模型）。

每一分钟（统一网格）只用 ≤ 当前时刻的数据：
  1) 结算上一分钟决定的出场/入场（延迟 latency_bars、分批成交、成交率、API 限频、共享流动性）；
  2) 熔断：异常跳变 / 数据缺口 / 日内回撤 / 当日亏损上限；
  3) 对资产池内“当前有 K 线”的每个资产打分（预期净收益、相对胜率、波动率风险、流动性、成本、切换成本），
     选择得分最高且通过成本门槛的资产；槛位满时只有新资产风险调整后得分 > 持有资产继续持有得分 + 切换成本 才轮动；
  4) 全部资产不满足条件 → 空仓（允许）；
  5) 当日累计净收益达到目标后进入“达标后再评估”模式：只接受更高门槛、减半仓位的机会，并设置回吐保护线，
     不假设利润已锁定。
方向只在各资产 ProductCapability 允许的集合内选择；没有登记可核实的做空渠道就绝不做空。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import load_yaml
from ..decision.engine import EngineConfig
from ..target_model.execution import BatchFill, ChildFill, bar_liquidity, batch_fill
from .strategies import StrategySpec
from .universe import GRID_MINUTES, AssetDay

DISCLAIMER_ROTATION = ("因果轮动引擎：每分钟只用已产生的数据决策，下一分钟起分批成交；"
                       "结果为无未来信息的历史模拟，不是保证收益，也不代表未来可复制。")


@dataclass
class RotParams:
    z_entry: float = 1.5
    tp_atr_mult: float = 1.5
    max_open: int = 4
    trend_thresh: float = 0.5
    edge_min_ratio: float = 1.2
    min_risk_ratio: float = 0.0        # 预期净收益 / 风险（size×每根波动×√持有期）的最低比值；0 = 不额外过滤
    switch_cost_mult: float = 1.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def load_rotation_config() -> dict:
    try:
        return load_yaml("rotation.yaml")
    except FileNotFoundError:
        return {}


@dataclass
class RotPosition:
    asset: AssetDay
    direction: str
    mode: str
    fill: BatchFill
    entry_grid: int
    entry_bar: int
    peak: float
    tp_net_usd: float
    post_target: bool = False


@dataclass
class RotTrade:
    seq: int
    symbol: str
    asset_class: str
    direction: str
    mode: str
    open_ts: str
    close_ts: str
    size_usd: float
    qty: float
    avg_open: float
    avg_close: float
    trading_cost_usd: float
    funding_cost_usd: float
    net_pnl_usd: float
    net_ret: float
    hold_bars: int
    reason_exit: str
    cum_net_pnl_usd: float = 0.0
    post_target: bool = False
    partial: bool = False
    open_children: list[dict] = field(default_factory=list)
    close_children: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Switch:
    ts: str
    from_symbol: str
    to_symbol: str
    reason: str
    est_cost_usd: float


@dataclass
class RotDayRun:
    strategy_key: str
    family: str
    day: str
    capital_usd: float
    target_usd: float
    params: RotParams
    universe: list[str]
    trades: list[RotTrade]
    switches: list[Switch]
    realized_net_usd: float
    realized_net_return: float
    target_met: bool
    target_hit_ts: str | None
    n_trades: int
    n_long: int
    n_short: int
    n_switches: int
    path: list[str]
    per_asset: dict
    win_rate: float | None
    max_drawdown_usd: float
    max_consecutive_losses: int
    peak_deployed_usd: float
    avg_deployed_usd: float
    flat_ratio: float
    execution_cost_usd: float
    funding_cost_usd: float
    post_target_trades: int
    post_target_net_usd: float
    post_target_stop: bool
    pause_reasons: dict
    stop_reason: str | None
    partial_fills: int
    unverifiable: bool
    data_is_simulated: bool
    sources: dict
    curve: list[float]
    disclaimer: str = DISCLAIMER_ROTATION

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trades", "params", "switches", "curve")}
        d["params"] = self.params.to_dict()
        d["trades"] = [t.to_dict() for t in self.trades]
        d["switches"] = [s.__dict__ for s in self.switches]
        return d


# ----------------------------------------------------------------------------- 特征（因果：只用 ≤ i 的 K 线）
def _features(bars: pd.DataFrame, fc: dict, bars_per_day: int) -> dict[str, np.ndarray]:
    c = bars["close"].astype(float)
    ema_f = c.ewm(span=int(fc.get("ema_fast", 10)), adjust=False).mean()
    ema_s = c.ewm(span=int(fc.get("ema_slow", 30)), adjust=False).mean()
    prev = c.shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(), (bars["low"] - prev).abs()], axis=1).max(axis=1)
    aw = int(fc.get("atr_window", 14))
    atr = tr.ewm(alpha=1 / aw, adjust=False, min_periods=aw).mean()
    zw = int(fc.get("z_window", 30))
    m, sd = c.rolling(zw, min_periods=zw).mean(), c.rolling(zw, min_periods=zw).std(ddof=0)
    z = (c - m) / sd.replace(0, np.nan)
    rv = np.log(c).diff().rolling(int(fc.get("rv_window", 30)), min_periods=10).std() * np.sqrt(bars_per_day)
    trend = (ema_f - ema_s) / atr.replace(0, np.nan)
    return {"atr": atr.to_numpy(), "z": z.to_numpy(), "rv": rv.to_numpy(), "trend": trend.to_numpy(), "close": c.to_numpy()}


def _gas(fill: BatchFill, cost) -> float:
    return sum(cost.p.gas_usd * max(1, int(np.ceil(f.notional_usd / cost.p.max_tx_usd))) for f in fill.fills)


def _to_grid(arr: np.ndarray, asset: AssetDay, fill_value=np.nan) -> np.ndarray:
    out = np.full(GRID_MINUTES, fill_value, dtype=float)
    out[asset.grid_of_bar] = arr
    return out


# ----------------------------------------------------------------------------- 主引擎
def run_rotation_day(assets: list[AssetDay], spec: StrategySpec, capital_usd: float, target_usd: float, cfg: EngineConfig,
                     params: RotParams, day: str, rot_cfg: dict | None = None) -> RotDayRun:
    rot_cfg = rot_cfg or {}
    risk, ex = cfg.risk, cfg.execution
    sl = float(risk.get("stop_loss_pct", 0.5)) / 100
    max_hold = int(risk.get("max_hold_bars", 60))
    flat_before = int(risk.get("flat_before_close_bars", 5))
    vol_target = float(risk.get("vol_target_daily", 0.02))
    part = float(ex.get("participation_rate", 0.1))
    batch = int(ex.get("batch_bars", 5))
    look = int(ex.get("liquidity_lookback", 20))
    rate_limit = int(ex.get("api_rate_limit_orders_per_min", 10))
    cap_cost_annual = float(ex.get("capital_cost_annual", 0.05))
    latency = max(1, int(ex.get("latency_bars", 1)))
    fill_rate = float(ex.get("fill_rate", 1.0))
    max_dd_pct = float(risk.get("max_intraday_drawdown_pct", 1.0)) / 100
    max_loss_pct = float(risk.get("max_daily_loss_pct", 1.5)) / 100
    jump_pct = float(risk.get("anomaly_jump_pct", 3.0)) / 100
    anomaly_pause = int(risk.get("anomaly_pause_bars", 15))
    gap_pause = int(risk.get("data_gap_pause_bars", 5))
    low_liq_min = float(risk.get("low_liquidity_min_tranche_usd", 500))
    post_edge_mult = float(rot_cfg.get("post_target_edge_mult", 1.5))
    post_size_mult = float(rot_cfg.get("post_target_size_mult", 0.5))
    post_giveback = float(rot_cfg.get("post_target_giveback", 0.25))
    exp_hold = int(rot_cfg.get("expected_hold_bars", 20))
    pwin_prior_n = float(rot_cfg.get("pwin_prior_n", 4))
    assert float(risk.get("max_leverage", 1.0)) <= 1.0, "V1 不允许杠杆"

    A = len(assets)
    if A == 0:
        raise ValueError("资产池为空")
    # ---- 预计算：各资产特征映射到统一网格（A × 1440）
    F = {k: np.full((A, GRID_MINUTES), np.nan) for k in ("close", "atr", "z", "rv", "trend", "trail_liq", "low", "high")}
    present = np.zeros((A, GRID_MINUTES), dtype=bool)
    liq_arrs, avail_arrs, feats, liq_known = [], [], [], []
    for a_i, a in enumerate(assets):
        f = _features(a.bars, cfg.features, a.bars_per_day)
        feats.append(f)
        liq = bar_liquidity(a.bars, part)
        known = bool(np.isfinite(liq).all())
        liq_known.append(known)
        liq_arrs.append(liq)
        avail_arrs.append(liq.copy())
        if known:
            trail = pd.Series(liq).rolling(look + 1, min_periods=1).mean().to_numpy() * batch
        else:
            trail = np.full(a.n, np.inf)
        for k in ("close", "atr", "z", "rv", "trend"):
            F[k][a_i] = _to_grid(f[k], a)
        F["trail_liq"][a_i] = _to_grid(trail, a)
        F["low"][a_i] = _to_grid(a.bars["low"].to_numpy(float), a)
        F["high"][a_i] = _to_grid(a.bars["high"].to_numpy(float), a)
        present[a_i, a.grid_of_bar] = True
    side_cost = np.array([a.cost.proportional_side_cost() for a in assets])
    gas_usd = np.array([a.cost.p.gas_usd for a in assets])
    impact_k = np.array([a.cost.p.impact_coeff * a.cost.p.daily_vol for a in assets])
    depth = np.array([a.cost.p.depth_usd for a in assets])
    min_tx = np.array([a.cost.p.min_tx_usd for a in assets])
    tx_max = np.array([a.cap.tx_max_buy_usd or np.inf for a in assets])
    can_long = np.array(["long" in a.cap.directions for a in assets])
    can_short = np.array(["short" in a.cap.directions for a in assets])
    bpd = np.array([a.bars_per_day for a in assets], dtype=float)
    weights = np.array([a.weight for a in assets], dtype=float)
    from .universe import day_grid_utc
    grid_ny = day_grid_utc(pd.Timestamp(day).date()).tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M:%S").tolist()

    # ---- 状态
    positions: list[RotPosition] = []
    trades: list[RotTrade] = []
    switches: list[Switch] = []
    pending_entries: list[tuple[int, str, str, float, int, bool]] = []   # (asset_i, direction, mode, size, due_grid, post_target)
    pending_exits: list[tuple[RotPosition, str]] = []
    paused_until = np.full(A, -1)
    pwin_w = np.zeros(A); pwin_n = np.zeros(A)
    cum, curve, deployed = 0.0, np.zeros(GRID_MINUTES), np.zeros(GRID_MINUTES)
    pause_reasons: dict[str, int] = {}
    stop: str | None = None
    hit_ts: str | None = None
    post_target = False
    post_stop = False
    partial = 0
    peak_dep = 0.0
    path: list[str] = []
    per_asset = {a.symbol: {"net_usd": 0.0, "trades": 0, "minutes_in_position": 0, "asset_class": a.asset_class} for a in assets}
    last_open = -10**9

    def note(reason: str):
        pause_reasons[reason] = pause_reasons.get(reason, 0) + 1

    def slot_budget(n_held_assets: int) -> float:
        if spec.rotation:
            return capital_usd / max(1, spec.max_assets)
        return capital_usd  # 非轮动：按资产权重在下方乘 weight

    def close_position(pos: RotPosition, t: int, reason: str, max_bars: int) -> bool:
        nonlocal cum, partial
        a = pos.asset
        a_i = assets.index(a)
        i = a.bar_at_grid[t]
        if i < 0:
            return False
        close = feats[a_i]["close"]
        side = "sell" if pos.direction == "long" else "buy"
        req = pos.fill.qty * close[i] * (1.0 if pos.direction == "long" else (1 + 2 * side_cost[a_i] + 0.002))
        fill = batch_fill(a.bars, i, side, req, a.cost, avail_arrs[a_i], max_bars, "open", qty_to_sell=pos.fill.qty if pos.direction == "long" else None)
        if not fill.fills:
            return False
        if pos.direction == "short" and fill.qty > pos.fill.qty:
            fill = fill.scaled(pos.fill.qty / fill.qty)
        ratio = min(1.0, fill.qty / pos.fill.qty)
        sold = pos.fill.scaled(ratio)
        for ch in fill.fills:
            avail_arrs[a_i][ch.idx] = max(0.0, avail_arrs[a_i][ch.idx] - ch.notional_usd)
        hold = fill.end_idx - pos.fill.start_idx
        gas = _gas(sold, a.cost) + _gas(fill, a.cost)
        borrow = a.cap.short_borrow_rate_annual if pos.direction == "short" else 0.0
        funding = sold.filled_usd * (cap_cost_annual + borrow) * hold / (252 * a.bars_per_day)
        gross = (fill.filled_usd - sold.filled_usd) if pos.direction == "long" else (sold.filled_usd - fill.filled_usd)
        net = gross - gas - funding
        cum += net
        tr = RotTrade(len(trades) + 1, a.symbol, a.asset_class, pos.direction, pos.mode, sold.fills[0].ts_ny, fill.fills[-1].ts_ny,
                      sold.filled_usd, sold.qty, pos.fill.avg_exec, fill.avg_exec, sold.cost_usd + fill.cost_usd, funding, net,
                      net / sold.filled_usd if sold.filled_usd else 0.0, hold, reason + ("（部分平仓）" if ratio < 0.999 else ""), cum,
                      pos.post_target, ratio < 0.999, [c.__dict__ for c in sold.fills], [c.__dict__ for c in fill.fills])
        trades.append(tr)
        per_asset[a.symbol]["net_usd"] += net
        per_asset[a.symbol]["trades"] += 1
        pwin_w[a_i] += 1.0 if net > 0 else 0.0
        pwin_n[a_i] += 1.0
        if ratio < 0.999:
            partial += 1
            pos.fill = pos.fill.scaled(1 - ratio)
            return False
        return True

    for t in range(GRID_MINUTES):
        orders_this_bar = 0
        live = present[:, t]
        if not live.any():
            curve[t] = cum
            continue
        # 1) 出场执行
        for pos, reason in pending_exits:
            if pos in positions and pos.asset.bar_at_grid[t] >= 0:
                a = pos.asset
                i = a.bar_at_grid[t]
                mb = batch if not reason.startswith("日终") else max(1, a.n - i)
                if close_position(pos, t, reason, mb):
                    positions.remove(pos)
        pending_exits = [(p, r) for p, r in pending_exits if p in positions and p.asset.bar_at_grid[t] < 0]
        # 2) 入场执行
        still = []
        held_assets = {p.asset.symbol for p in positions}
        for a_i, direction, mode, size, due, is_post in pending_entries:
            a = assets[a_i]
            if due > t or a.bar_at_grid[t] < 0:
                if a.bar_at_grid[t] < 0 and t > a.last_grid:
                    continue
                still.append((a_i, direction, mode, size, due, is_post))
                continue
            if stop is not None:
                continue
            if len(positions) >= params.max_open * (1 if spec.rotation else A) or orders_this_bar >= rate_limit:
                note("API限频/并行上限")
                continue
            if spec.rotation and a.symbol not in held_assets and len(held_assets) >= spec.max_assets:
                note("资产槛位已满（切换未完成）")
                continue
            i = a.bar_at_grid[t]
            side = "buy" if direction == "long" else "sell"
            fill = batch_fill(a.bars, i, side, size * fill_rate, a.cost, avail_arrs[a_i], batch, "open")
            if not fill.fills or fill.filled_usd < max(a.cost.p.min_tx_usd, 0.005 * size):
                note("流动性不足未成交")
                continue
            for ch in fill.fills:
                avail_arrs[a_i][ch.idx] = max(0.0, avail_arrs[a_i][ch.idx] - ch.notional_usd)
            orders_this_bar += len(fill.fills)
            partial += int(fill.partial)
            atr_i = feats[a_i]["atr"][i] if np.isfinite(feats[a_i]["atr"][i]) else feats[a_i]["close"][i] * 0.002
            positions.append(RotPosition(a, direction, mode, fill, t, i, fill.avg_exec, params.tp_atr_mult * atr_i * np.sqrt(exp_hold) * fill.qty, is_post))
            held_assets.add(a.symbol)
            if not path or path[-1] != a.symbol:
                path.append(a.symbol)
            last_open = t
        pending_entries = still
        dep = sum(p.fill.filled_usd for p in positions)
        deployed[t] = dep
        peak_dep = max(peak_dep, dep)
        curve[t] = cum
        for p in positions:
            per_asset[p.asset.symbol]["minutes_in_position"] += 1
        # 2b) 达标 / 熔断
        if cum >= target_usd - 1e-9 and hit_ts is None:
            hit_ts = grid_ny[t]
            post_target = True
        if post_target and stop is None and cum < target_usd * (1 - post_giveback):
            stop = "达标后回吐保护：累计净利润跌破保护线，停止开新仓"
            post_stop = True
        if stop is None and cum <= -max_loss_pct * capital_usd:
            stop = "触及当日亏损上限，暂停交易"
        if stop is None and t > 0:
            pk = np.max(curve[: t + 1])
            if pk > 0 and (pk - cum) >= max_dd_pct * capital_usd:
                stop = "日内回撤熔断：累计净利润回撤超过上限，停止交易"
        for a_i in np.flatnonzero(live):
            a = assets[a_i]
            i = a.bar_at_grid[t]
            if i > 0:
                c = feats[a_i]["close"]
                jump = abs(c[i] / c[i - 1] - 1)
                gap_min = (a.grid_of_bar[i] - a.grid_of_bar[i - 1])
                if jump >= jump_pct:
                    paused_until[a_i] = max(paused_until[a_i], t + anomaly_pause); note("异常跳变熔断")
                elif gap_min > 1.5:
                    paused_until[a_i] = max(paused_until[a_i], t + gap_pause); note("数据缺口暂停")
        # 3a) 出场判断（各资产自身 K 线）
        cont_score: dict[str, float] = {}
        exit_cost: dict[str, float] = {}
        for pos in positions:
            a = pos.asset
            a_i = assets.index(a)
            i = a.bar_at_grid[t]
            if i < 0:
                continue
            px = feats[a_i]["close"][i]
            atr_i, tr_i, rv_i = feats[a_i]["atr"][i], feats[a_i]["trend"][i], feats[a_i]["rv"][i]
            imp = a.cost.impact_fraction(pos.fill.filled_usd)
            unreal = (pos.fill.qty * px * (1 - side_cost[a_i] - imp) - pos.fill.filled_usd) if pos.direction == "long" \
                else (pos.fill.filled_usd - pos.fill.qty * px * (1 + side_cost[a_i] + imp))
            adverse = (px / pos.fill.avg_ref - 1) if pos.direction == "long" else (1 - px / pos.fill.avg_ref)   # 相对参考价（不含成本）
            pos.peak = max(pos.peak, px) if pos.direction == "long" else min(pos.peak, px)
            trail = (pos.peak - px) / pos.peak if pos.direction == "long" else (px - pos.peak) / pos.peak
            held = i - pos.entry_bar
            if i >= a.n - 1 - batch:
                pending_exits.append((pos, f"日终平仓（{a.asset_class}；日度核算不隔夜）"))
            elif unreal >= pos.tp_net_usd:
                pending_exits.append((pos, "止盈（净额达到 ATR 倍数）"))
            elif adverse <= -sl:
                pending_exits.append((pos, "止损 0.5%"))
            elif pos.mode.startswith("trend") and np.isfinite(atr_i) and trail * px >= atr_i and unreal > 0:
                pending_exits.append((pos, "趋势跟踪止盈回撤"))
            elif held >= max_hold:
                pending_exits.append((pos, "超过最长持有"))
            elif pos.mode.startswith("trend") and np.isfinite(tr_i) and ((pos.direction == "long" and tr_i < -params.trend_thresh) or (pos.direction == "short" and tr_i > params.trend_thresh)):
                pending_exits.append((pos, "趋势反转"))
            else:
                # 继续持有的风险调整价值（供轮动比较）
                pw = (0.5 * pwin_prior_n + pwin_w[a_i]) / (pwin_prior_n + pwin_n[a_i])
                remaining = max(0.0, pos.tp_net_usd - unreal)
                cont_score[a.symbol] = cont_score.get(a.symbol, 0.0) + pw * remaining     # 继续持有的期望剩余收益（USD）
                exit_cost[a.symbol] = exit_cost.get(a.symbol, 0.0) + pos.fill.filled_usd * (side_cost[a_i] + imp) + a.cost.p.gas_usd
        exiting = {p.asset.symbol for p, _ in pending_exits}
        # 3b) 入场打分（向量化，跨资产）
        if stop is not None:
            continue
        n_pos = len(positions)
        max_total = params.max_open * (1 if spec.rotation else A)
        if n_pos >= max_total or (t - last_open) < 1:
            continue
        atr, z, rv, tr, close = F["atr"][:, t], F["z"][:, t], F["rv"][:, t], F["trend"][:, t], F["close"][:, t]
        trail_liq = F["trail_liq"][:, t]
        # 每资产最后一根（日终前不开仓）
        last_grid = np.array([a.last_grid for a in assets])
        ok = live & (t > paused_until) & (t <= last_grid - flat_before) & np.isfinite(atr) & np.isfinite(z)
        if not ok.any():
            continue
        held_sym = {p.asset.symbol for p in positions}
        held_mask = np.array([a.symbol in held_sym for a in assets])
        n_held_assets = int(held_mask.sum())
        per_asset_open = np.array([sum(p.asset is a for p in positions) for a in assets])
        # 定仓：槛位预算 / 并行数，受可用本金、流动性、单笔上限、波动率目标约束
        budget = (slot_budget(n_held_assets) * (weights if not spec.rotation else 1.0))
        dep_asset = np.array([sum(p.fill.filled_usd for p in positions if p.asset is a) for a in assets])
        size = np.minimum(budget / params.max_open, np.maximum(0.0, budget - dep_asset))
        size = np.minimum(size, capital_usd - dep)
        size = np.where(np.isfinite(trail_liq), np.minimum(size, trail_liq), size)
        size = np.minimum(size, tx_max * batch)
        with np.errstate(invalid="ignore", divide="ignore"):
            vol_scale = np.where(np.isfinite(rv) & (rv > vol_target), vol_target / rv, 1.0)
        size = size * vol_scale
        if post_target:
            size = size * post_size_mult
        with np.errstate(invalid="ignore", divide="ignore"):
            # 预期有利波动按持有期尺度估计：ATR × √持有根数（随机游走尺度），而不是单根 ATR
            exp_move = params.tp_atr_mult * atr / close * np.sqrt(exp_hold)
            impact = impact_k * np.sqrt(np.where(depth > 0, size / depth, 0.0))
            rt = 2 * (side_cost + impact) + 2 * gas_usd / np.maximum(size, 1.0)
            pwin = (0.5 * pwin_prior_n + pwin_w) / (pwin_prior_n + pwin_n)
            edge = exp_move / rt
            edge_adj = edge * (pwin / 0.5)                     # 相对当日胜率调整
            rv_bar = np.where(np.isfinite(rv), rv, 0.02) / np.sqrt(bpd)
            exp_net = size * (exp_move * (pwin / 0.5) - rt)   # 预期净收益（USD，含成本）
            risk_usd = size * rv_bar * np.sqrt(exp_hold)       # 持有期波动风险（USD）
            score = exp_net / np.maximum(risk_usd, 1e-9)       # 风险调整后得分（用于跨资产排序）
        edge_min = params.edge_min_ratio * (post_edge_mult if post_target else 1.0)
        # 方向/模式
        direction = np.full(A, "", dtype=object); mode = np.full(A, "", dtype=object)
        up = ok & np.isfinite(tr) & (tr > params.trend_thresh) & can_long
        dn = ok & np.isfinite(tr) & (tr < -params.trend_thresh) & can_short
        rl = ok & np.isfinite(tr) & (np.abs(tr) <= params.trend_thresh) & (z <= -params.z_entry) & can_long
        rs = ok & np.isfinite(tr) & (np.abs(tr) <= params.trend_thresh) & (z >= params.z_entry) & can_short
        direction[up], mode[up] = "long", "trend_long"
        direction[dn], mode[dn] = "short", "trend_short"
        direction[rl & (direction == "")], mode[rl & (direction == "")] = "long", "range_long"
        direction[rs & (direction == "")], mode[rs & (direction == "")] = "short", "range_short"
        has_sig = direction != ""
        if not has_sig.any():
            note("全部资产无信号（空仓）")
            continue
        low_liq = has_sig & np.isfinite(trail_liq) & (trail_liq < low_liq_min)
        too_small = has_sig & ~low_liq & (size < np.maximum(min_tx, 0.002 * capital_usd / max(1, spec.max_assets)))
        weak = has_sig & ~low_liq & ~too_small & ~(edge_adj >= edge_min)
        for m, r in ((low_liq, "低流动性行情暂停"), (too_small, "流动性/容量不足"), (weak, "成本门槛高于预期波动")):
            if m.any():
                note(r)
        cand = has_sig & ~low_liq & ~too_small & ~weak & (per_asset_open < params.max_open) & (exp_net > 0) & (score >= params.min_risk_ratio)
        cand &= np.array([a.symbol not in exiting for a in assets])
        if not cand.any():
            note("无资产满足风险调整后收益条件（空仓）")
            continue
        order = [int(i) for i in np.argsort(-score) if cand[i]]
        slots_pos = max_total - n_pos
        queued = 0
        for a_i in order:
            if queued >= slots_pos:
                break
            a = assets[a_i]
            if spec.rotation and a.symbol not in held_sym and n_held_assets >= spec.max_assets:
                # 轮动：与持有中得分最低的资产比较，需覆盖切换成本
                worst = min(cont_score.items(), key=lambda kv: kv[1], default=None)
                if worst is None:
                    note("资产槛位已满（持仓待结算）")
                    continue
                w_sym, w_score = worst
                sw_cost = exit_cost.get(w_sym, 0.0) * params.switch_cost_mult
                if exp_net[a_i] - sw_cost > w_score:
                    for p in positions:
                        if p.asset.symbol == w_sym and p.asset.bar_at_grid[t] >= 0:
                            pending_exits.append((p, f"轮动切换→{a.symbol}"))
                    switches.append(Switch(grid_ny[t], w_sym, a.symbol, f"新资产预期净收益 {exp_net[a_i]:,.0f} − 切换成本 {sw_cost:,.0f} > 继续持有期望 {w_score:,.0f}（风险比 {score[a_i]:.2f}）", sw_cost))
                    cont_score.pop(w_sym, None)
                    held_sym.discard(w_sym)
                    n_held_assets -= 1
                else:
                    note("切换收益不足以覆盖切换成本")
                    continue
            pending_entries.append((a_i, str(direction[a_i]), str(mode[a_i]), float(size[a_i]), t + latency, post_target))
            held_sym.add(a.symbol)
            if a.symbol not in held_mask:
                n_held_assets += 1
            queued += 1
    # 收盘仍未平仓：按最后收盘估值（标记不可验证）
    unliq = 0.0
    for pos in positions:
        a = pos.asset
        a_i = assets.index(a)
        px = feats[a_i]["close"][-1]
        est = px * (1 - side_cost[a_i]) if pos.direction == "long" else px * (1 + side_cost[a_i])
        net = (pos.fill.qty * est - pos.fill.filled_usd) if pos.direction == "long" else (pos.fill.filled_usd - pos.fill.qty * est)
        net -= a.cost.p.gas_usd
        cum += net
        unliq += pos.fill.qty * px
        trades.append(RotTrade(len(trades) + 1, a.symbol, a.asset_class, pos.direction, pos.mode, pos.fill.fills[0].ts_ny, str(a.bars["ts_ny"].iloc[-1]),
                               pos.fill.filled_usd, pos.fill.qty, pos.fill.avg_exec, est, pos.fill.cost_usd + a.cost.p.gas_usd, 0.0, net,
                               net / pos.fill.filled_usd if pos.fill.filled_usd else 0.0, a.n - 1 - pos.entry_bar,
                               "日终未能平仓：按收盘估值（不可验证）", cum, pos.post_target, True))
        per_asset[a.symbol]["net_usd"] += net
        per_asset[a.symbol]["trades"] += 1
    curve[-1] = cum
    met = cum >= target_usd - 1e-9
    wins = [tr for tr in trades if tr.net_pnl_usd > 0]
    dd = float((np.maximum.accumulate(curve) - curve).max())
    streak = best_streak = 0
    for tr in trades:
        streak = streak + 1 if tr.net_pnl_usd < 0 else 0
        best_streak = max(best_streak, streak)
    active = present.any(axis=0)
    flat_ratio = float(((deployed <= 0) & active).sum() / max(1, active.sum()))
    pt = [tr for tr in trades if tr.post_target]
    return RotDayRun(spec.key, spec.family, day, capital_usd, target_usd, params, [a.symbol for a in assets], trades, switches, cum,
                     cum / capital_usd if capital_usd else 0.0, met, hit_ts, len(trades), sum(tr.direction == "long" for tr in trades),
                     sum(tr.direction == "short" for tr in trades), len(switches), path, per_asset, (len(wins) / len(trades)) if trades else None,
                     dd, best_streak, peak_dep, float(deployed[active].mean()) if active.any() else 0.0, flat_ratio,
                     float(sum(tr.trading_cost_usd for tr in trades)), float(sum(tr.funding_cost_usd for tr in trades)), len(pt),
                     float(sum(tr.net_pnl_usd for tr in pt)), post_stop, pause_reasons, stop, partial,
                     (not all(liq_known)) or unliq > 0 or any(a.cost.is_unverifiable(peak_dep) for a in assets),
                     any(a.simulated for a in assets), {a.symbol: a.source for a in assets}, curve.tolist())
