"""第二十六阶段：A = 第十一阶段现行规则（V4 主配置，代码不改）；B = 只去掉回调确认里的“收盘 > 支撑区上沿”，其余全部沿用 A。
数据：OKX 现货日线（1Dutc，已收盘）。只读，无任何交易接口。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..pa11.engine11 import SYMS, Config, MarketContext, PriceActionBacktester
from ..pa11.signals import PullbackConfirmation, VolumeAndOrderFlowConfirmation
from ..pa11.structure import DailyStructureDetector, structure_state
from ..pa11.zones import SupportResistanceZones

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase26_pullback_ab"
DATA_DIR = PROJECT_ROOT / "data_store" / "brando_pa" / "phase26_pullback_ab"
DATA_START, DATA_END = "2018-01-11", "2026-11-23"
WINDOWS = {"样本内": ("2018-07-30", "2023-12-31"), "样本外": ("2024-01-01", DATA_END)}
FEE_BPS, SLIP_BPS = 20.0, 5.0                      # 第十一阶段 MAIN 成本 25 bps 单边
INITIAL = 100_000.0
FAIL_DAYS = 10
MAIN_CFG = dict(strategies=("A", "B"), exit_mode="B", risk_pct=0.01, max_alloc=0.70, dd_limit=0.30, macro_mode="none")


class PullbackConfirmationRelaxed(PullbackConfirmation):
    """B：与 A 逐行相同，只是确认日不要求“收盘 > 支撑区上沿”。"""

    def check(self, d: DailyStructureDetector, zones_obj: SupportResistanceZones, i: int, zones: list) -> dict:
        st = structure_state(d.sw, i); close = d.close[i]; a = d.atr[i]
        if st["last_high_idx"] is None or st["prev_low"] is None or st["last_low_idx"] is None or not np.isfinite(a):
            return {"signal": False, "reason": "结构点不足"}
        hi_idx = st["last_high_idx"]; H = st["last_high"]
        lows_before = d.sw[(d.sw.kind == "L") & (d.sw.idx < hi_idx) & (d.sw.confirmed_at <= i)]
        if lows_before.empty:
            return {"signal": False, "reason": "高点前无已确认低点"}
        L0 = float(lows_before.price.iloc[-1])
        if H <= L0 or hi_idx >= i - 1:
            return {"signal": False, "reason": "无有效上行波段或回调未开始"}
        seg_low = float(d.d["low"].iloc[hi_idx + 1:i + 1].min()); depth = (H - seg_low) / (H - L0)
        if depth > self.max_depth:
            return {"signal": False, "reason": f"回调深度 {depth:.0%} > 61.8%，结构破坏"}
        if depth <= 0.05:
            return {"signal": False, "reason": "尚未回调"}
        z = SupportResistanceZones.zone_containing(zones, seg_low)
        if z is None or z.low >= H:
            return {"signal": False, "reason": "回调低点未触及任何已确认支撑区"}
        if not VolumeAndOrderFlowConfirmation.selling_pressure_weakening(d, hi_idx + 1, i - 1):
            return {"signal": False, "reason": "回调期间未见卖压减弱（缩量下跌日）"}
        b = VolumeAndOrderFlowConfirmation.bar(d, i); prev_high = float(d.d["high"].iloc[i - 1])
        vol_ok = bool(np.isfinite(b["vol_rel20"]) and b["vol_rel20"] >= self.vol_rel_min and d.vol[i] > 0)
        conf = close > prev_high and b["close_pos"] >= 0.6 and vol_ok                       # ← 唯一差异：去掉 close > z.high
        if not conf:
            return {"signal": False, "reason": f"等待确认：收盘>昨高={close > prev_high}，收盘位置={b['close_pos']:.2f}，量比={b['vol_rel20']:.2f}"}
        inval = max(z.low - 0.5 * a, st["last_low"] - 0.25 * a)
        return {"signal": True, "zone": z, "invalidation": inval, "depth": depth, "reason": f"B 回调买入（放宽）：回调 {depth:.0%} 至支撑区 [{z.low:.0f},{z.high:.0f}]，首次收盘 {close:.0f} > 昨高 {prev_high:.0f}，收盘位置 {b['close_pos']:.2f}，量比 {b['vol_rel20']:.2f}"}


class SymbolBacktester(PriceActionBacktester):
    """只允许在指定标的上开新仓（单币种回测）；组合回测时允许两个标的。"""

    def __init__(self, *a, allowed=tuple(SYMS), **k):
        super().__init__(*a, **k); self.allowed = set(allowed)

    def _decide_entry(self, s, i, eq, cl):
        if s in self.allowed:
            super()._decide_entry(s, i, eq, cl)


# ---------------- 数据 ----------------
def fetch(data_dir: Path = DATA_DIR) -> dict:
    from ..holding.data import okx_daily
    data_dir.mkdir(parents=True, exist_ok=True); meta = {}
    for s in SYMS:
        d = okx_daily(s, pd.Timestamp(DATA_START, tz="UTC"), pd.Timestamp(DATA_END, tz="UTC")); p = data_dir / f"okx_{s}_1d.csv"
        d[["ts", "open", "high", "low", "close", "volume"]].to_csv(p, index=False); meta[s] = {"rows": len(d), "first": str(d.ts.min()), "last": str(d.ts.max()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    (data_dir / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8"); return meta


def load(data_dir: Path = DATA_DIR) -> dict:
    out = {}
    for s in SYMS:
        d = pd.read_csv(data_dir / f"okx_{s}_1d.csv"); d["ts"] = pd.to_datetime(d["ts"], utc=True); d = d.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
        full = pd.date_range(d.ts.min(), d.ts.max(), freq="D")
        assert len(full) == len(d), f"{s} 日线有缺天"
        out[s] = d
    return out


def cross_check(bars: dict) -> dict:
    """与旧缓存（Binance 镜像）收盘价比对，只用于报告数据可信度。"""
    try:
        from ..holding.data import load_dataset
        old = load_dataset()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    res = {}
    for s in SYMS:
        m = bars[s][["ts", "close"]].merge(old[s][["ts", "close"]], on="ts", suffixes=("_okx", "_old"))
        rel = (m.close_okx / m.close_old - 1).abs(); res[s] = {"overlap_days": len(m), "median_abs_diff": float(rel.median()), "p99_abs_diff": float(rel.quantile(0.99)), "max_abs_diff": float(rel.max()), "max_day": str(m.ts.iloc[int(rel.values.argmax())].date())}
    return res


# ---------------- 上下文 ----------------
def contexts(bars: dict) -> dict:
    ca = MarketContext(bars); cb = copy.copy(ca); cb.det_res = {s: dict(ca.det_res[s]) for s in SYMS}; det = PullbackConfirmationRelaxed()
    for s in SYMS:
        cb.det_res[s]["A"] = [det.check(ca.d[s], ca.z[s], i, ca.zones[s][i]) if i >= 30 else {"signal": False, "reason": "数据不足"} for i in range(ca.n)]
    return {"A": ca, "B": cb}


def episodes(ctx: MarketContext, s: str) -> pd.DataFrame:
    """前置条件满足且回调检测触发的日子；同一摆动高点只取第一次。"""
    rows = []
    seen = set()
    for i in range(ctx.n):
        r = ctx.det_res[s]["A"][i]
        if not r["signal"] or not (ctx.wt[s][i]["trend"] == "UP" and ctx.ds[s][i]["sma_filter_ok"]):
            continue
        hi = ctx.st[s][i]["last_high_idx"]
        if hi in seen:
            continue
        seen.add(hi); cl = ctx.close[s]; inv = float(r["invalidation"]); fut = cl[i + 1:i + 1 + FAIL_DAYS]
        rows.append({"symbol": s, "episode_high_idx": int(hi), "episode_high_date": str(ctx.idx[hi].date()), "signal_idx": i, "signal_date": str(ctx.idx[i].date()), "close": float(cl[i]), "invalidation": inv,
                     "zone_low": float(r["zone"].low), "zone_high": float(r["zone"].high), "depth": float(r["depth"]), "fail_10d": bool((fut < inv).any()) if len(fut) == FAIL_DAYS else None, "reason": r["reason"]})
    return pd.DataFrame(rows)


def compare_episodes(ea: pd.DataFrame, eb: pd.DataFrame, start: str, end: str) -> dict:
    sel = lambda e: e[(pd.to_datetime(e.signal_date) >= pd.Timestamp(start)) & (pd.to_datetime(e.signal_date) <= pd.Timestamp(end))] if len(e) else e
    a, b = sel(ea), sel(eb); key = ["symbol", "episode_high_idx"]
    m = b.merge(a[key + ["signal_idx"]], on=key, how="left", suffixes=("_b", "_a")) if len(b) else pd.DataFrame()
    common = m[m.signal_idx_a.notna()] if len(m) else m; b_only = m[m.signal_idx_a.isna()] if len(m) else m
    a_only = a.merge(b[key], on=key, how="left", indicator=True) if len(a) else a
    a_only = a_only[a_only["_merge"] == "left_only"] if len(a) else a_only
    lead = (common.signal_idx_a - common.signal_idx_b) if len(common) else pd.Series(dtype=float)
    fr = lambda e: (float(e.fail_10d.dropna().mean()) if len(e) and e.fail_10d.notna().any() else None, int(e.fail_10d.notna().sum()) if len(e) else 0)
    return {"n_A": len(a), "n_B": len(b), "n_common": len(common), "n_B_only": len(b_only), "n_A_only": len(a_only),
            "lead_days": {"mean": float(lead.mean()) if len(lead) else None, "median": float(lead.median()) if len(lead) else None, "max": float(lead.max()) if len(lead) else None, "n_earlier": int((lead > 0).sum()), "n_same_day": int((lead == 0).sum()), "values": [int(x) for x in lead]},
            "fail10_A": fr(a), "fail10_B": fr(b), "fail10_B_only": fr(b_only) if len(b_only) else (None, 0), "fail10_common_B": fr(common) if len(common) else (None, 0)}


# ---------------- 回测 ----------------
def max_dd(series: pd.Series) -> float:
    s = series.astype(float); return float((s / s.cummax() - 1).min())


def backtest(bars: dict, ctx: MarketContext, variant: str, allowed: tuple, start: str, end: str) -> dict:
    c = Config(**MAIN_CFG, name=f"{variant}_{'+'.join(allowed)}")
    r = SymbolBacktester(bars, c, FEE_BPS, SLIP_BPS, start, end, initial=INITIAL, ctx=ctx, allowed=allowed).run()
    nav = r.nav; t = r.trades
    closed = t[~t["partial"]] if len(t) else t
    return {"res": r, "row": {"variant": variant, "scope": "+".join(x.replace("USDT", "") for x in allowed), "start": start, "end": end, "final_liquidation_usdt": float(r.final["liquidation_usdt"]), "net_usdt": float(r.final["liquidation_usdt"] - INITIAL),
                               "max_drawdown": max_dd(nav["liquidation"]), "n_buys": int((r.fills["side"] == "BUY").sum()) if len(r.fills) else 0, "n_round_trips": len(closed), "win_rate": float((closed.net_pnl > 0).mean()) if len(closed) else None,
                               "fees_usdt": float(r.final["fees_usdt"]), "open_positions_at_end": int(r.final["open_positions"]), "avg_exposure": float(nav["exposure"].mean() / nav["equity"].mean()) if len(nav) else None, "identity_ok": bool(r.final["identity_ok"])}}


def judge(rows: list[dict], cmp: dict) -> dict:
    g = {(x["variant"], x["scope"], x["window"]): x for x in rows}
    oa, ob = g[("A", "BTC+ETH", "样本外")], g[("B", "BTC+ETH", "样本外")]; ia, ib = g[("A", "BTC+ETH", "样本内")], g[("B", "BTC+ETH", "样本内")]
    c1 = ob["net_usdt"] > oa["net_usdt"]; c2 = ob["max_drawdown"] >= oa["max_drawdown"] - 0.02; c3 = ib["net_usdt"] >= ia["net_usdt"]
    oos_ep = cmp["样本外"]["n_common"] + cmp["样本外"]["n_B_only"]
    few = oa["n_round_trips"] < 10 or ob["n_round_trips"] < 10 or oos_ep < 10
    dir_btc = np.sign(g[("B", "BTC", "样本外")]["net_usdt"] - g[("A", "BTC", "样本外")]["net_usdt"]); dir_eth = np.sign(g[("B", "ETH", "样本外")]["net_usdt"] - g[("A", "ETH", "样本外")]["net_usdt"])
    unstable = (not c3 and c1) or (dir_btc * dir_eth < 0)
    verdict = "B 优于 A（按事先规定）" if (c1 and c2 and c3 and not few and not unstable) else "保留 A，继续观察"
    return {"c1_oos_net_higher": bool(c1), "c2_oos_dd_not_worse_2pp": bool(c2), "c3_is_same_direction": bool(c3), "too_few_samples": bool(few), "oos_signal_episodes": int(oos_ep), "unstable": bool(unstable), "btc_eth_direction": [int(dir_btc), int(dir_eth)], "verdict": verdict}


def run(out_dir: Path | None = None, refetch: bool = False) -> dict:
    out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    if refetch or not (DATA_DIR / "okx_BTCUSDT_1d.csv").exists():
        fetch()
    bars = load(); ctxs = contexts(bars); R = {"data": {"source": "OKX 现货 history-candles 1Dutc（已收盘）", "range": [DATA_START, DATA_END], "manifest": json.loads((DATA_DIR / "manifest.json").read_text()), "cross_check_vs_old_cache": cross_check(bars)},
                                           "windows": WINDOWS, "cost_bps_one_side": FEE_BPS + SLIP_BPS, "initial_usdt": INITIAL, "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in MAIN_CFG.items()}}
    eps = {v: pd.concat([episodes(ctxs[v], s) for s in SYMS], ignore_index=True) for v in ("A", "B")}
    for v in eps:
        eps[v].assign(variant=v).to_csv(out / f"signals_{v}.csv", index=False)
    R["episodes"] = {w: compare_episodes(eps["A"], eps["B"], *rng) for w, rng in WINDOWS.items()}
    R["episodes_by_symbol"] = {f"{s}|{w}": compare_episodes(eps["A"][eps["A"].symbol == s], eps["B"][eps["B"].symbol == s], *rng) for s in SYMS for w, rng in WINDOWS.items()}
    rows = []; trades = []; fills = []
    for w, (a, b) in WINDOWS.items():
        for v in ("A", "B"):
            for allowed in (("BTCUSDT",), ("ETHUSDT",), tuple(SYMS)):
                x = backtest(bars, ctxs[v], v, allowed, a, b); x["row"]["window"] = w; rows.append(x["row"]); r = x["res"]
                tag = {"variant": v, "scope": x["row"]["scope"], "window": w}
                if len(r.trades):
                    trades += [t | tag for t in r.trades.to_dict("records")]
                fills += [f | tag for f in r.fills.to_dict("records")] if len(r.fills) else []
                if len(r.signals):
                    r.signals.assign(**tag).to_csv(out / f"engine_signals_{v}_{x['row']['scope']}_{w}.csv", index=False)
    R["results"] = rows; R["judgement"] = judge(rows, R["episodes"])
    R |= {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "历史研究（价格历史已反复研究过，非盲测）", "real_trading": "disabled (no implementation)"}
    (out / "phase26_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.DataFrame(trades).to_csv(out / "trades.csv", index=False); pd.DataFrame(fills).to_csv(out / "fills.csv", index=False); pd.DataFrame(rows).to_csv(out / "results.csv", index=False)
    return R


if __name__ == "__main__":
    print(run()["judgement"])
