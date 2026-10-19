"""对某日逐bar给出 AI 信号：概率、期望收益、决策与中文解释；无优势 → 不交易。"""
from __future__ import annotations

import pandas as pd

from .features import build_features
from .train import HorizonModel, explain_prediction

FEATURE_CN = {
    "ret_1": "1分钟收益", "ret_3": "3分钟收益", "ret_5": "5分钟收益", "ret_15": "15分钟收益", "ret_30": "30分钟收益",
    "atr_rel": "ATR/价格", "rv_30": "30分钟实现波动", "rv_ratio": "短/长波动比", "ema9_dist": "距EMA9(ATR倍)",
    "ema21_dist": "距EMA21(ATR倍)", "ema50_dist": "距EMA50(ATR倍)", "ema_fast_slow": "EMA快慢差", "macd_hist": "MACD柱",
    "rsi14": "RSI14", "bb_pos": "布林带位置", "bb_width": "布林带宽", "vwap_dist": "距VWAP(ATR倍)", "rel_vol": "相对成交量",
    "vol_z": "成交量z分", "hi_dist": "距30根高点", "lo_dist": "距30根低点", "range_pos": "K线内位置",
    "minute_of_day": "日内时间", "bars_remaining": "剩余K线", "ret_from_open": "开盘以来收益", "bias": "基准",
}


def ai_signals_for_day(bars: pd.DataFrame, models: dict[int, HorizonModel], explain_top: int = 4) -> pd.DataFrame:
    bars = bars.reset_index(drop=True)
    X = build_features(bars)
    ok = X.notna().all(axis=1)
    rows = []
    for h, m in sorted(models.items()):
        p = pd.Series(float("nan"), index=bars.index)
        ev = pd.Series(float("nan"), index=bars.index)
        if ok.any():
            p[ok] = m.predict_proba(X[ok])
            ev[ok] = m.predict_ev(X[ok])
        dec = pd.Series(0, index=bars.index)
        if m.threshold is not None:
            dec[ok] = m.decide(p[ok].to_numpy(), ev[ok].to_numpy())
        for i in bars.index:
            reason = _reason(m, p[i], ev[i], ok[i])
            expl = []
            if dec[i] == 1 and explain_top:
                expl = [{"feature": FEATURE_CN.get(e["feature"], e["feature"]), "contribution": e["contribution"]}
                        for e in explain_prediction(m, X.iloc[[i]], explain_top)]
            rows.append({"idx": int(i), "ts": bars["ts"].iloc[i].isoformat(), "ts_ny": bars["ts_ny"].iloc[i], "horizon": h,
                         "p_up": None if pd.isna(p[i]) else float(p[i]), "ev": None if pd.isna(ev[i]) else float(ev[i]),
                         "decision": int(dec[i]), "threshold": m.threshold, "reason": reason, "explanation": expl})
    return pd.DataFrame(rows)


def _reason(m: HorizonModel, p: float, ev: float, feats_ok: bool) -> str:
    if not feats_ok:
        return "特征窗口不足（开盘初期指标尚未形成），不交易"
    if m.threshold is None:
        return f"{m.horizon}分钟模型在验证集上不存在正期望阈值：无统计优势，不交易"
    if pd.isna(p):
        return "无预测"
    if p < m.threshold:
        return f"上涨概率 {p:.1%} 低于阈值 {m.threshold:.1%}，不交易"
    if ev <= 0:
        return f"概率 {p:.1%} 达标但扣费后期望收益 {ev:+.3%} ≤ 0，不交易"
    return f"概率 {p:.1%} ≥ 阈值 {m.threshold:.1%} 且扣费后期望收益 {ev:+.3%} > 0：做多 {m.horizon} 分钟"
