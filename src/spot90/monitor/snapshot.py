"""每日快照：行情（日线 + 参考价，含新鲜度）、指标、趋势/风险、距离、历史收益/波动/回撤、限价成交概率表、条件历史统计、方案比较摘要。
写入 reports_out/spot90/monitor/latest.json 与 history/<date>.json；Dashboard 只读取快照，不在页面里做模型计算。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import cfg, report_dir
from ..data.ingest import load_status
from ..data.store import BarStore
from .fill_model import predict_table
from .live import daily_bar_freshness, fetch_live_prices
from .position import scenario_table
from .rules import conditional_history, enrich, risk_components, risk_level_history


def _f(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def build_snapshot(symbols: list[str] | None = None, store: BarStore | None = None, live: dict | None = None) -> dict:
    symbols = symbols or cfg()["symbols"]
    store = store or BarStore()
    live = live if live is not None else fetch_live_prices(symbols)
    st = load_status() or {}
    now = datetime.now(timezone.utc)
    snap = {"generated_at": now.isoformat(), "generated_date_utc": now.date().isoformat(), "snapshot_max_age_hours": 30,
            "disclaimer": "仅供研究；不执行交易；不含任何上涨概率预测或 AI 买点。", "ingest_status_run_ts": st.get("run_ts"), "fallback_used": st.get("fallback_used"), "symbols": {}}
    mdir = report_dir() / "monitor"
    fv = json.loads((mdir / "fill_model_validation.json").read_text()) if (mdir / "fill_model_validation.json").exists() else None
    plans = json.loads((mdir / "plans_comparison.json").read_text()) if (mdir / "plans_comparison.json").exists() else None
    for sym in symbols:
        b = store.load(sym, "1d")
        if b.empty:
            snap["symbols"][sym] = {"error": "无日线数据"}
            continue
        e = enrich(b); r = e.iloc[-1]
        fresh = daily_bar_freshness(r["ts"], now)
        lv = live.get(sym, {})
        ref_price = lv.get("price")
        src = st.get("symbols", {}).get(sym, {}).get("1d", {})
        market = {"daily_bar": {"date": str(r["ts"].date()), "open": _f(r["open"]), "high": _f(r["high"]), "low": _f(r["low"]), "close": _f(r["close"]), "volume": _f(r["volume"]),
                                "source": str(b["source"].iloc[-1]), "freshness": fresh, "history_first": str(b["ts"].iloc[0].date()), "n_bars": int(len(b)),
                                "cross_check": src.get("cross_check"), "bitget_probe": (st.get("probes", {}) or {}).get("bitget")},
                  "live": lv, "price_for_display": ref_price if (ref_price and lv.get("status") == "正常") else _f(r["close"]),
                  "price_label": ("参考价（" + str(lv.get("source")) + "）") if (ref_price and lv.get("status") == "正常") else f"最后日线收盘（{fresh['status']}）",
                  "data_delayed": bool(fresh["lag_days"] > 0 or lv.get("status") != "正常")}
        px = market["price_for_display"]
        ind = {k: _f(r[k]) for k in ("ma20", "ma50", "ma90", "ma200", "ma200_slope30", "rsi14", "atr14", "rv20", "rv60", "rv90")}
        dist = {"vs_ma20": px / r["ma20"] - 1, "vs_ma50": px / r["ma50"] - 1, "vs_ma90": px / r["ma90"] - 1, "vs_ma200": px / r["ma200"] - 1,
                "vs_30d_high": px / r["high_30d"] - 1, "vs_30d_low": px / r["low_30d"] - 1, "vs_90d_high": px / r["high_90d"] - 1, "vs_90d_low": px / r["low_90d"] - 1,
                "vs_365d_high": px / r["high_365d"] - 1, "vs_365d_low": px / r["low_365d"] - 1,
                "levels": {"ma20": _f(r["ma20"]), "ma50": _f(r["ma50"]), "ma90": _f(r["ma90"]), "ma200": _f(r["ma200"]), "high_30d": _f(r["high_30d"]), "low_30d": _f(r["low_30d"]),
                           "high_90d": _f(r["high_90d"]), "low_90d": _f(r["low_90d"]), "high_365d": _f(r["high_365d"]), "low_365d": _f(r["low_365d"])}}
        dist = {k: (_f(v) if not isinstance(v, dict) else v) for k, v in dist.items()}
        windows = {str(n): {"ret": _f(r[f"ret_{n}d"]), "rv_ann": _f(r[f"rv{n}_ann"]), "dd_from_high": _f(r[f"dd_from_{n}d_high"]), "up_from_low": _f(r[f"up_from_{n}d_low"])} for n in (7, 30, 90)}
        windows["365"] = {"dd_from_high": _f(r["dd_from_365d_high"])}
        comps = risk_components(r)
        trend = {"state": str(r["state"]), "risk_score": _f(r["risk_score"]), "risk_level": str(r["risk_level"]), "components": comps,
                 "rule_text": "趋势：close/MA50/MA200 相对位置 + MA200 30日斜率；风险 = 波动率分位 + 趋势状态 + 偏离MA200过热 + 30日动量极端，各 0~25 分",
                 "state_history_30d": e["state"].tail(30).tolist(), "risk_history_90d": [_f(x) for x in e["risk_score"].tail(90)], "risk_dates_90d": [str(d.date()) for d in e["ts"].tail(90)]}
        cond = conditional_history(b, e).replace({np.nan: None}).to_dict(orient="records")
        rl_hist = risk_level_history(b, e).replace({np.nan: None}).to_dict(orient="records")
        fills = predict_table(r)
        fill_val = None
        if fv:
            lg = fv["overall"].get("logistic", {})
            fill_val = {"auc": lg.get("auc"), "brier": lg.get("brier"), "ece": lg.get("ece"), "n_oos": fv["n_oos"], "oos_range": fv["oos_range"],
                        "by_state": fv["by_state"].get("logistic"), "by_vol": fv["by_vol"].get("logistic"), "by_symbol": fv["by_symbol"].get("logistic", {}).get(sym),
                        "fill_vs_profit": fv["fill_vs_profit"]}
        pos = scenario_table(px, 10_000.0)
        plan_sum = (plans or {}).get("results", {}).get(sym)
        series = e.tail(400)[["ts", "close", "ma20", "ma50", "ma90", "ma200", "risk_score"]].copy(); series["ts"] = series["ts"].dt.strftime("%Y-%m-%d")
        snap["symbols"][sym] = {"market": market, "indicators": ind, "distances": dist, "windows": windows, "trend_risk": trend, "conditional_history_90d": cond,
                                "risk_level_history_90d": rl_hist,
                                "limit_fill_table": fills, "fill_model_validation": fill_val, "position_scenarios": pos, "plans": plan_sum,
                                "plans_method_note": "三方案为独立滚动模拟：每个历史起始日各自以 10,000 USDT 开一个假想账户、相同手续费/滑点/现金收益假设、窗口末平仓；不是一个连续资金账户的可执行净值曲线，不同起始日之间资金不滚存。",
                                "series_400d": series.replace({np.nan: None}).to_dict(orient="list")}
    out = report_dir() / "monitor"; (out / "history").mkdir(parents=True, exist_ok=True)
    txt = json.dumps(snap, ensure_ascii=False, indent=1, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (float(o) if isinstance(o, (np.floating,)) else str(o)))
    (out / "latest.json").write_text(txt)
    (out / "history" / f"{now.date().isoformat()}.json").write_text(txt)
    return snap


if __name__ == "__main__":
    s = build_snapshot()
    for sym, v in s["symbols"].items():
        m = v["market"]; t = v["trend_risk"]
        print(sym, m["price_label"], round(m["price_for_display"], 2), "日线", m["daily_bar"]["date"], m["daily_bar"]["freshness"]["status"], "| 趋势", t["state"], "风险", t["risk_score"], t["risk_level"])
