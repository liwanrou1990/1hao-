"""第十六阶段实验运行器（预登记 docs/brando_pa_validation/phase16_minercycle/preregistration.md）。
基准（BH_BTC、B1 BTC SMA200、B2）+ {A, A0, B, C, D} × {X1…X5} × 窗口（训练 / 验证 / 后段检验 / 全样本 / 减半周期）× 单边成本 {0, 10, 25, 50} bps。
不做参数选择；三段只用于检查一致性。只写 reports_out/brando_pa/phase16_minercycle/。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, cfg
from ..holding.data import load_dataset
from ..holding.engine import HoldingBacktester
from ..holding.metrics import summarize
from ..pa11.signals import BreakoutRetestDetector, FailedBreakdownDetector, PullbackConfirmation
from ..pa11.structure import DailyStructureDetector
from ..pa11.zones import SupportResistanceZones
from . import data16
from .engine16 import ENTRIES, EXITS, CampaignBacktester

OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase16_minercycle"
COSTS16 = {0: (0.0, 0.0), 10: (5.0, 5.0), 25: (20.0, 5.0), 50: (45.0, 5.0)}           # 单边 bps → (手续费, 滑点)；往返 = 2 × 单边
WINDOWS16 = {"train": ("2014-01-01", "2017-12-31"), "valid": ("2018-01-01", "2021-12-31"), "test": ("2022-01-01", None), "full": ("2014-01-01", None),
             "H1": ("2014-01-01", "2016-07-08"), "H2": ("2016-07-09", "2020-05-10"), "H3": ("2020-05-11", "2024-04-19"), "H4": ("2024-04-20", None)}
WINDOW_NOTE = {"test": "后段检验（非盲测：2024–2026 已在第十至十五阶段被反复研究）", "H1": "部分周期（数据从 2014-01-01 起；2015-07-19 之前只有收盘价）", "H4": "未完成周期（截至最新已收盘日线）"}
B2_START = "2018-01-01"


def c_confirmations(d: pd.DataFrame) -> np.ndarray:
    """C 实验的“支撑 / 回踩 / 收复”确认（第十一阶段检测器，K 线量价代理）。无 OHLCV 的行一律 False。"""
    det = DailyStructureDetector(d[["ts", "open", "high", "low", "close", "volume"]].assign(volume=d["volume"].fillna(0.0)))
    z = SupportResistanceZones(det.sw, None, None, det.atr); out = np.zeros(len(d), bool); ok = d["ohlc_ok"].to_numpy(bool)
    A, B, C = PullbackConfirmation(), BreakoutRetestDetector(), FailedBreakdownDetector()
    for i in range(len(d)):
        if not ok[i] or not np.isfinite(det.atr[i]):
            continue
        zones = z.zones_at(i, det.close[i])
        if not zones:
            continue
        try:
            out[i] = bool(A.check(det, z, i, zones)["signal"] or B.check(det, z, i, zones)["signal"] or C.check(det, z, i, zones)["signal"])
        except Exception:  # noqa: BLE001
            out[i] = False
    return out


def episodes(f: pd.DataFrame, thr: float, merge_gap_days: int = 60, look_days: int = 730, start: str = "2014-01-01") -> list[dict]:
    """成本区事件（预登记 §9）。"""
    s = f[f.index >= pd.Timestamp(start, tz="UTC")]; hit = s["r"] <= thr; days = s.index[hit.fillna(False).to_numpy()]
    if len(days) == 0:
        return []
    groups = [[days[0], days[0]]]
    for t in days[1:]:
        if (t - groups[-1][1]).days <= merge_gap_days:
            groups[-1][1] = t
        else:
            groups.append([t, t])
    out = []; close = f["close"]
    for a, b in groups:
        fut = close[(close.index >= a) & (close.index <= a + pd.Timedelta(days=look_days))]; p0 = float(close.loc[a]); tmin = fut.idxmin(); pmin = float(fut.min())
        after = close[close.index > tmin]; back = after[after >= p0]
        out.append({"threshold": thr, "first_date": str(a.date()), "last_date": str(b.date()), "days_in_zone_span": (b - a).days + 1, "n_days_r_le_thr": int(((s["r"] <= thr) & (s.index >= a) & (s.index <= b)).sum()),
                    "first_price": p0, "first_cost_mid": float(f.loc[a, "cost_mid"]), "min_close_730d": pmin, "min_date": str(tmin.date()), "days_to_min": (tmin - a).days, "further_drawdown": pmin / p0 - 1,
                    "days_back_to_first_price": (back.index[0] - a).days if len(back) else None, "min_r": float(s.loc[a:b, "r"].min()), "reliability": str(f.loc[a, "reliability"])})
    return out


def run_baselines(d: pd.DataFrame, cost: int, a: str, b: str | None, c: dict) -> dict:
    fee, slip = COSTS16[cost]; bars = {"BTCUSDT": d[["ts", "open", "high", "low", "close", "volume"]]}; out = {}
    for name, spec in (("BH_BTC", {"name": "BH_BTC", "assets": ["BTCUSDT"], "mode": "buyhold"}), ("B1_BTC_SMA200", {"name": "B1_BTC_SMA200", "assets": ["BTCUSDT"], "mode": "trend", "risk_exit_dd": None})):
        out[name] = HoldingBacktester(bars, spec, fee, slip, a, b, sma=200).run()
    if pd.Timestamp(a) >= pd.Timestamp(B2_START):
        out["B2_modelB_state_machine"] = HoldingBacktester(load_dataset(c), {"name": "B2", "assets": ["BTCUSDT", "ETHUSDT"], "mode": "trend", "risk_mode": "state_machine", "risk_exit_dd": None}, fee, slip, a, b, sma=200, risk_params=c.get("trendsys", {}).get("risk", {})).run()
    return out


def campaign_quality(res, f: pd.DataFrame, eps10: list[dict]) -> list[dict]:
    """每个战役：平均成本 / 周期最低收盘、/ 首次进入价；卖出均价 / 周期最高收盘（两次成本区事件之间）。"""
    camps = res.final["campaigns"]; close = f["close"]; starts = [pd.Timestamp(e["first_date"], tz="UTC") for e in eps10]; out = []
    if camps is None or len(camps) == 0:
        return out
    for _, c in camps.iterrows():
        if not c["first_buy"]:
            continue
        t0 = pd.Timestamp(c["first_buy"], tz="UTC"); nxt = [s for s in starts if s > pd.Timestamp(c["end"], tz="UTC")]; t1 = nxt[0] if nxt else close.index[-1]
        ep = [e for e in eps10 if pd.Timestamp(e["first_date"], tz="UTC") <= t0 <= pd.Timestamp(e["first_date"], tz="UTC") + pd.Timedelta(days=730)]
        low = min(e["min_close_730d"] for e in ep) if ep else float(close[(close.index >= t0 - pd.Timedelta(days=365)) & (close.index <= t0 + pd.Timedelta(days=365))].min())
        peak = float(close[(close.index >= t0) & (close.index <= t1)].max())
        out.append({"campaign": int(c["campaign"]), "first_buy": c["first_buy"], "avg_entry": float(c["avg_entry"]), "cycle_low_close": low, "entry_over_low": float(c["avg_entry"]) / low,
                    "entry_over_first_touch": (float(c["avg_entry"]) / ep[0]["first_price"]) if ep else None, "avg_exit": None if pd.isna(c["avg_exit"]) else float(c["avg_exit"]), "cycle_peak_close": peak,
                    "exit_over_peak": None if pd.isna(c["avg_exit"]) else float(c["avg_exit"]) / peak, "status": c["status"], "net_pnl": float(c["net_pnl"]), "hold_days": int(c["hold_days"])})
    return out


def _row(res, name, kind, window, cost, btc_close) -> dict:
    s = summarize(res, btc_close); s.update({"name": name, "kind": kind, "window": window, "cost_one_side_bps": cost, "cost_round_trip_bps": 2 * cost, "time_flat": 1 - s["time_in_market"], "window_note": WINDOW_NOTE.get(window)})
    camps = res.final.get("campaigns") if isinstance(res.final, dict) else None
    s["n_campaigns_closed"] = int((camps["status"] == "closed").sum()) if camps is not None and len(camps) else (s["trades"]["n_round_trips"] if kind == "baseline" else 0)
    s["n_campaigns_open_at_end"] = int((camps["status"] != "closed").sum()) if camps is not None and len(camps) else 0
    return s


def judge(rows: list[dict], verifiable: bool) -> dict:
    """预登记 §10。"""
    R = {(r["name"], r["window"], r["cost_one_side_bps"]): r for r in rows}; out = {}
    def v(n, w, c, k="liquidation_net_profit_usdt"):
        r = R.get((n, w, c)); return None if r is None else r[k]
    names = sorted({r["name"] for r in rows if r["kind"] == "minercost"}) if verifiable else [f"{e}_{x}" for e in ENTRIES for x in EXITS]
    for n in names:
            if not verifiable:
                out[n] = {"verdict": "不可验证（成本模型未通过可靠性检查）"}; continue
            need = [(w, c) for w in ("valid", "test") for c in (10, 25)]
            if any(v(n, w, c) is None or v("BH_BTC", w, c) is None or v("B1_BTC_SMA200", w, c) is None for w, c in need):
                out[n] = {"verdict": "窗口 / 成本不完整，未判定"}; continue
            checks = {}
            for w in ("valid", "test"):
                for c in (10, 25):
                    mine = v(n, w, c); bh = v("BH_BTC", w, c); b1 = v("B1_BTC_SMA200", w, c); mdd = v(n, w, c, "max_drawdown"); bhmdd = v("BH_BTC", w, c, "max_drawdown")
                    b2 = v("B2_modelB_state_machine", w, c); b2mdd = v("B2_modelB_state_machine", w, c, "max_drawdown")
                    checks[f"{w}|{c}"] = {"net": mine, "BH_BTC": bh, "B1": b1, "B2": b2, "mdd": mdd, "BH_mdd": bhmdd, "B2_mdd": b2mdd, "beats_BH_and_B1": bool(mine > bh and mine > b1), "mdd_not_worse_than_BH": bool(mdd >= bhmdd - 1e-9),
                                          "vs_B2": "better_return" if (b2 is not None and mine > b2) else ("better_drawdown" if (b2mdd is not None and mdd > b2mdd) else "worse")}
            adv = all(c["beats_BH_and_B1"] and c["mdd_not_worse_than_BH"] for c in checks.values())
            closed_full = (R.get((n, "full", 10)) or {}).get("n_campaigns_closed", 0)
            verdict = ("有新增优势（验证与后段检验、10/25 bps 均胜 BH 与 B1，回撤不劣于 BH）" if adv else "无新增优势") + (f"；全样本闭合战役 {closed_full} 个 < 5 → 周期样本极少，不能证明稳定规律" if closed_full < 5 else "")
            out[n] = {"checks": checks, "advantage": adv, "closed_campaigns_full_10bps": closed_full, "verdict": verdict, "forward_observation": "建议前瞻观察（只记录，不执行）" if adv else "不采用"}
    return out


def run_phase16(c: dict | None = None, out_dir: Path | None = None, windows: dict | None = None, costs: list[int] | None = None, combos: list[tuple] | None = None, write: bool = True) -> dict:
    c = c or cfg(); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True)
    d = data16.load_btc_daily(); f = data16.signal_frame(d); latest = str(d["ts"].iloc[-1].date())
    rel = data16.reliability_checks(variant="corrected"); rel0 = data16.reliability_checks(variant="preregistered"); verifiable = rel["verifiable"]
    cconf = c_confirmations(d); windows = windows or WINDOWS16; costs = costs or list(COSTS16); combos = combos or [(e, x) for e in ENTRIES for x in EXITS]
    btc_close = d.set_index("ts")["close"]; eps = {thr: episodes(f, thr) for thr in (1.1, 1.0, 0.9, 0.8)}
    rows, quality, fills_out, camps_out, curves = [], {}, [], [], {}
    for w, (a, b) in windows.items():
        b = b or latest
        for cost in costs:
            fee, slip = COSTS16[cost]
            for name, res in run_baselines(d, cost, a, b, c).items():
                rows.append(_row(res, name, "baseline", w, cost, btc_close))
                if cost == 10 and w in ("full", "test", "valid", "train"):
                    curves[f"{w}|{name}"] = res.nav[["ts", "equity", "liquidation", "drawdown"]].assign(ts=lambda x: x["ts"].astype(str)).to_dict("list")
            if not verifiable:
                continue
            for e, x in combos:
                res = CampaignBacktester(d, f, e, x, fee, slip, a, b, c_confirm=cconf).run(); n = f"{e}_{x}"
                rows.append(_row(res, n, "minercost", w, cost, btc_close))
                if cost == 10:
                    quality[f"{w}|{n}"] = campaign_quality(res, f, eps[1.0])
                    if w == "full":
                        curves[f"{w}|{n}"] = res.nav[["ts", "equity", "liquidation", "drawdown"]].assign(ts=lambda x_: x_["ts"].astype(str)).to_dict("list")
                        fl = res.fills.copy(); fl.insert(0, "strategy", n); fills_out.append(fl)
                        cp = res.final["campaigns"].copy(); cp.insert(0, "strategy", n); camps_out.append(cp)
                    if w in ("valid", "test", "train"):
                        curves[f"{w}|{n}"] = res.nav[["ts", "equity", "liquidation", "drawdown"]].assign(ts=lambda x_: x_["ts"].astype(str)).to_dict("list")
    J = judge(rows, verifiable)
    resd = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "backtest（历史回测；日线决策、链上 / 成本滞后 1 日、周线周日收盘更新、次日开盘执行；非前瞻）", "latest_complete_day": latest,
            "windows": {k: (a, b or latest) for k, (a, b) in windows.items()}, "window_notes": WINDOW_NOTE, "costs_one_side_bps": costs, "cost_split": {k: {"fee_bps": v[0], "slippage_bps": v[1], "round_trip_bps": 2 * k} for k, v in COSTS16.items()},
            "preregistration": "docs/brando_pa_validation/phase16_minercycle/preregistration.md", "cost_model_reliability": {"corrected": rel, "preregistered": rel0}, "verifiable": verifiable,
            "data_audit": data16.audit(d), "episodes": eps, "campaign_quality_10bps": quality, "rows": rows, "verdicts": J, "c_confirm_days": int(cconf.sum()), "real_trading": "disabled (no implementation)"}
    if write:
        (out / "phase16_results.json").write_text(json.dumps(resd, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out / "phase16_curves_10bps.json").write_text(json.dumps(curves, ensure_ascii=False, default=str), encoding="utf-8")
        if fills_out:
            fl = pd.concat(fills_out, ignore_index=True); fl["basis"] = fl["basis"].apply(lambda x: json.dumps(x, ensure_ascii=False)); fl.to_csv(out / "phase16_fills_full_10bps.csv", index=False)
            cp = pd.concat(camps_out, ignore_index=True); cp["entry_basis"] = cp["entry_basis"].apply(lambda x: json.dumps(x, ensure_ascii=False)); cp["exit_basis"] = cp["exit_basis"].apply(lambda x: json.dumps(x, ensure_ascii=False)); cp.to_csv(out / "phase16_campaigns_full_10bps.csv", index=False)
    return resd


if __name__ == "__main__":
    r = run_phase16()
    print(json.dumps({"rows": len(r["rows"]), "verifiable": r["verifiable"], "latest": r["latest_complete_day"], "c_confirm_days": r["c_confirm_days"]}, ensure_ascii=False))
