"""流水线：ingest → backtest（选模型 + 校准器）→ train（最终模型）→ predict（每日）→ 报告/历史记录。"""
from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .backtest.dataset import build_dataset, load_bars
from .backtest.evaluate import evaluate_all, regime_at
from .backtest.walkforward import MODEL_NAMES, run_walkforward
from .config import cfg, data_dir, model_dir, report_dir
from .data.ingest import load_status, run_ingest
from .data.store import BarStore
from .decision import evaluate_candidates
from .features import FEATURES
from .models.baselines import BaseRateModel, VolBootstrapModel
from .models.calibration import apply_cal, fit_isotonic
from .models.common import A_COLS, B_COLS
from .models.ml import CatBoostModel, LightGBMModel, ensemble

warnings.filterwarnings("ignore")


def _json_dump(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o)))


def _clean(o):
    """递归把 numpy / NaN 转成 JSON 友好对象。"""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


# ---------------------------------------------------------------- backtest
def select_model(pq: dict) -> dict:
    """按校准后 A 盈利概率的 Brier 技能选择生产模型；技能 ≤ 0 或 AUC ≤ 0.55 视为未通过验证。"""
    rows = []
    for m, r in pq.items():
        a = r.get("A_profit_cal", {})
        if a.get("available"):
            rows.append({"model": m, "brier_skill": a["brier_skill"], "auc": a["auc"], "ece": a["ece"], "n": a["n"]})
    if not rows:
        return {"selected_model": "ensemble", "validated": False, "reason": "无样本外评估", "table": []}
    df = pd.DataFrame(rows).sort_values("brier_skill", ascending=False)
    best = df.iloc[0]
    ok = bool(best["brier_skill"] > 0 and best["auc"] > 0.55 and best["n"] >= int(cfg()["thresholds"]["min_calibration_samples"]))
    return {"selected_model": str(best["model"]), "validated": ok,
            "reason": "样本外 Brier 技能 > 0 且 AUC > 0.55" if ok else f"最佳模型 {best['model']} 样本外 Brier 技能 {best['brier_skill']:.3f}、AUC {best['auc']:.3f}，未优于历史频率基准",
            "table": df.to_dict(orient="records")}


def backtest_all(symbols: list[str] | None = None, quick: bool = False) -> dict:
    t0 = time.time()
    bars = load_bars(symbols=symbols)
    ds = build_dataset(bars)
    wf = run_walkforward(ds, bars, quick=quick)
    res = evaluate_all(wf, ds)
    out = report_dir() / "backtest"
    out.mkdir(parents=True, exist_ok=True)
    signals = res.pop("_signals")
    for m, sig in signals.items():
        sig.to_parquet(out / f"signals_{m}.parquet", index=False)
        sig.to_csv(out / f"signals_{m}.csv", index=False)
    for m in wf["predA"]:
        wf["predA"][m].to_parquet(out / f"predA_{m}.parquet"); wf["predB"][m].to_parquet(out / f"predB_{m}.parquet")
    # 生产用校准器：用全部样本外预测拟合（供最终模型使用的近似）
    cals = {}
    min_n = int(cfg()["thresholds"]["min_calibration_samples"])
    for m, a in wf["predA"].items():
        b = wf["predB"][m]
        cals[m] = {
            "A_p_profit": fit_isotonic(a["p_profit"].to_numpy(float), ds["wide"].loc[a.index, "profit_A"].to_numpy(float), min_n),
            "A_p_dd": fit_isotonic(a["p_dd"].to_numpy(float), ds["wide"].loc[a.index, "dd_A_flag"].to_numpy(float), min_n),
            "B_p_fill": fit_isotonic(b["p_fill"].to_numpy(float), ds["long"].loc[b.index, "filled"].to_numpy(float), min_n),
            "B_p_profit": fit_isotonic(b["p_profit"].to_numpy(float), ds["long"].loc[b.index, "profit_B"].to_numpy(float), min_n),
            "B_p_dd": fit_isotonic(b["p_dd"].to_numpy(float), ds["long"].loc[b.index, "dd_B_flag"].to_numpy(float), min_n),
        }
    joblib.dump(cals, model_dir() / "calibrators.joblib")
    sel = select_model(res["probability_quality"])
    status = load_status() or {}
    summary = {
        "run_ts": datetime.now(timezone.utc).isoformat(), "quick": quick, "seconds": round(time.time() - t0),
        "data": {s: {"source": status.get("symbols", {}).get(s, {}).get("1d", {}).get("continuity", {}).get("sources"),
                     "first": status.get("symbols", {}).get(s, {}).get("1d", {}).get("continuity", {}).get("first"),
                     "last": status.get("symbols", {}).get(s, {}).get("1d", {}).get("continuity", {}).get("last")} for s in bars},
        "oos_range": {"first": str(min(a["ts"].min() for a in wf["predA"].values()).date()), "last": str(max(a["ts"].max() for a in wf["predA"].values()).date()),
                      "n_decision_days": int(len(next(iter(wf["predA"].values()))))},
        "config": {k: cfg()[k] for k in ("horizon_days", "wait_days", "costs", "thresholds", "limit_order", "backtest")},
        "selection": sel, **res,
    }
    _json_dump(_clean(summary), out / "summary.json")
    BarStore().save_json("backtest_runs", run_ts=datetime.now(timezone.utc), name="walkforward", payload=_clean({"selection": sel, "oos_range": summary["oos_range"]}))
    return _clean(summary)


