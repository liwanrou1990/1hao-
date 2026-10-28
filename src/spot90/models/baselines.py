"""基准模型（必须先于复杂模型建立；复杂模型若不能在样本外击败它们，就不采用）。

1. BaseRateModel：训练窗内的历史频率/均值（无条件）。
2. VolBootstrapModel：基于历史波动率的风险模型 —— 对训练窗的“日 (收益, 开盘/最低相对前收) 三元组”做块自助重采样生成
   W+H 天路径，并按“当前 rv20 / 训练窗 rv20 中位数”缩放对数收益，得到成交概率、收益分布与回撤分布。
   注意它用到最低价是为了模拟路径内的触价，而非利用未来真实最低价。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import cfg
from ..labels import cost_fractions
from .common import A_COLS, B_COLS, QUANTILES


class BaseRateModel:
    name = "baserate"

    def fit(self, wide: pd.DataFrame, long: pd.DataFrame) -> "BaseRateModel":
        self.A = {"p_profit": wide["profit_A"].mean(), "ret_mean": wide["ret_A"].mean(), "p_dd": wide["dd_A_flag"].mean()}
        for q in QUANTILES:
            self.A[f"ret_q{int(q*100):02d}"] = wide["ret_A"].quantile(q)
        g = long.groupby("d_pct")
        self.B = pd.DataFrame({"p_fill": g["filled"].mean(), "p_profit": g["profit_B"].mean(), "ret_mean": g["ret_B"].mean(),
                               "ret_q50": g["ret_B"].median(), "p_dd": g["dd_B_flag"].mean()})
        return self

    def predict(self, X: pd.DataFrame, XB: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        pa = pd.DataFrame({k: self.A[k] for k in A_COLS}, index=X.index)
        pb = XB[["d_pct"]].merge(self.B, left_on="d_pct", right_index=True, how="left")[B_COLS]
        pb.index = XB.index
        return pa, pb


class VolBootstrapModel:
    name = "volboot"

    def __init__(self, n_paths: int = 1500, block: int = 10, seed: int = 42):
        self.n_paths, self.block, self.seed = n_paths, block, seed

    def fit(self, bars_by_symbol: dict[str, pd.DataFrame], train_end: pd.Timestamp) -> "VolBootstrapModel":
        c = cfg()
        self.H, self.W = int(c["horizon_days"]), int(c["wait_days"])
        self.grid = [float(d) for d in c["limit_order"]["discount_grid_pct"]]
        self.thr = float(c["thresholds"]["drawdown_threshold"])
        self.metric = str(c["thresholds"].get("drawdown_metric", "mae"))
        self.banks: dict[str, dict] = {}
        rng = np.random.default_rng(self.seed)
        L = self.W + self.H
        for sym, b in bars_by_symbol.items():
            b = b[b["ts"] <= train_end].sort_values("ts")
            pc = b["close"].shift(1)
            r = np.log(b["close"] / pc).to_numpy()[1:]
            lo = np.log(b["low"] / pc).to_numpy()[1:]
            op = np.log(b["open"] / pc).to_numpy()[1:]
            rv20 = pd.Series(r).rolling(20).std().to_numpy() * np.sqrt(365)
            med_rv = float(np.nanmedian(rv20))
            n = len(r)
            n_blocks = int(np.ceil(L / self.block))
            starts = rng.integers(0, n - self.block, size=(self.n_paths, n_blocks))
            idx = (starts[:, :, None] + np.arange(self.block)[None, None, :]).reshape(self.n_paths, -1)[:, :L]
            self.banks[sym] = {"r": r[idx], "lo": lo[idx], "op": op[idx], "med_rv": med_rv}
        return self

    def _simulate(self, sym: str, scale: float) -> tuple[dict, pd.DataFrame]:
        bank = self.banks[sym]
        s = float(np.clip(scale, 0.4, 3.0))
        r, lo, op = bank["r"] * s, bank["lo"] * s, bank["op"] * s
        cost = cost_fractions()
        H, W = self.H, self.W
        logc = np.cumsum(r, axis=1)                          # 相对决策日收盘(=1) 的对数价格，列 t = 第 i+1+t 天
        prev = np.concatenate([np.zeros((r.shape[0], 1)), logc[:, :-1]], axis=1)
        close, low, open_ = np.exp(logc), np.exp(prev + lo), np.exp(prev + op)
        low = np.minimum(low, close)
        # ---- A：第 i+1 天开盘买入，持有 H 天 ----
        entry = open_[:, 0]
        cw, lw = close[:, :H], low[:, :H]
        ret = cw[:, -1] * (1 - cost["out"]) / (entry * (1 + cost["market_in"])) - 1
        peak_prev = np.maximum(np.concatenate([entry[:, None], np.maximum.accumulate(cw, axis=1)[:, :-1]], axis=1), entry[:, None])
        mdd = (lw / peak_prev - 1).min(axis=1); mae = lw.min(axis=1) / entry - 1
        dd = mae if self.metric == "mae" else mdd
        A = {"p_profit": float((ret > 0).mean()), "ret_mean": float(ret.mean()), "p_dd": float((dd < -self.thr).mean()),
             "mdd_mean": float(mdd.mean()), "mae_mean": float(mae.mean())}
        for q in QUANTILES:
            A[f"ret_q{int(q*100):02d}"] = float(np.quantile(ret, q))
        # ---- B：限价 ----
        rows = []
        lw_wait = low[:, :W]
        for d in self.grid:
            limit = 1 - d / 100
            mask = lw_wait <= limit
            filled = mask.any(axis=1)
            if filled.sum() < 20:
                rows.append({"d_pct": d, "p_fill": float(filled.mean()), "p_profit": np.nan, "ret_mean": np.nan, "ret_q50": np.nan, "p_dd": np.nan})
                continue
            first = mask.argmax(axis=1)[filled]
            fill_px = np.minimum(limit, open_[filled, first])
            J = first[:, None] + np.arange(H)[None, :]
            cf, lf = close[filled][np.arange(filled.sum())[:, None], J], low[filled][np.arange(filled.sum())[:, None], J]
            retb = cf[:, -1] * (1 - cost["out"]) / (fill_px * (1 + cost["limit_in"])) - 1
            pk = np.maximum(np.concatenate([fill_px[:, None], np.maximum.accumulate(cf, axis=1)[:, :-1]], axis=1), fill_px[:, None])
            mddb = (lf / pk - 1).min(axis=1); maeb = lf.min(axis=1) / fill_px - 1
            ddb = maeb if self.metric == "mae" else mddb
            rows.append({"d_pct": d, "p_fill": float(filled.mean()), "p_profit": float((retb > 0).mean()), "ret_mean": float(retb.mean()),
                         "ret_q50": float(np.median(retb)), "p_dd": float((ddb < -self.thr).mean())})
        return A, pd.DataFrame(rows)

    def predict(self, X: pd.DataFrame, XB: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """X 需含 symbol, rv20；XB 需含 symbol, ts, d_pct。按 (symbol, rv20 分桶) 缓存以提速。"""
        pa = pd.DataFrame(index=X.index, columns=A_COLS, dtype=float)
        cache: dict = {}
        bres = {}
        for i, row in X.iterrows():
            sym = row["symbol"]
            scale = row["rv20"] / self.banks[sym]["med_rv"] if np.isfinite(row["rv20"]) else 1.0
            key = (sym, round(float(scale), 2))
            if key not in cache:
                cache[key] = self._simulate(sym, scale)
            A, B = cache[key]
            for k in A_COLS:
                pa.at[i, k] = A[k]
            bres[(sym, row["ts"])] = B
        parts = []
        for (sym, ts), B in bres.items():
            sub = XB[(XB["symbol"] == sym) & (XB["ts"] == ts)]
            m = sub[["d_pct"]].merge(B, on="d_pct", how="left")
            m.index = sub.index
            parts.append(m[B_COLS])
        pb = pd.concat(parts).reindex(XB.index) if parts else pd.DataFrame(index=XB.index, columns=B_COLS, dtype=float)
        return pa, pb
