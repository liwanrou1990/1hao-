"""第十八阶段运行器（预登记 docs/brando_pa_validation/phase18_b2recovery/preregistration.md）。
变体 × 窗口（2017 早期检验 / 2018–2021 开发 / 2022 → 重复检验 / 全样本）× 单边成本 {0, 15, 25, 50}；指标、审计与判定；只写输出目录。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, cfg
from ..holding.data import load_dataset
from ..holding.engine import HoldingBacktester
from ..holding.metrics import drawdown_stats, regime_labels, summarize
from ..pa16 import data16
from ..pa17 import missed17
from .variants18 import VARIANTS, RecoveryBacktester

RAW18 = PROJECT_ROOT / "data_store" / "brando_pa" / "phase18_b2recovery" / "raw"
OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase18_b2recovery"
COSTS18 = {0: (0.0, 0.0), 15: (10.0, 5.0), 25: (20.0, 5.0), 50: (45.0, 5.0)}
WINDOWS18 = {"early_2017": ("2017-01-01", "2017-12-31"), "dev_2018_2021": ("2018-01-01", "2021-12-31"), "repeat_2022_2026": ("2022-01-01", None), "full": ("2017-01-01", None)}
WINDOW_LABEL = {"early_2017": "早期检验（B2 未在此段运行过；价格历史众所周知，非盲测）", "dev_2018_2021": "开发数据（含 2021 年典型空仓案例）", "repeat_2022_2026": "重复检验数据（已被反复研究，非盲测）", "full": "全样本"}


def load_bars() -> dict:
    btc = data16.load_btc_daily(); btc = btc[btc["ts"] >= pd.Timestamp("2016-01-01", tz="UTC")][["ts", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
    p10 = load_dataset()["ETHUSDT"].copy(); p10["ts"] = pd.to_datetime(p10["ts"], utc=True).dt.normalize()
    cb = pd.read_csv(RAW18 / "coinbase_eth_usd_1d.csv"); cb["ts"] = pd.to_datetime(cb["ts"], utc=True).dt.normalize(); cb = cb[cb["ts"] < p10["ts"].min()].set_index("ts")
    full = pd.date_range(cb.index.min(), p10["ts"].min() - pd.Timedelta(days=1), freq="1D"); filled = cb.reindex(full); gap = filled["close"].isna()
    filled["close"] = filled["close"].ffill(); filled["open"] = filled["open"].fillna(filled["close"]); filled["high"] = filled["high"].fillna(filled["close"]); filled["low"] = filled["low"].fillna(filled["close"]); filled["volume"] = filled["volume"].fillna(0.0)
    filled = filled.reset_index().rename(columns={"index": "ts"}); filled["ffilled"] = gap.values
    eth = pd.concat([filled[["ts", "open", "high", "low", "close", "volume"]], p10[["ts", "open", "high", "low", "close", "volume"]]], ignore_index=True).sort_values("ts").reset_index(drop=True)
    btc["ts"] = pd.to_datetime(btc["ts"], utc=True); last = min(btc["ts"].max(), eth["ts"].max())
    return {"BTCUSDT": btc[btc["ts"] <= last].reset_index(drop=True), "ETHUSDT": eth[eth["ts"] <= last].reset_index(drop=True), "_eth_ffilled_days": [str(x.date()) for x in filled.loc[filled["ffilled"], "ts"]]}


def run_variant(bars, name, cost, a, b, c):
    fee, slip = COSTS18[cost]
    bt = RecoveryBacktester({k: v for k, v in bars.items() if not k.startswith("_")}, {"name": name, "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, slip, a, b, variant=name, risk_params=c.get("trendsys", {}).get("risk", {}))
    return bt, bt.run()


def baselines(bars, cost, a, b):
    fee, slip = COSTS18[cost]; bb = {k: v for k, v in bars.items() if not k.startswith("_")}; out = {}
    out["B1_BTC_SMA200"] = HoldingBacktester({"BTCUSDT": bb["BTCUSDT"]}, {"name": "B1", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None}, fee, slip, a, b, sma=200).run()
    out["BH_BTC"] = HoldingBacktester({"BTCUSDT": bb["BTCUSDT"]}, {"name": "BH_BTC", "assets": ["BTCUSDT"], "mode": "buyhold"}, fee, slip, a, b).run()
    out["BH_5050"] = HoldingBacktester(bb, {"name": "BH_5050", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "buyhold"}, fee, slip, a, b).run()
    return out


def _returns(bars, nav):
    idx = pd.DatetimeIndex(pd.to_datetime(nav["ts"], utc=True)); px = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        b = bars[s].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); px[s] = b.set_index("ts")["close"].astype(float).reindex(idx)
    mix = 0.5 * px["BTCUSDT"].pct_change() + 0.5 * px["ETHUSDT"].pct_change(); return px, mix


def metrics(bt, res, bars, cost) -> dict:
    s = summarize(res); nav = res.nav.copy(); nav["ts"] = pd.to_datetime(nav["ts"], utc=True); liq = nav.set_index("ts")["liquidation"]
    px, mix = _returns(bars, nav); sr = liq.pct_change(); up, dn = mix > 0, mix < 0
    lab = regime_labels(px["BTCUSDT"]).reindex(liq.index, method="ffill"); expo = nav.set_index("ts")["exposure"]
    eps = missed17.flat_episodes(res, {k: v for k, v in bars.items() if not k.startswith("_")})
    unnec = [e for e in eps if e["class"] == "不必要的长期空仓"]
    log = pd.DataFrame(bt.risk.log); trans = getattr(bt.risk, "transitions", [])
    halts = [pd.Timestamp(t["ts"]) for t in trans if t["to"] == "EMERGENCY_HALT"]
    fills = res.fills.copy(); fills["ts"] = pd.to_datetime(fills["ts"], utc=True); buys = fills[fills["side"] == "BUY"].sort_values("ts")
    reentries = []
    for h in halts:
        nb = buys[buys["ts"] > h]
        if len(nb):
            t = nb["ts"].iloc[0]; later = liq[liq.index >= t + pd.Timedelta(days=60)]
            if len(later):
                rehalt = any(t < x <= t + pd.Timedelta(days=90) for x in halts)
                reentries.append({"halt": str(h.date()), "reentry": str(t.date()), "days_halt_to_reentry": int((t - h).days), "liq_change_60d": float(later.iloc[0] / liq.loc[t] - 1), "rehalt_within_90d": bool(rehalt)})
    loss_rate = float(np.mean([r["liq_change_60d"] < 0 for r in reentries])) if reentries else None
    dd = drawdown_stats(liq)
    return {"liquidation_net_profit_usdt": s["liquidation_net_profit_usdt"], "final_liquidation_usdt": s["liquidation_usdt"], "max_drawdown": float((liq / liq.cummax() - 1).min()), "longest_drawdown_days": dd["longest_drawdown_days"],
            "up_capture_vs_5050": float(sr[up].sum() / mix[up].sum()) if up.any() else None, "down_capture_vs_5050": float(sr[dn].sum() / mix[dn].sum()) if dn.any() else None,
            "avg_exposure_bull_months": float(expo[lab == "bull"].mean()) if (lab == "bull").any() else None, "avg_exposure_bear_months": float(expo[lab == "bear"].mean()) if (lab == "bear").any() else None,
            "time_in_market": s["time_in_market"], "unnecessary_long_flat": {"n": len(unnec), "days": int(sum(e["days_flat"] for e in unnec)), "missed_usdt": float(sum(e["missed_usdt_if_positive"] for e in unnec))},
            "n_halts": len(halts), "reentries": reentries, "reentry_loss_rate_60d": loss_rate, "rehalts_within_90d": int(sum(r["rehalt_within_90d"] for r in reentries)),
            "fees_usdt": s["fees_usdt"], "slippage_usdt": s["slippage_usdt"], "n_fills": s["n_fills"], "state_share": log["state"].value_counts(normalize=True).round(4).to_dict() if len(log) else {},
            "yearly_return": {y: float(v) for y, v in s["annual_returns"].items()}, "cost_one_side_bps": cost}


def audit_episodes(bars, c) -> list[dict]:
    """预登记 §2：原 B2（2018 起，15 bps）7 次不必要长期空仓的完整还原。"""
    bt, res = run_variant(bars, "V0_original", 15, "2018-01-01", None, c); eps = [e for e in missed17.flat_episodes(res, {k: v for k, v in bars.items() if not k.startswith("_")}) if e["class"] == "不必要的长期空仓"]
    trans = pd.DataFrame(bt.risk.transitions); trans["ts"] = pd.to_datetime(trans["ts"]); fills = res.fills.copy(); fills["ts"] = pd.to_datetime(fills["ts"], utc=True); out = []
    for e in eps:
        t0 = pd.Timestamp(e["exit_date"], tz="UTC"); t1 = pd.Timestamp(e["reentry_date"], tz="UTC") if e["reentry_date"] else None
        reason = fills[(fills["ts"] == t0) & (fills["symbol"] == e["symbol"]) & (fills["side"] == "SELL")]["reason"].tolist()
        tr = trans[(trans["ts"] >= t0 - pd.Timedelta(days=2)) & (trans["ts"] <= (t1 or t0 + pd.Timedelta(days=200)))]
        b = bars[e["symbol"]].copy(); b["ts"] = pd.to_datetime(b["ts"], utc=True); px = b.set_index("ts")["close"].astype(float); sma = px.rolling(200).mean()
        after = px[px.index > t0]; back = after[after >= e["exit_price"]]; above = after[after > sma.reindex(after.index)]
        out.append({**{k: e[k] for k in ("symbol", "exit_date", "reentry_date", "days_flat", "exit_price", "reentry_price", "reentry_over_exit", "max_drawdown_while_flat", "max_gain_while_flat", "missed_usdt_if_positive")},
                    "exit_reason": reason, "risk_transitions": tr[["ts", "from", "to", "why", "halt_count"]].assign(ts=lambda x: x["ts"].astype(str)).to_dict("records"),
                    "days_until_price_back_to_exit": int((back.index[0] - t0).days) if len(back) else None, "days_until_close_above_sma200": int((above.index[0] - t0).days) if len(above) else None,
                    "reentry_condition": "冷静期 + 恢复期结束且收盘 > SMA200" if "risk_state_machine_halt" in reason else "趋势重新开启（收盘 > SMA200）"})
    return out


def judge(R: dict) -> dict:
    out = {}
    for v in VARIANTS:
        if v in ("V0_original", "C0_original_topup"):
            continue
        checks = {}; ok = True
        for w in ("early_2017", "dev_2018_2021", "repeat_2022_2026"):
            for c in (15, 25):
                m, b = R[(v, w, c)], R[("V0_original", w, c)]
                c1 = m["liquidation_net_profit_usdt"] >= b["liquidation_net_profit_usdt"]; c2 = m["max_drawdown"] >= b["max_drawdown"] - 0.03
                n_re = len(m["reentries"]); c3 = True
                if n_re >= 3 and m["reentry_loss_rate_60d"] is not None and b["reentry_loss_rate_60d"] is not None:
                    c3 = m["reentry_loss_rate_60d"] <= b["reentry_loss_rate_60d"] + 0.20
                checks[f"{w}|{c}"] = {"net": m["liquidation_net_profit_usdt"], "b2_net": b["liquidation_net_profit_usdt"], "mdd": m["max_drawdown"], "b2_mdd": b["max_drawdown"], "net_ok": c1, "mdd_ok": c2, "reentry_ok": c3}
                ok = ok and c1 and c2 and c3
        full, fb = R[(v, "full", 15)], R[("V0_original", "full", 15)]
        yrs = [y for y in full["yearly_return"] if y in fb["yearly_return"]]; pos = sum(1 for y in yrs if full["yearly_return"][y] > fb["yearly_return"][y] + 1e-12); ties = sum(1 for y in yrs if abs(full["yearly_return"][y] - fb["yearly_return"][y]) <= 1e-12)
        full_ok = full["liquidation_net_profit_usdt"] > fb["liquidation_net_profit_usdt"] and pos >= (len(yrs) - ties) / 2 and (len(yrs) - ties) > 0
        adv = bool(ok and full_ok)
        out[v] = {"checks": checks, "full_net": full["liquidation_net_profit_usdt"], "b2_full_net": fb["liquidation_net_profit_usdt"], "years_better": pos, "years_equal": ties, "n_years": len(yrs), "full_ok": bool(full_ok), "advantage": adv,
                  "verdict": "具有增量优势 → 建议进入下一阶段独立影子观察（原 B2 不变）" if adv else "无增量优势 → 维持原 B2 不变"}
    return out


def run_phase18(c: dict | None = None, out_dir: Path | None = None, windows: dict | None = None, costs: list[int] | None = None, variants: list[str] | None = None, write: bool = True) -> dict:
    c = c or cfg(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); bars = load_bars(); latest = str(bars["BTCUSDT"]["ts"].iloc[-1].date())
    windows = windows or WINDOWS18; costs = costs or list(COSTS18); variants = variants or list(VARIANTS); R = {}; base = {}; curves = {}
    for w, (a, b) in windows.items():
        b = b or latest
        for cost in costs:
            for n, res in baselines(bars, cost, a, b).items():
                s = summarize(res); liq = res.nav.set_index("ts")["liquidation"]
                base[(n, w, cost)] = {"liquidation_net_profit_usdt": s["liquidation_net_profit_usdt"], "max_drawdown": float((liq / liq.cummax() - 1).min()), "n_fills": s["n_fills"], "time_in_market": s["time_in_market"]}
                if cost == 15 and w == "full":
                    curves[f"{w}|{n}"] = res.nav[["ts", "liquidation"]].assign(ts=lambda x: x["ts"].astype(str)).to_dict("list")
            for v in variants:
                bt, res = run_variant(bars, v, cost, a, b, c); R[(v, w, cost)] = metrics(bt, res, bars, cost)
                if cost == 15 and w == "full":
                    curves[f"{w}|{v}"] = res.nav[["ts", "liquidation", "exposure"]].assign(ts=lambda x: x["ts"].astype(str)).to_dict("list")
    full_set = all((v, w, cc) in R for v in VARIANTS for w in ("early_2017", "dev_2018_2021", "repeat_2022_2026", "full") for cc in (15, 25))
    J = judge(R) if full_set else {}
    resd = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；日线决策、次日开盘执行；2018–2026 为开发与重复检验数据，非盲测）", "latest_complete_day": latest,
            "windows": {k: (a, b or latest) for k, (a, b) in windows.items()}, "window_labels": WINDOW_LABEL, "costs_one_side_bps": costs, "variants": VARIANTS, "eth_ffilled_days": bars["_eth_ffilled_days"],
            "preregistration": "docs/brando_pa_validation/phase18_b2recovery/preregistration.md", "results": {f"{v}|{w}|{cc}": m for (v, w, cc), m in R.items()}, "baselines": {f"{n}|{w}|{cc}": m for (n, w, cc), m in base.items()},
            "episode_audit_original_b2": audit_episodes(bars, c) if write else None, "verdicts": J, "real_trading": "disabled (no implementation)"}
    if write:
        (out / "phase18_results.json").write_text(json.dumps(resd, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out / "phase18_curves_15bps.json").write_text(json.dumps(curves, ensure_ascii=False, default=str), encoding="utf-8")
    return resd


if __name__ == "__main__":
    r = run_phase18()
    print(json.dumps({"n_results": len(r["results"]), "verdicts": {k: v["verdict"] for k, v in r["verdicts"].items()}}, ensure_ascii=False, indent=1))
