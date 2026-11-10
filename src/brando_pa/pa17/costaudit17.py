"""矿工成本可信度审计（预登记 §1）。不使用真实成本（不可得），用三种方法估计误差：
假设敏感性网格、跨模型分歧、可观测一致性（价格只用于检验，不进入成本）。判定规则事先固定。"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from ..pa15 import data15, factors15
from ..pa16 import data16

KEY_DATES = ["2015-01-14", "2018-12-15", "2020-03-12", "2022-11-21"]
GRID_FLEET = {"deploy": ("newest", "geomean_newest_prev"), "lag_days": (60, 90, 180), "max_age_years": (4, 5, 6), "obsolete_ratio": (5.0, 10.0, 20.0)}
GRID_SCALAR = {"pue": (1.05, 1.10, 1.20), "elec": (0.04, 0.05, 0.06, 0.07, 0.08), "spec_err": (0.85, 1.0, 1.15)}


def fleet(hr: pd.Series, deploy: str, lag_days: int, max_age_years: int, obsolete_ratio: float) -> pd.Series:
    """参数化的矿机代际模型（与第十六阶段同一逻辑；第十六阶段代码不改）。返回逐日机队效率 J/TH。"""
    hw = [(pd.Timestamp(d, tz="UTC") + pd.Timedelta(days=lag_days), e) for d, _, e in data16.HARDWARE]
    h30 = hr.rolling(30, min_periods=1).mean(); stock: list[list] = []; out = {}
    for t, target in h30.items():
        avail = [e for d0, e in hw if d0 <= t]
        if not avail:
            new_e = data16.LEGACY_JTH
        elif deploy == "newest" or len(avail) == 1:
            new_e = avail[-1]
        else:
            new_e = float(np.sqrt(avail[-1] * avail[-2]))
        best = avail[-1] if avail else data16.LEGACY_JTH
        if not stock:
            stock.append([new_e, float(target), t])
        repl = 0.0; keep = []
        for e, q, d0 in stock:
            if e > obsolete_ratio * best or (t - d0).days >= max_age_years * 365:
                repl += q
            else:
                keep.append([e, q, d0])
        stock = keep + ([[new_e, repl, t]] if repl > 0 else [])
        delta = float(target) - sum(q for _, q, _ in stock)
        if delta > 0:
            stock.append([new_e, delta, t])
        elif delta < 0:
            need = -delta
            for row in sorted(stock, key=lambda r: -r[0]):
                take = min(row[1], need); row[1] -= take; need -= take
                if need <= 1e-12:
                    break
            stock = [r for r in stock if r[1] > 1e-12]
        tot = sum(q for _, q, _ in stock); out[t] = sum(e * q for e, q, _ in stock) / tot if tot > 0 else np.nan
    return pd.Series(out)


def _base(panel):
    hr = panel["bci_hashrate_ths"].combine_first(panel["HashRate"]).dropna(); hr = hr[hr.index >= pd.Timestamp("2012-06-01", tz="UTC")]
    subs = data16.subsidy_at(panel.index); blocks = (panel["IssTotNtv"] / subs).rolling(30, min_periods=1).mean(); fees = panel["FeeTotNtv"].rolling(30, min_periods=1).mean()
    btc_day = (blocks * subs + fees).reindex(hr.index); h30 = hr.rolling(30, min_periods=1).mean()
    return hr, h30, btc_day


def grid_costs(panel: pd.DataFrame | None = None) -> pd.DataFrame:
    """每个网格组合的逐日成本（USD/BTC）。列 = 组合编号；附 combos 元数据。"""
    panel = panel if panel is not None else data15.build_panel("btc"); hr, h30, btc_day = _base(panel)
    cols = {}; meta = []
    for deploy, lag, age, obs in itertools.product(*GRID_FLEET.values()):
        eff = fleet(hr, deploy, lag, age, obs)
        energy_per_btc_kwh = h30 * eff * 86400.0 / 3.6e6 / btc_day.replace(0, np.nan)
        for pue, elec, spec in itertools.product(*GRID_SCALAR.values()):
            k = len(meta); cols[k] = energy_per_btc_kwh * pue * spec * elec
            meta.append({"id": k, "deploy": deploy, "lag_days": lag, "max_age_years": age, "obsolete_ratio": obs, "pue": pue, "elec": elec, "spec_err": spec})
    df = pd.DataFrame(cols); df.attrs["combos"] = meta
    return df


def audit(panel: pd.DataFrame | None = None, close: pd.Series | None = None) -> dict:
    panel = panel if panel is not None else data15.build_panel("btc")
    G = grid_costs(panel); G = G[G.index >= pd.Timestamp("2014-01-01", tz="UTC")]
    close = close if close is not None else data16.load_btc_daily().set_index("ts")["close"]
    close.index = pd.DatetimeIndex(close.index).normalize()
    main = data16.miner_cost(panel)["cost_mid"]
    last = min(close.index.max(), G.index.max()); dates = KEY_DATES + [str(last.date())]
    out = {"n_grid_combos": int(G.shape[1]), "grid": {**{k: list(v) for k, v in GRID_FLEET.items()}, **{k: list(v) for k, v in GRID_SCALAR.items()}}, "key_dates": {}, "criteria": {}}
    flips = 0; wide = 0
    for d in dates:
        t = pd.Timestamp(d, tz="UTC"); g = G.loc[t - pd.Timedelta(days=1)].dropna(); px = float(close.loc[t])   # 成本滞后 1 日
        share = float((px <= g).mean()); spread = float(g.max() / g.min())
        wide += spread >= 2.0; flips += 0.2 <= share <= 0.8
        out["key_dates"][d] = {"price": px, "grid_min": float(g.min()), "grid_p10": float(g.quantile(.1)), "grid_median": float(g.median()), "grid_p90": float(g.quantile(.9)), "grid_max": float(g.max()), "max_over_min": spread,
                               "p90_over_p10": float(g.quantile(.9) / g.quantile(.1)), "main_model_mid": float(main.loc[t - pd.Timedelta(days=1)]), "share_of_grid_price_le_cost": share,
                               "price_over_cost_range": [px / float(g.max()), px / float(g.min())]}
    # 全期离散度
    sp = (G.max(axis=1) / G.min(axis=1)); sp9 = (G.quantile(.9, axis=1) / G.quantile(.1, axis=1))
    out["dispersion_all_days"] = {"max_over_min_median": float(sp.median()), "p90_over_p10_median": float(sp9.median()), "share_days_max_over_min_ge_2": float((sp >= 2).mean())}
    # 跨模型
    p15 = factors15.btc_factors()["miner_cost_mid"]; j = pd.concat([main.rename("v16"), p15.rename("v15")], axis=1).dropna(); j = j[j.index >= "2018-01-01"]; ratio = j["v16"] / j["v15"]
    out["cross_model"] = {"median_v16_over_v15": float(ratio.median()), "p5": float(ratio.quantile(.05)), "p95": float(ratio.quantile(.95)), "min": float(ratio.min()), "max": float(ratio.max()), "n_days": int(len(ratio))}
    c2 = pd.concat([close.rename("px"), main.shift(1, freq="D").rename("m16"), p15.shift(1, freq="D").rename("m15")], axis=1).dropna(); c2 = c2[c2.index >= "2018-01-01"]
    a = c2["px"] <= c2["m16"]; b = c2["px"] <= c2["m15"]
    out["cross_model"]["days_le_cost_v16"] = int(a.sum()); out["cross_model"]["days_le_cost_v15"] = int(b.sum()); out["cross_model"]["days_both"] = int((a & b).sum())
    out["cross_model"]["jaccard_le_cost_days"] = float((a & b).sum() / max((a | b).sum(), 1))
    # 可观测一致性
    hr = panel["bci_hashrate_ths"].combine_first(panel["HashRate"]); h30 = hr.rolling(30, min_periods=20).mean(); fwd = h30.shift(-60, freq="D").reindex(h30.index) / h30 - 1
    r = (close / main.shift(1, freq="D").reindex(close.index)).dropna(); r = r[r.index >= "2014-01-01"]; g60 = fwd.reindex(r.index)
    low, high = g60[r <= 1.0].dropna(), g60[r >= 1.5].dropna(); mid = g60[(r > 1.0) & (r < 1.5)].dropna()
    rho = float(pd.concat([r, g60], axis=1).dropna().corr(method="spearman").iloc[0, 1])
    out["observable_consistency"] = {"n_days_r_le_1": int(len(low)), "median_60d_hashrate_growth_r_le_1": float(low.median()) if len(low) else None, "median_r_1_to_1_5": float(mid.median()) if len(mid) else None,
                                     "median_60d_hashrate_growth_r_ge_1_5": float(high.median()) if len(high) else None, "spearman_r_vs_next60d_growth": rho,
                                     "note": "价格只用于检验，不进入成本；r ≤ 1 的日子极少（集中在 2015-01、2018-12、2020-03），统计力很弱"}
    crit_a = wide >= 2; crit_b = flips >= 2; crit_c = not (out["cross_model"]["p5"] >= 0.67 and out["cross_model"]["p95"] <= 1.5)
    crit_d = (len(low) == 0) or not (low.median() < high.median())
    out["criteria"] = {"a_spread_ge_2_on_ge_2_dates": {"met": bool(crit_a), "n_dates": int(wide)}, "b_classification_flips_on_ge_2_dates": {"met": bool(crit_b), "n_dates": int(flips)},
                       "c_cross_model_band_outside_0.67_1.5": {"met": bool(crit_c)}, "d_observable_inconsistent": {"met": bool(crit_d)}}
    out["severe_error"] = bool(crit_a or crit_b or crit_c or crit_d)
    out["verdict"] = ("严重误差：不可作为阈值型策略基础；第十六阶段成本类研究冻结，成本区间只作带不确定性说明的背景" if out["severe_error"] else "未发现严重误差（仍无真实数据校准）")
    return out
