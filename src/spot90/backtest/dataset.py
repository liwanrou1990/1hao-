"""把 K 线 → 特征 + 标签的训练数据集（BTC、ETH 合并，symbol 作为特征）。"""
from __future__ import annotations

import pandas as pd

from ..config import cfg
from ..data.store import BarStore
from ..features import FEATURES, build_features
from ..labels import build_labels
from ..models.common import add_dd_flags


def load_bars(store: BarStore | None = None, symbols: list[str] | None = None) -> dict[str, dict[str, pd.DataFrame]]:
    store = store or BarStore()
    symbols = symbols or cfg()["symbols"]
    return {s: {"1d": store.load(s, "1d"), "4h": store.load(s, "4h")} for s in symbols}


def build_dataset(bars: dict[str, dict[str, pd.DataFrame]]) -> dict:
    """返回 {X: 特征(含 ts,symbol,close,FEATURES), wide: A 标签, long: B 标签, XB: B 行对应特征, feats_all: 全部日期的特征(含无标签的最新日)}"""
    market = bars.get("BTCUSDT", {}).get("1d")
    Xs, Ws, Ls = [], [], []
    for sym, b in bars.items():
        if b["1d"].empty:
            continue
        f = build_features(b["1d"], b.get("4h"), market)
        wide, long = build_labels(b["1d"])
        Xs.append(f); Ws.append(wide); Ls.append(long)
    feats_all = pd.concat(Xs, ignore_index=True)
    wide = pd.concat(Ws, ignore_index=True)
    long = pd.concat(Ls, ignore_index=True)
    wide, long = add_dd_flags(wide, long)
    X = wide[["ts", "symbol"]].merge(feats_all, on=["ts", "symbol"], how="left")
    X.index = wide.index
    XB = long[["ts", "symbol", "d_pct"]].merge(feats_all, on=["ts", "symbol"], how="left")
    XB.index = long.index
    return {"X": X, "wide": wide, "long": long, "XB": XB, "feats_all": feats_all}
