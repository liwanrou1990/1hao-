"""MarketContext：某一标的的多周期预计算 + 任意执行 K 线索引 i 的“此刻可知”上下文构造。

* 对每根执行 K 线 i，决策时刻 = 其收盘时刻；高周期索引由 mtf.align_last_closed 给出（收盘时刻 ≤ 决策时刻）。
* 区域按 (tf, 高周期索引) 缓存：高周期不出新 K 线，区域不变（真实交易员也是在日线收盘后才重画关键位）。
* 4h 事件：当对齐的 4h 索引比上一根执行 K 线前进时，对“刚收盘的 4h K 线”做 classify_bar（相对于上一根 4h 收盘时的区域快照）。
* 环境（regime）：日线 MA50/MA200 + 斜率、rv20 分位、ADX，全部滚动计算。
* extras：资金费率/链上净流入 z 分数（若有）按时间戳对齐，只取 ≤ 决策时刻 的记录。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.indicators import adx, atr, realized_vol, rel_volume
from ..analysis.levels import Zone, build_zones, classify_bar, merge_zones
from ..analysis.mtf import align_last_closed, assert_no_lookahead
from ..analysis.structure import structure_state
from ..analysis.swings import find_swings
from ..schema import tf_seconds
from ..strategies.base import BarContext


def regime_frame(d1: pd.DataFrame, rc: dict) -> pd.DataFrame:
    c = d1["close"]
    ma = c.rolling(int(rc["trend_ma"])).mean(); ma_l = c.rolling(int(rc["trend_ma_long"])).mean()
    slope = ma_l.diff(30)
    rv = realized_vol(c, int(rc["highvol_rv_n"]))
    rv_pct = rv.rolling(365, min_periods=120).rank(pct=True)
    ax = adx(d1, int(rc["range_adx_n"]))
    trend = np.full(len(d1), "range", dtype=object)
    up = (c > ma_l) & (slope > 0); dn = (c < ma_l) & (slope < 0)
    trend[up.to_numpy()] = "up"; trend[dn.to_numpy()] = "down"
    trend[(up & (c > ma) & (ma > ma_l)).to_numpy()] = "strong_up"
    trend[(dn & (c < ma) & (ma < ma_l)).to_numpy()] = "strong_down"
    trend[ma_l.isna().to_numpy()] = "undefined"
    return pd.DataFrame({"ts": d1["ts"], "trend": trend, "highvol": (rv_pct >= float(rc["highvol_pct"])).fillna(False).to_numpy(), "adx": ax.to_numpy(), "rv20": rv.to_numpy()})


class MarketContext:
    def __init__(self, symbol: str, bars: dict[str, pd.DataFrame], exec_tf: str, c: dict, extras: dict[str, pd.DataFrame] | None = None):
        self.symbol, self.exec_tf, self.c = symbol, exec_tf, c
        lv = c["levels"]
        self.bars = {tf: df.reset_index(drop=True) for tf, df in bars.items() if df is not None and not df.empty}
        if exec_tf not in self.bars:
            raise ValueError(f"{symbol} 缺少执行周期 {exec_tf} 数据")
        self.ex = self.bars[exec_tf]
        self.n = len(self.ex)
        self.o, self.h, self.l, self.cl = (self.ex[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.vol = self.ex["volume"].to_numpy(float)
        self.ts = self.ex["ts"]
        self.atr_ex = atr(self.ex, int(lv["atr_n"])).to_numpy(float)
        self.relvol_ex = rel_volume(self.ex["volume"], int(c["filters"]["volume"]["lookback"])).to_numpy(float)
        self.level_tfs = [tf for tf in ("1w", "1d", "4h") if tf in self.bars]
        self.htf: dict[str, dict] = {}
        for tf in set(self.level_tfs + [c["structure"]["tf"]]):
            if tf not in self.bars:
                continue
            df = self.bars[tf]
            idx = align_last_closed(self.ts, exec_tf, df["ts"], tf)
            assert_no_lookahead(self.ts, exec_tf, df["ts"], tf, idx)
            self.htf[tf] = {"df": df, "idx": idx, "atr": atr(df, int(lv["atr_n"])).to_numpy(float), "swings": find_swings(df, int(lv["swing_k"].get(tf, 3))),
                            "relvol": rel_volume(df["volume"], int(c["filters"]["volume"]["lookback"])).to_numpy(float),
                            "o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float)}
        self.struct_tf = c["structure"]["tf"]
        if "1d" in self.bars:
            self.reg = regime_frame(self.bars["1d"], c["regime"])
            self.reg_idx = self.htf["1d"]["idx"] if "1d" in self.htf else align_last_closed(self.ts, exec_tf, self.bars["1d"]["ts"], "1d")
            self._reg_trend = self.reg["trend"].to_numpy(); self._reg_hv = self.reg["highvol"].to_numpy(bool)
            self._reg_adx = self.reg["adx"].to_numpy(float); self._reg_rv = self.reg["rv20"].to_numpy(float)
        else:
            self.reg, self.reg_idx = None, None
        self.extras = extras or {}
        self._zone_cache: dict[tuple[str, int], list[Zone]] = {}
        self._merged_cache: dict[tuple, tuple] = {}
        self._struct_cache: dict[int, object] = {}
        self.bar_seconds = tf_seconds(exec_tf)

    # ---- 缓存访问 ----
    def zones_tf(self, tf: str, hidx: int) -> list[Zone]:
        key = (tf, hidx)
        if key not in self._zone_cache:
            H = self.htf[tf]
            self._zone_cache[key] = build_zones(H["df"], H["swings"], hidx, H["atr"], tf, self.c["levels"]) if hidx >= 0 else []
            if len(self._zone_cache) > 5000:
                self._zone_cache.pop(next(iter(self._zone_cache)))
        return self._zone_cache[key]

    def zones_at(self, i: int) -> tuple[list[Zone], dict[str, list[Zone]]]:
        key = tuple(int(self.htf[tf]["idx"][i]) for tf in self.level_tfs)
        hit = self._merged_cache.get(key)
        if hit is None:
            by_tf = {tf: self.zones_tf(tf, k) for tf, k in zip(self.level_tfs, key)}
            hit = (merge_zones(list(by_tf.values())), by_tf)
            if len(self._merged_cache) > 5000:
                self._merged_cache.pop(next(iter(self._merged_cache)))
            self._merged_cache[key] = hit
        return hit

    def structure_at(self, i: int):
        H = self.htf.get(self.struct_tf)
        if H is None:
            return None
        hidx = int(H["idx"][i])
        if hidx not in self._struct_cache:
            a = H["atr"][hidx] if hidx >= 0 else np.nan
            self._struct_cache[hidx] = structure_state(H["swings"], hidx, a, float(self.c["structure"]["range_atr"]))
        return self._struct_cache[hidx]

    def regime_at(self, i: int) -> dict:
        if self.reg is None or self.reg_idx is None or self.reg_idx[i] < 0:
            return {"trend": "undefined", "highvol": False, "adx": None}
        j = int(self.reg_idx[i]); adx_v = self._reg_adx[j]; rv_v = self._reg_rv[j]
        return {"trend": str(self._reg_trend[j]), "highvol": bool(self._reg_hv[j]), "adx": None if np.isnan(adx_v) else float(adx_v), "rv20": None if np.isnan(rv_v) else float(rv_v)}

    def htf_events_at(self, i: int, tf: str, zone_tfs: list[str]) -> list[dict]:
        """若 tf 周期在本执行 K 线有新收盘 K 线，返回它对 zone_tfs 区域（以上一根 tf K 线收盘时的区域快照）的事件。"""
        H = self.htf.get(tf)
        if H is None or i == 0:
            return []
        hidx, prev = int(H["idx"][i]), int(H["idx"][i - 1])
        if hidx < 0 or hidx == prev:
            return []
        out = []
        buf = float(self.c["levels"]["break_buffer_atr"]) * (H["atr"][hidx] if np.isfinite(H["atr"][hidx]) else np.nan)
        if not np.isfinite(buf):
            return []
        zl = [z for ztf in zone_tfs if ztf in self.htf for z in self.zones_tf(ztf, int(self.htf[ztf]["idx"][i - 1]))]
        pc = H["c"][hidx - 1] if hidx >= 1 else H["o"][hidx]
        for z in merge_zones([zl]):
            ev = classify_bar(H["o"][hidx], H["h"][hidx], H["l"][hidx], H["c"][hidx], pc, z, buf)
            if ev != "none":
                out.append({"zone": z, "event": ev, "rel_vol": float(H["relvol"][hidx]) if np.isfinite(H["relvol"][hidx]) else None, "ts": H["df"]["ts"].iloc[hidx], "tf": tf})
        return out

    def extras_at(self, i: int) -> dict:
        out = {"bar_seconds": self.bar_seconds}
        dec = self.ts.iloc[i] + pd.Timedelta(seconds=self.bar_seconds)
        for name, df in self.extras.items():
            if df is None or df.empty:
                continue
            col = "available_at" if "available_at" in df else "ts"
            j = df[col].searchsorted(dec, side="right") - 1
            if j >= 0:
                out[name] = float(df["value"].iloc[j])
        return out

    def ready(self, i: int) -> bool:
        return i >= 1 and np.isfinite(self.atr_ex[i]) and self.atr_ex[i] > 0 and all(self.htf[tf]["idx"][i] >= 0 for tf in self.level_tfs)

    def bar_context(self, i: int, htf_event_tf: str = "4h", htf_zone_tfs: list[str] | None = None) -> BarContext:
        zones, by_tf = self.zones_at(i)
        st = self.structure_at(i)
        a = float(self.atr_ex[i])
        return BarContext(self.symbol, i, self.ts.iloc[i], float(self.o[i]), float(self.h[i]), float(self.l[i]), float(self.cl[i]), float(self.cl[i - 1]),
                          float(self.vol[i]), float(self.relvol_ex[i]) if np.isfinite(self.relvol_ex[i]) else float("nan"), a, a / float(self.cl[i]),
                          zones, by_tf, st.trend if st else "undefined", self.regime_at(i),
                          self.htf_events_at(i, htf_event_tf, htf_zone_tfs or ["1w", "1d"]) if htf_event_tf in self.htf else [], self.extras_at(i))

    def structure_dict(self, i: int) -> dict:
        st = self.structure_at(i)
        return st.to_dict() if st else {"trend": "undefined"}
