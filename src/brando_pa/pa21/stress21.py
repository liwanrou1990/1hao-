"""第二十一阶段压力测试（情景，不是预测）：BTC 暴跌、USDT 脱锚、年内暴涨后暴跌、连续多年下跌、历史最坏路径放大、
成本 / 冲击 / 流动性不足、再平衡延迟（行情延迟）及组合情景。找出固定观察基准 FIX25_Y 可能超过 50% 回撤预算的情景。
只做计算，不改动任何账本；冻结规则代码不修改（延迟情景用本模块内的子类，冲击上限情景临时替换模块常量后恢复）。"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..pa20 import execution20 as E
from ..pa20 import phase20 as P20
from ..pa20.alloc20 import SYM, AllocBacktester
from . import BUDGET, OUT_DIR, RESEARCH_INITIAL_USDT

W = 0.25


def _mdd(res) -> dict:
    nav = res.nav; liq = nav["liquidation"].astype(float).to_numpy(); peak = np.maximum.accumulate(np.r_[RESEARCH_INITIAL_USDT, liq])[1:]; dd = liq / peak - 1; k = int(dd.argmin())
    return {"max_drawdown": float(dd.min()), "trough_date": str(pd.Timestamp(nav["ts"].iloc[k]).date()), "final_usdt": float(res.final["liquidation_usdt"]), "n_fills": int(res.final["n_fills"]),
            "max_btc_weight": float(nav["exposure"].max()), "exceeds_budget": bool(dd.min() < -BUDGET)}


def _bars(dates, close, vol=1e9):
    close = np.asarray(close, float); o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": dates, "open": o, "high": np.maximum(o, close), "low": np.minimum(o, close), "close": close, "volume": vol / close})


def _run(bars, start=None, end=None, fee=10.0, slip=5.0, liq=None, cls=AllocBacktester, **kw):
    start = start or str(pd.Timestamp(bars["ts"].iloc[0]).date()); liq = liq or {SYM: E.liquidity_table(bars)}
    return cls(bars, W, "Y", fee, slip, start, end, RESEARCH_INITIAL_USDT, liq, **kw).run()


# 1 即时冲击（美元计价；无机会交易）
def instant_shocks(max_w: float = 0.25, max_w_date: str | None = None) -> dict:
    """max_w：FIX25_Y 在 2014–2026 历史上两次再平衡之间 BTC 仓位漂移到的最高值（由 run_all 计算）。"""
    btc = (0.0, 0.30, 0.50, 0.70, 0.80, 0.90); usdt = (0.0, 0.05, 0.10, 0.30, 0.50, 1.00)
    expo = {"FIX25_Y（目标 25%）": (0.25, 0.0), f"FIX25_Y（历史最高漂移 {max_w:.0%}，{max_w_date}）": (max_w, 0.0), "B2 持仓中（BTC 50% + ETH 50%）": (0.5, 0.5), "B2 / SMA200 空仓中（100% USDT）": (0.0, 0.0), "SMA200 持仓中 / BTC 买入持有（100% BTC）": (1.0, 0.0)}
    grid = {}
    for name, (wb, we) in expo.items():
        wu = 1 - wb - we; grid[name] = {f"BTC−{int(d * 100)}%|USDT−{int(u * 100)}%": -(wb * d + we * d + wu * u) for d in btc for u in usdt}   # ETH 假设与 BTC 同跌幅
    usdt_alone = {n: (1 - wb - we) for n, (wb, we) in expo.items()}
    thr = {n: (None if u == 0 or BUDGET / u > 1 else BUDGET / u) for n, u in usdt_alone.items()}                         # None = 仅 USDT 脱锚不可能超过 50%                                           # 只有 USDT 脱锚时超过 50% 所需脱锚幅度
    thr_comb = {f"BTC−{int(d * 100)}%": max(0.0, (BUDGET - W * d) / (1 - W)) for d in btc if d > 0}                                # FIX25_Y：BTC 同时下跌时所需脱锚幅度
    return {"loss_grid": grid, "usdt_depeg_needed_alone": thr, "fix25_usdt_depeg_needed_with_btc_drop": thr_comb,
            "note": "以美元计价；USDT 脱锚时以 USDT 计价的净值没有意义。FIX25_Y 有 75% 在 USDT，B2 / SMA200 空仓时 100% 在 USDT，BTC 买入持有不持有 USDT。"}


# 2 路径情景（合成行情，配置引擎原样运行）
def rally_then_crash(ks=(1, 2, 3, 5, 8, 13), ds=(0.5, 0.7, 0.8, 0.9)) -> list[dict]:
    """1 月 1 日建仓后年内上涨 k 倍（至 12 月 1 日），12 月内下跌 d（在下一次 1 月再平衡之前），次年持平。"""
    dates = pd.date_range(pd.Timestamp("2031-01-01", tz="UTC"), pd.Timestamp("2032-03-31", tz="UTC"), freq="D"); rows = []
    up = int(np.searchsorted(dates, pd.Timestamp("2031-12-01", tz="UTC"))); dn = int(np.searchsorted(dates, pd.Timestamp("2031-12-31", tz="UTC")))
    for k in ks:
        for d in ds:
            c = np.empty(len(dates)); c[:up + 1] = 100 * np.exp(np.linspace(0, np.log(k), up + 1)); c[up:dn + 1] = c[up] * np.exp(np.linspace(0, np.log(1 - d), dn - up + 1)); c[dn:] = c[dn]
            rows.append({"rally_x": k, "crash": d, **_mdd(_run(_bars(dates, c)))})
    return rows


def consecutive_down_years(ds=(0.5, 0.7, 0.8, 0.9), ns=(1, 2, 3, 4, 5)) -> list[dict]:
    """每个日历年 BTC 平滑下跌 d，连续 n 年；每年 1 月再平衡会把仓位加回 25%。"""
    rows = []
    for d in ds:
        for n in ns:
            dates = pd.date_range(pd.Timestamp("2031-01-01", tz="UTC"), pd.Timestamp(f"{2031 + n}-01-31", tz="UTC"), freq="D"); yrs = (dates - dates[0]).days / 365.0
            c = 100 * np.exp(np.minimum(yrs, n) * np.log(1 - d)); rows.append({"yearly_drop": d, "years": n, **_mdd(_run(_bars(dates, c)))})
    return rows


def amplified_history(ctx, windows=(("2014_bear", "2014-01-01", "2015-12-31"), ("2017_rally_2018_bear", "2017-01-01", "2018-12-31"), ("2021_2022", "2021-01-01", "2022-12-31")), ms=(1.0, 1.25, 1.5)) -> list[dict]:
    """历史区间的日对数收益放大 m 倍（保留真实日期，因而保留真实的 1 月再平衡时点）。m = 1 即历史原样。"""
    b = ctx.btc.copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); rows = []
    for name, a, z in windows:
        x = b[(b["ts"] >= pd.Timestamp(a, tz="UTC")) & (b["ts"] <= pd.Timestamp(z, tz="UTC"))].reset_index(drop=True); lr = np.log(x["close"].astype(float)).diff().fillna(0.0).to_numpy()
        for m in ms:
            c = float(x["close"].iloc[0]) * np.exp(np.cumsum(lr * m)); rows.append({"window": name, "amplify": m, "btc_mdd": float((c / np.maximum.accumulate(c) - 1).min()), **_mdd(_run(_bars(x["ts"], c)))})
    return rows


# 3 执行情景（2014–2026 全样本真实行情）
class DelayedAlloc(AllocBacktester):
    """再平衡（含建仓）推迟 delay 个交易日执行：模拟行情延迟 / 服务离线。"""
    def __init__(self, *a, delay: int = 0, **k):
        super().__init__(*a, **k); self.delay = int(delay)

    def run(self):
        q = []
        for i in range(self.i0, self.i1):
            for item in [x for x in q if x[0] == i]:
                self._rebalance(i, item[1]); q.remove(item)
            if i == self.i0 and self.w > 0:
                q.append((i + 1 + self.delay, "建仓（延迟）"))
            cl = float(self.c[i]); eq = self.L.equity({SYM: cl}); wa = self.L.qty(SYM) * cl / eq if eq > 0 else 0.0
            if self.w > 0 and i > self.i0 and i + 1 < self.i1 and self.ts[i + 1].year != self.ts[i].year:
                q.append((i + 1 + self.delay, "年度再平衡（延迟）"))
            self.nav.append({"ts": self.ts[i], "equity": eq, "liquidation": self.L.cash + self.L.qty(SYM) * cl * (1 - self.L.base_slip / 1e4) * (1 - self.L.fee_bps / 1e4), "exposure": wa, "holdings": {SYM: self.L.qty(SYM)}})
        from ..holding.engine import HoldResult
        nav = pd.DataFrame(self.nav); fills = pd.DataFrame(self.fills); last = float(self.c[self.i1 - 1]); liq = E.final_liquidation_with_impact(self.L, {SYM: last}, self.ts[self.i1 - 1])
        return HoldResult(self.name, {}, self.L.fee_bps, self.L.base_slip, self.initial, nav, pd.DataFrame(), fills, {"liquidation_usdt": liq, "n_fills": int(len(fills))})


@contextlib.contextmanager
def _cap(bps):
    old = E.CAP_BPS; E.CAP_BPS = bps
    try:
        yield
    finally:
        E.CAP_BPS = old


def _scaled_liq(liq: dict, k: float) -> dict:
    return {s: t.assign(quote_vol=t["quote_vol"] / k) for s, t in liq.items()}


def execution_stress(ctx) -> list[dict]:
    a = "2014-01-01"; rows = []
    for one_side, fee in ((15, 10.0), (50, 45.0), (100, 95.0), (200, 195.0)):
        rows.append({"scenario": f"单边成本 {one_side} bps", **_mdd(_run(ctx.btc, a, fee=fee, liq=ctx.liq_btc))})
    for k, lab in ((10, "成交量降为 1/10（冲击 ×√10）"), (100, "成交量降为 1/100（冲击 ×10）"), (9, "冲击系数 ×3"), (1e4, "成交量降为 1/10000（冲击 ×100）")):
        rows.append({"scenario": lab, **_mdd(_run(ctx.btc, a, liq=_scaled_liq(ctx.liq_btc, k)))})
    with _cap(1e6):
        rows.append({"scenario": "成交量降为 1/10000 且取消 100 bps 冲击上限", **_mdd(_run(ctx.btc, a, liq=_scaled_liq(ctx.liq_btc, 1e4)))})
    for dl in (1, 3, 7, 14, 30):
        rows.append({"scenario": f"再平衡延迟 {dl} 天（行情延迟 / 离线）", **_mdd(_run(ctx.btc, a, liq=ctx.liq_btc, cls=DelayedAlloc, delay=dl))})
    return rows


def comparators_execution(ctx) -> list[dict]:
    """对照在最差执行情景下（单边 200 bps + 成交量 1/100）的全样本表现（B2 自 2017 起）。"""
    out = []
    for name, a in (("BH_BTC", "2014-01-01"), ("B1_BTC_SMA200", "2014-01-01"), ("B2_original", "2017-01-01"), ("ALLOC_25_Y", "2014-01-01")):
        for lab, cost, k in (("基准 15 bps", 15, 1), ("单边 200 bps + 成交量 1/100", 200, 100)):
            fee = {15: 10.0, 200: 195.0}[cost]; liq_b, liq_2 = _scaled_liq(ctx.liq_btc, k), _scaled_liq(ctx.liq_b2, k)
            if name == "ALLOC_25_Y":
                res = _run(ctx.btc, a, fee=fee, liq=liq_b); fin = res.final["liquidation_usdt"]; mdd = _mdd(res)["max_drawdown"]
            else:
                if name == "B2_original":
                    from ..pa18 import phase18
                    from ..pa18.variants18 import RecoveryBacktester
                    bb = {s: v for s, v in ctx.b2bars.items() if not s.startswith("_")}
                    bt = E.attach(RecoveryBacktester(bb, {"name": "B2", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, 5.0, a, None, variant="V0_original", initial=RESEARCH_INITIAL_USDT, risk_params=ctx.c.get("trendsys", {}).get("risk", {})), liq_2)
                else:
                    spec = {"name": name, "assets": ["BTCUSDT"], "mode": "buyhold"} if name == "BH_BTC" else {"name": name, "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}
                    bt = E.attach(P20.HoldingBacktester({"BTCUSDT": ctx.btc}, spec, fee, 5.0, a, None, initial=RESEARCH_INITIAL_USDT, sma=200), liq_b)
                res = bt.run(); px = {s: float(res.nav["closes"].iloc[-1][s]) for s in res.nav["closes"].iloc[-1]}; fin = E.final_liquidation_with_impact(bt.L, px, res.nav["ts"].iloc[-1])
                liq = res.nav["liquidation"].astype(float); mdd = float((liq / np.maximum.accumulate(np.r_[RESEARCH_INITIAL_USDT, liq.to_numpy()])[1:] - 1).min())
            out.append({"name": name, "start": a, "scenario": lab, "final_usdt": float(fin), "max_drawdown": mdd, "n_fills": int(len(res.fills))})
    return out


# 4 组合情景
def combined(ctx) -> list[dict]:
    """最坏历史区间（原样 / 放大 1.25）+ 单边 200 bps + 成交量 1/100，并在区间低点叠加 USDT 脱锚 u（美元计价）。"""
    b = ctx.btc.copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); rows = []
    for name, a, z in (("2017_rally_2018_bear", "2017-01-01", "2018-12-31"), ("2021_2022", "2021-01-01", "2022-12-31")):
        x = b[(b["ts"] >= pd.Timestamp(a, tz="UTC")) & (b["ts"] <= pd.Timestamp(z, tz="UTC"))].reset_index(drop=True); lr = np.log(x["close"].astype(float)).diff().fillna(0.0).to_numpy()
        for m in (1.0, 1.25):
            c = float(x["close"].iloc[0]) * np.exp(np.cumsum(lr * m)); bars = _bars(x["ts"], c); liq = _scaled_liq({SYM: E.liquidity_table(bars)}, 100)
            res = _run(bars, fee=195.0, liq=liq); nav = res.nav; liqv = nav["liquidation"].astype(float).to_numpy(); peak = np.maximum.accumulate(np.r_[RESEARCH_INITIAL_USDT, liqv])[1:]; k = int((liqv / peak - 1).argmin())
            btc_val = float(nav["holdings"].iloc[k][SYM]) * c[k]; cash = float(nav["equity"].iloc[k]) - btc_val
            for u in (0.0, 0.10, 0.20, 0.30, 0.50):
                v = liqv[k] - cash * u; rows.append({"window": name, "amplify": m, "usdt_depeg_at_trough": u, "max_drawdown": float(v / peak[k] - 1), "exceeds_budget": bool(v / peak[k] - 1 < -BUDGET), "trough_date": str(pd.Timestamp(nav["ts"].iloc[k]).date())})
    return rows


def run_all(ctx=None, out: Path | None = None) -> dict:
    ctx = ctx or P20.Ctx(); out = Path(out or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    base = _run(ctx.btc, "2014-01-01", liq=ctx.liq_btc).nav; k = int(base["exposure"].astype(float).idxmax()); mw, mwd = float(base["exposure"].iloc[k]), str(pd.Timestamp(base["ts"].iloc[k]).date())
    R = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "压力情景（合成 / 放大 / 历史数据上的执行情景；不是预测，不是前瞻结果）", "fix25_hist_max_btc_weight": {"weight": mw, "date": mwd}, "instant": instant_shocks(mw, mwd), "rally_then_crash": rally_then_crash(),
         "consecutive_down_years": consecutive_down_years(), "amplified_history": amplified_history(ctx), "execution": execution_stress(ctx), "comparators_execution": comparators_execution(ctx), "combined": combined(ctx)}
    ex = []
    for r in R["rally_then_crash"]:
        r["exceeds_budget"] and ex.append(f"年内上涨 {r['rally_x']} 倍后、再平衡前下跌 {r['crash']:.0%}：回撤 {r['max_drawdown']:.1%}")
    for r in R["consecutive_down_years"]:
        r["exceeds_budget"] and ex.append(f"连续 {r['years']} 年每年下跌 {r['yearly_drop']:.0%}：回撤 {r['max_drawdown']:.1%}")
    for r in R["amplified_history"]:
        r["exceeds_budget"] and ex.append(f"{r['window']} 路径放大 {r['amplify']}×（BTC 回撤 {r['btc_mdd']:.1%}）：回撤 {r['max_drawdown']:.1%}")
    for r in R["execution"]:
        r["exceeds_budget"] and ex.append(f"{r['scenario']}：回撤 {r['max_drawdown']:.1%}")
    for r in R["combined"]:
        r["exceeds_budget"] and ex.append(f"{r['window']} ×{r['amplify']} + 200 bps + 成交量 1/100 + 低点 USDT 脱锚 {r['usdt_depeg_at_trough']:.0%}：回撤 {r['max_drawdown']:.1%}")
    th = R["instant"]["usdt_depeg_needed_alone"]["FIX25_Y（目标 25%）"]; ex.append(f"即时冲击（仓位 25% 时）：USDT 单独脱锚 ≥ {th:.0%}；或 BTC 下跌 80% 同时 USDT 脱锚 ≥ {R['instant']['fix25_usdt_depeg_needed_with_btc_drop']['BTC−80%']:.0%}")
    ex.append(f"即时冲击（仓位漂移到历史最高 {mw:.0%} 时）：BTC 一次性下跌 ≥ {BUDGET / mw:.0%} 即超过 50%")
    R["exceeds_budget_scenarios"] = ex
    (out / "stress21.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8"); return R


if __name__ == "__main__":
    r = run_all(); print("\n".join(r["exceeds_budget_scenarios"]))