def load_backtest_summary() -> dict | None:
    p = report_dir() / "backtest" / "summary.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------- train
def train_all(symbols: list[str] | None = None) -> dict:
    t0 = time.time()
    bars = load_bars(symbols=symbols)
    ds = build_dataset(bars)
    X, wide, XB, long = ds["X"], ds["wide"], ds["XB"], ds["long"]
    models = {
        "baserate": BaseRateModel().fit(wide, long),
        "volboot": VolBootstrapModel().fit({s: b["1d"] for s, b in bars.items()}, max(b["1d"]["ts"].max() for b in bars.values())),
        "lightgbm": LightGBMModel().fit(X, wide, XB, long),
        "catboost": CatBoostModel().fit(X, wide, XB, long),
    }
    meta = {"trained_at": datetime.now(timezone.utc).isoformat(), "n_rows_A": int(len(wide)), "n_rows_B": int(len(long)),
            "last_label_ts": wide["ts"].max().isoformat(), "features": FEATURES, "seconds": round(time.time() - t0),
            "feature_importance_lightgbm": models["lightgbm"].feature_importance().head(15).to_dict(orient="records"),
            "feature_importance_catboost": models["catboost"].feature_importance().head(15).to_dict(orient="records")}
    joblib.dump({"models": models, "meta": meta}, model_dir() / "final_models.joblib")
    _json_dump(_clean(meta), model_dir() / "final_models_meta.json")
    return _clean(meta)


def load_models() -> dict | None:
    p = model_dir() / "final_models.joblib"
    return joblib.load(p) if p.exists() else None


# ---------------------------------------------------------------- predict
def _confidence(sel: dict, pq: dict | None, model: str) -> dict:
    if not pq or model not in pq:
        return {"level": "低", "reason": "尚未进行样本外回测", "validated": False}
    a = pq[model].get("A_profit_cal", {})
    skill, auc, ece = a.get("brier_skill", 0), a.get("auc", 0.5), a.get("ece", 1)
    if sel.get("validated") and skill > 0.05 and auc > 0.65 and ece < 0.08:
        lvl = "中"          # 加密资产 90 天预测没有“高”置信度：有效独立样本太少
    elif sel.get("validated"):
        lvl = "低-中"
    else:
        lvl = "低"
    return {"level": lvl, "validated": bool(sel.get("validated")), "brier_skill": skill, "auc": auc, "ece": ece, "n_oos": a.get("n"), "reason": sel.get("reason")}


