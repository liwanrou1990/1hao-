"""第二阶段实验：A（技术指标）/ B（+链上资金流）/ C（+衍生品，本环境 blocked）/ D（+宏观；ETF blocked）+ 特征消融 + 稳健性。
同一 14 折 Walk-Forward、同一测试日期、同一校准流程；按日配对的 Brier / LogLoss 差值做块自助（块长 90 天）显著性检验，
并给出按实验数量 Bonferroni 调整后的置信水平。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

from ..config import cfg, report_dir
from ..features import FEATURES
from ..features_onchain import build_onchain_features
from ..models.calibration import classification_report, quantile_report
from .dataset import build_dataset, load_bars
from .evaluate import block_bootstrap_ci, simulate_decisions, summarize_signals
from .walkforward import run_walkforward

PIT_GROUPS = ["chain_activity", "chain_valuation", "stablecoin", "eth_staking"]
NONPIT_GROUPS = ["exchange_flows", "defi_tvl"]


def attach_onchain(ds: dict) -> tuple[dict, dict]:
    keys = ds["feats_all"][["ts", "symbol"]]
    oc, groups = build_onchain_features(keys)
    fa = ds["feats_all"].merge(oc, on=["ts", "symbol"], how="left")
    ds = dict(ds)
    ds["feats_all"] = fa
    X = ds["wide"][["ts", "symbol"]].merge(fa, on=["ts", "symbol"], how="left"); X.index = ds["wide"].index
    XB = ds["long"][["ts", "symbol", "d_pct"]].merge(fa, on=["ts", "symbol"], how="left"); XB.index = ds["long"].index
    ds["X"], ds["XB"] = X, XB
    return ds, groups


def experiment_sets(groups: dict) -> dict[str, dict]:
    g = {k: v["cols"] for k, v in groups.items()}
    pit = sum((g[k] for k in PIT_GROUPS), [])
    nonpit = sum((g[k] for k in NONPIT_GROUPS), [])
    ex = {
        "A_tech": {"features": list(FEATURES), "main": True, "desc": "K 线 + 技术指标（原模型 A）"},
        "B_pit": {"features": FEATURES + pit, "main": True, "desc": "A + 链上活跃度/估值 + 稳定币供应 + Lido 质押（全部 Point-in-Time 安全）"},
        "B_full": {"features": FEATURES + pit + nonpit, "main": True, "desc": "B_pit + 交易所流量/储备 + DeFi TVL/手续费（非 PIT，仅作参考）"},
        "D_pit": {"features": FEATURES + pit + g["macro"], "main": True, "desc": "B_pit + 月度宏观（10Y 美债 / 标普 / 黄金）；ETF 资金流 blocked"},
        "onchain_pit_only": {"features": pit, "main": False, "desc": "只用 PIT 链上特征，不用价格/技术指标"},
    }
    for k in PIT_GROUPS + NONPIT_GROUPS + ["macro"]:
        ex[f"A+{k}"] = {"features": FEATURES + g[k], "main": False, "desc": f"消融：A + 单组 {k}"}
    for k in PIT_GROUPS:
        ex[f"Bpit-{k}"] = {"features": FEATURES + [c for c in pit if c not in g[k]], "main": False, "desc": f"消融：B_pit 去掉 {k}"}
    return ex


def _daily_scores(a: pd.DataFrame, y: np.ndarray, col: str) -> pd.DataFrame:
    p = np.clip(a[col].to_numpy(float), 1e-6, 1 - 1e-6)
    return pd.DataFrame({"ts": a["ts"].to_numpy(), "symbol": a["symbol"].to_numpy(), "brier": (p - y) ** 2,
                         "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p))})


def metrics_for(a: pd.DataFrame, b: pd.DataFrame, ds: dict, sym: str | None) -> dict:
    wide, long = ds["wide"], ds["long"]
    if sym:
        a = a[a["symbol"] == sym]; b = b[b["symbol"] == sym]
    y = wide.loc[a.index, "profit_A"].to_numpy(float)
    r = {"n": int(len(a)), "A_profit_cal": classification_report(a["p_profit_cal"].to_numpy(float), y, float(y.mean())),
         "A_profit_raw": classification_report(a["p_profit"].to_numpy(float), y, float(y.mean())),
         "A_dd15_cal": classification_report(a["p_dd_cal"].to_numpy(float), wide.loc[a.index, "dd_A_flag"].to_numpy(float)),
         "A_quantiles": quantile_report(*(a[f"ret_q{q}"].to_numpy(float) for q in ("05", "25", "50", "75", "95")), wide.loc[a.index, "ret_A"].to_numpy())}
    for t in ("mae10_A", "mae20_A"):
        if f"p_{t}_cal" in a:
            r[f"A_{t}_cal"] = classification_report(a[f"p_{t}_cal"].to_numpy(float), wide.loc[a.index, t].to_numpy(float))
    filled = long.loc[b.index, "filled"].to_numpy() == 1
    r["B_fill_cal"] = classification_report(b["p_fill_cal"].to_numpy(float), long.loc[b.index, "filled"].to_numpy(float))
    r["B_profit_given_fill_cal"] = classification_report(b.loc[filled, "p_profit_cal"].to_numpy(float), long.loc[b.index[filled], "profit_B"].to_numpy(float))
    for k in list(r):
        if isinstance(r[k], dict):
            r[k].pop("reliability", None)
    return r


def paired_ci(d: np.ndarray, levels=(0.95,), block: int = 90, n: int = 600, seed: int = 11) -> dict:
    d = np.asarray(d, float); d = d[np.isfinite(d)]
    rng = np.random.default_rng(seed); nb = int(np.ceil(len(d) / block)); stats = []
    for _ in range(n):
        starts = rng.integers(0, len(d) - block + 1, size=nb); idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[: len(d)]
        stats.append(d[idx].mean())
    out = {"mean": float(d.mean())}
    for lv in levels:
        al = (1 - lv) / 2
        out[f"ci{int(round(lv*1000))}"] = [float(np.quantile(stats, al)), float(np.quantile(stats, 1 - al))]
    return out


def run(quick: bool = False, log=print, lag_robustness: bool = True) -> dict:
    t0 = time.time()
    bars = load_bars()
    ds0 = build_dataset(bars)
    ds, groups = attach_onchain(ds0)
    ex = experiment_sets(groups)
    n_exp = len(ex)
    bonf = 1 - 0.05 / n_exp
    out_dir = report_dir() / "phase2"; (out_dir / "preds").mkdir(parents=True, exist_ok=True)
    results: dict = {"run_ts": datetime.now(timezone.utc).isoformat(), "n_experiments": n_exp, "bonferroni_level": bonf,
                     "groups": {k: {"pit_safe": v["pit_safe"], "n_features": len(v["cols"]), "cols": v["cols"]} for k, v in groups.items()},
                     "experiments": {}, "blocked": {"C_derivatives": "资金费率/未平仓量/清算：本环境所有衍生品数据源不可达，未运行",
                                                   "D_etf": "ETF 日资金流不可达，D 组只含月度宏观"}}
    daily: dict = {}
    thr = float(cfg()["thresholds"]["drawdown_threshold"])
    for name, spec in ex.items():
        models = ("lightgbm", "catboost") if spec["main"] else ("lightgbm",)
        t1 = time.time()
        wf = run_walkforward(ds, bars, quick=quick, log=lambda *_: None, features=spec["features"], ml_models=models, baselines=(name == "A_tech"))
        res: dict = {"desc": spec["desc"], "n_features": len(spec["features"]), "main": spec["main"], "models": {}}
        for m in wf["predA"]:
            a, b = wf["predA"][m], wf["predB"][m]
            a.to_parquet(out_dir / "preds" / f"predA_{name}_{m}.parquet"); b.to_parquet(out_dir / "preds" / f"predB_{name}_{m}.parquet")
            mm = {"pooled": metrics_for(a, b, ds, None), "BTCUSDT": metrics_for(a, b, ds, "BTCUSDT"), "ETHUSDT": metrics_for(a, b, ds, "ETHUSDT")}
            # 决策模拟
            sig = simulate_decisions(wf, ds, m, use_cal=True)
            sig.to_parquet(out_dir / f"signals_{name}_{m}.parquet", index=False)
            mm["decision"] = {"pooled": summarize_signals(sig, thr), "BTCUSDT": summarize_signals(sig[sig["symbol"] == "BTCUSDT"], thr),
                              "ETHUSDT": summarize_signals(sig[sig["symbol"] == "ETHUSDT"], thr)}
            y = ds["wide"].loc[a.index, "profit_A"].to_numpy(float)
            daily[(name, m)] = {"scores": _daily_scores(a, y, "p_profit_cal"), "strategy": sig[["ts", "symbol", "realized_ret", "ret_A"]].copy()}
            if m in wf.get("importance", {}):
                mm["importance_top20"] = wf["importance"][m].head(20).round(1).to_dict()
            res["models"][m] = mm
        res["seconds"] = round(time.time() - t1)
        results["experiments"][name] = res
        pa = res["models"]["lightgbm"]["pooled"]["A_profit_cal"]
        log(f"[{name}] {res['seconds']}s lightgbm pooled AUC={pa.get('auc', float('nan')):.3f} Brier={pa.get('brier', float('nan')):.4f} skill={pa.get('brier_skill', float('nan')):+.3f}")
    # ---- 配对检验：每个实验 vs A_tech（同模型）
    comps = {}
    for (name, m), d in daily.items():
        if name == "A_tech" or ("A_tech", m) not in daily:
            continue
        base = daily[("A_tech", m)]
        s = d["scores"].merge(base["scores"], on=["ts", "symbol"], suffixes=("", "_A"))
        st = d["strategy"].merge(base["strategy"], on=["ts", "symbol"], suffixes=("", "_A"))
        c = {}
        for sym in (None, "BTCUSDT", "ETHUSDT"):
            ss = s if sym is None else s[s["symbol"] == sym]; tt = st if sym is None else st[st["symbol"] == sym]
            c[sym or "pooled"] = {"brier_diff": paired_ci((ss["brier"] - ss["brier_A"]).to_numpy(), (0.95, bonf)),
                                  "logloss_diff": paired_ci((ss["logloss"] - ss["logloss_A"]).to_numpy(), (0.95, bonf)),
                                  "strategy_ret_all_days_diff": paired_ci((tt["realized_ret"] - tt["realized_ret_A"]).to_numpy(), (0.95, bonf))}
        comps[f"{name}|{m}"] = c
    results["paired_vs_A_tech"] = comps
    # ---- 稳健性：B_pit 用 lag=2
    if lag_robustness:
        import spot90.config as C
        orig = C.cfg()
        C.cfg.cache_clear()
        cfg_mod = dict(orig); cfg_mod["onchain"] = dict(orig.get("onchain", {})); cfg_mod["onchain"]["availability_lag_days"] = 2
        C.cfg = lambda: cfg_mod  # type: ignore
        try:
            ds2, groups2 = attach_onchain(ds0)
            feats = experiment_sets(groups2)["B_pit"]["features"]
            wf2 = run_walkforward(ds2, bars, quick=quick, log=lambda *_: None, features=feats, ml_models=("lightgbm",), baselines=False)
            a = wf2["predA"]["lightgbm"]; b = wf2["predB"]["lightgbm"]
            results["robustness_B_pit_lag2"] = {"pooled": metrics_for(a, b, ds2, None), "BTCUSDT": metrics_for(a, b, ds2, "BTCUSDT"), "ETHUSDT": metrics_for(a, b, ds2, "ETHUSDT")}
        finally:
            C.cfg = C.lru_cache(maxsize=1)(lambda: orig)  # type: ignore
    results["seconds_total"] = round(time.time() - t0)
    (out_dir / "phase2_summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o)))
    return results


if __name__ == "__main__":
    import sys
    r = run(quick="--quick" in sys.argv)
    print("done", r["seconds_total"], "s")