def predict_all(symbols: list[str] | None = None, as_of: str | None = None, persist: bool = True) -> dict:
    c = cfg()
    symbols = symbols or c["symbols"]
    pack = load_models()
    if pack is None:
        train_all(symbols); pack = load_models()
    models, meta = pack["models"], pack["meta"]
    cal_path = model_dir() / "calibrators.joblib"
    cals = joblib.load(cal_path) if cal_path.exists() else {}
    bt = load_backtest_summary()
    sel = (bt or {}).get("selection", {"selected_model": "ensemble", "validated": False, "reason": "尚未回测"})
    pq = (bt or {}).get("probability_quality")
    chosen_model = sel.get("selected_model", "ensemble")
    bars = load_bars(symbols=symbols)
    ds = build_dataset(bars)
    feats = ds["feats_all"]
    status = load_status() or {}
    out = {"run_ts": datetime.now(timezone.utc).isoformat(), "as_of_requested": as_of, "model_used": chosen_model, "validated": sel.get("validated"),
           "symbols": {}}
    grid = [float(d) for d in c["limit_order"]["discount_grid_pct"]]
    for sym in symbols:
        f = feats[feats["symbol"] == sym].sort_values("ts")
        if as_of:
            f = f[f["ts"] <= pd.Timestamp(as_of, tz="UTC")]
        if f.empty:
            continue
        row = f.iloc[[-1]].copy()
        X = row.reset_index(drop=True)
        XB = pd.concat([X.assign(d_pct=d) for d in grid], ignore_index=True)
        preds = {}
        for name in ("baserate", "volboot", "lightgbm", "catboost"):
            pa, pb = models[name].predict(X, XB)
            preds[name] = (pa, pb)
        preds["ensemble"] = ensemble(preds)
        per_model = {}
        for name, (pa, pb) in preds.items():
            pa, pb = pa.copy(), pb.copy()
            cl = cals.get(name, {})
            pa["p_profit_raw"], pa["p_dd_raw"] = pa["p_profit"], pa["p_dd"]
            pa["p_profit"] = apply_cal(cl.get("A_p_profit"), pa["p_profit"].to_numpy(float)); pa["p_dd"] = apply_cal(cl.get("A_p_dd"), pa["p_dd"].to_numpy(float))
            pb["p_fill"] = apply_cal(cl.get("B_p_fill"), pb["p_fill"].to_numpy(float)); pb["p_profit"] = apply_cal(cl.get("B_p_profit"), pb["p_profit"].to_numpy(float))
            pb["p_dd"] = apply_cal(cl.get("B_p_dd"), pb["p_dd"].to_numpy(float))
            pb["d_pct"] = XB["d_pct"].to_numpy()
            per_model[name] = {"A": pa.iloc[0].to_dict(), "B": pb.to_dict(orient="records")}
        close = float(row["close"].iloc[0])
        pa, pb = per_model[chosen_model]["A"], pd.DataFrame(per_model[chosen_model]["B"])
        dec = evaluate_candidates(close, pa, pb)
        conf = _confidence(sel, pq, chosen_model)
        if not conf["validated"]:
            dec["action_final"] = "暂不买入"
            dec["action_note"] = f"模型未通过样本外验证（{sel.get('reason')}），按规则不给出买入建议；以下概率仅供参考。"
        else:
            dec["action_final"] = dec["action"]
            dec["action_note"] = ""
        st = status.get("symbols", {}).get(sym, {}).get("1d", {})
        b1 = bars[sym]["1d"]
        last_bar = b1.iloc[-1]
        market = {"close": close, "bar_ts": row["ts"].iloc[0].isoformat(), "source": str(last_bar["source"]),
                  "data_first": st.get("continuity", {}).get("first"), "data_last": st.get("continuity", {}).get("last"),
                  "ma20": float(b1["close"].rolling(20).mean().iloc[-1]), "ma50": float(b1["close"].rolling(50).mean().iloc[-1]),
                  "ma90": float(b1["close"].rolling(90).mean().iloc[-1]), "ma200": float(b1["close"].rolling(200).mean().iloc[-1]),
                  "rsi14": float(row["rsi14"].iloc[0]), "rv20_annualized": float(row["rv20"].iloc[0]), "atr_rel": float(row["atr_rel"].iloc[0]),
                  "dd_from_365d_high": float(row["dd_from_365d_high"].iloc[0]), "ret_30d": float(row["ret_30d"].iloc[0]), "ret_90d": float(row["ret_90d"].iloc[0]),
                  "regime": str(regime_at(row).iloc[0]), "cross_check": st.get("cross_check")}
        dec_summary = (bt or {}).get("decision", {}).get(chosen_model, {}).get("summary")
        out["symbols"][sym] = {"market_data": market, "decision": dec, "per_model": per_model, "confidence": conf,
                               "validation": {"selected_model": chosen_model, "selection": sel, "decision_backtest_summary": dec_summary,
                                              "probability_quality": (pq or {}).get(chosen_model), "oos_range": (bt or {}).get("oos_range")},
                               "model_meta": {"trained_at": meta["trained_at"], "last_label_ts": meta["last_label_ts"], "n_rows_A": meta["n_rows_A"]}}
    out = _clean(out)
    if persist:
        pdir = report_dir() / "predictions"
        pdir.mkdir(parents=True, exist_ok=True)
        day = max(v["market_data"]["bar_ts"][:10] for v in out["symbols"].values()) if out["symbols"] else datetime.now(timezone.utc).date().isoformat()
        _json_dump(out, pdir / f"{day}.json")
        _json_dump(out, report_dir() / "latest_prediction.json")
        store = BarStore()
        for sym, v in out["symbols"].items():
            store.save_json("predictions", run_ts=datetime.now(timezone.utc), as_of=v["market_data"]["bar_ts"][:10], symbol=sym, payload=v)
    return out


def daily(symbols: list[str] | None = None, retrain: bool = False, retrain_after_days: int = 30) -> dict:
    st = run_ingest(symbols)
    pack = load_models()
    stale = pack is None or (datetime.now(timezone.utc) - datetime.fromisoformat(pack["meta"]["trained_at"])).days >= retrain_after_days
    trained = None
    if retrain or stale:
        trained = train_all(symbols)
    pred = predict_all(symbols)
    brief = {sym: {"close": v["market_data"]["close"], "action": v["decision"]["action_final"], "buy_range": v["decision"]["buy_range"],
                   "wait_range": v["decision"]["wait_range"], "model": pred["model_used"], "validated": pred["validated"]} for sym, v in pred["symbols"].items()}
    return {"ingest_probes": st["probes"], "retrained": trained is not None, "predictions": brief, "latest_file": str(report_dir() / "latest_prediction.json")}
