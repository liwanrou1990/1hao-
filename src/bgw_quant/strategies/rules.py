"""9 类规则策略：VWAP、EMA 趋势、MACD、RSI、布林带、ATR 通道、突破、均值回归、成交量过滤。

所有策略：日内多头，目标仓位 0/1；收盘前 `flat_before_close` 根K线强制空仓；
以上一根（已完成）K线的指标决定本根收盘时的目标仓位时仍属因果——这里直接使用“当根已完成K线”的指标，
由复盘引擎负责在下一根K线开盘执行，因此不存在用未完成K线或未来价格的情况。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind

FLAT_BEFORE_CLOSE = 5   # 收盘前 5 分钟不再持仓/开仓


def _session_guard(df: pd.DataFrame, pos: pd.Series, flat_before_close: int = FLAT_BEFORE_CLOSE) -> pd.Series:
    rem = ind.bars_remaining_in_session(df)
    pos = pos.where(rem > flat_before_close, 0)
    return pos.fillna(0).astype(int)


def _hold_state(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """由进场/出场布尔序列生成持仓状态（因果，逐根传递）。"""
    e = entry.fillna(False).to_numpy(dtype=bool)
    x = exit_.fillna(False).to_numpy(dtype=bool)
    out = np.zeros(len(e), dtype=int)
    state = 0
    for i in range(len(e)):
        if state == 0 and e[i]:
            state = 1
        elif state == 1 and x[i]:
            state = 0
        out[i] = state
    return pd.Series(out, index=entry.index)


def _reset_by_day(df: pd.DataFrame, pos: pd.Series) -> pd.Series:
    """跨日不持仓：每日第一根强制 0（配合 _session_guard 收盘前空仓）。"""
    first = ind.session_minute_index(df) == 0
    return pos.where(~first, 0)


@dataclass
class RuleStrategy:
    name: str
    params: dict = field(default_factory=dict)
    description: str = ""

    def compute(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def explain(self, df: pd.DataFrame, i: int) -> str:
        return f"{self.name} 参数 {self.params}"


class VwapStrategy(RuleStrategy):
    """价格上穿 VWAP 且位于 VWAP 上方做多；下穿 VWAP 平仓。"""
    def __init__(self, band_bps: float = 5.0):
        super().__init__("VWAP", {"band_bps": band_bps}, "价格上穿日内 VWAP 做多，跌破 VWAP 平仓")

    def compute(self, df):
        v = ind.session_vwap(df)
        above = df["close"] > v * (1 + self.params["band_bps"] / 1e4)
        below = df["close"] < v * (1 - self.params["band_bps"] / 1e4)
        entry = above & ~above.shift(1, fill_value=False)
        pos = _hold_state(entry, below)
        return _session_guard(df, _reset_by_day(df, pos))


class EmaTrendStrategy(RuleStrategy):
    def __init__(self, fast: int = 9, slow: int = 21):
        super().__init__("EMA趋势", {"fast": fast, "slow": slow}, "快 EMA 在慢 EMA 上方且价格在快 EMA 上方做多")

    def compute(self, df):
        f, s = ind.ema(df["close"], self.params["fast"]), ind.ema(df["close"], self.params["slow"])
        pos = ((f > s) & (df["close"] > f)).astype(int)
        return _session_guard(df, _reset_by_day(df, pos))


class MacdStrategy(RuleStrategy):
    def __init__(self, fast=12, slow=26, signal=9):
        super().__init__("MACD", {"fast": fast, "slow": slow, "signal": signal}, "MACD 金叉做多，死叉平仓")

    def compute(self, df):
        m = ind.macd(df["close"], **self.params)
        pos = (m["macd"] > m["signal"]).astype(int)
        return _session_guard(df, _reset_by_day(df, pos))


class RsiStrategy(RuleStrategy):
    def __init__(self, n=14, lower=30, upper=55):
        super().__init__("RSI", {"n": n, "lower": lower, "upper": upper}, "RSI 低于下阈值反弹做多，回到上阈值平仓")

    def compute(self, df):
        r = ind.rsi(df["close"], self.params["n"])
        entry = (r > self.params["lower"]) & (r.shift(1) <= self.params["lower"])
        exit_ = r >= self.params["upper"]
        pos = _hold_state(entry, exit_)
        return _session_guard(df, _reset_by_day(df, pos))


class BollingerStrategy(RuleStrategy):
    def __init__(self, n=20, k=2.0):
        super().__init__("布林带", {"n": n, "k": k}, "触及下轨后收回做多，回到中轨平仓（均值回归型）")

    def compute(self, df):
        b = ind.bollinger(df["close"], self.params["n"], self.params["k"])
        entry = (df["close"] > b["lower"]) & (df["close"].shift(1) <= b["lower"].shift(1))
        exit_ = df["close"] >= b["mid"]
        pos = _hold_state(entry, exit_)
        return _session_guard(df, _reset_by_day(df, pos))


class AtrChannelStrategy(RuleStrategy):
    def __init__(self, n=14, k=1.5, ema_n=20):
        super().__init__("ATR通道", {"n": n, "k": k, "ema_n": ema_n}, "收盘突破 EMA+k·ATR 做多，跌回 EMA 平仓")

    def compute(self, df):
        a = ind.atr(df, self.params["n"])
        e = ind.ema(df["close"], self.params["ema_n"])
        entry = df["close"] > e + self.params["k"] * a
        exit_ = df["close"] < e
        pos = _hold_state(entry & ~entry.shift(1, fill_value=False), exit_)
        return _session_guard(df, _reset_by_day(df, pos))


class BreakoutStrategy(RuleStrategy):
    def __init__(self, n=30, stop_atr=1.0):
        super().__init__("突破", {"n": n, "stop_atr": stop_atr}, "收盘创 n 根新高做多，跌破 n 根低点或 ATR 止损平仓")

    def compute(self, df):
        hi, lo = ind.rolling_high(df["high"], self.params["n"]), ind.rolling_low(df["low"], self.params["n"])
        a = ind.atr(df, 14)
        entry = df["close"] > hi
        exit_ = (df["close"] < lo) | (df["close"] < hi - self.params["stop_atr"] * a)
        pos = _hold_state(entry, exit_)
        return _session_guard(df, _reset_by_day(df, pos))


class MeanReversionStrategy(RuleStrategy):
    def __init__(self, n=30, z_entry=-2.0, z_exit=0.0):
        super().__init__("均值回归", {"n": n, "z_entry": z_entry, "z_exit": z_exit}, "z 分数低于阈值做多，回到 0 平仓")

    def compute(self, df):
        m = ind.sma(df["close"], self.params["n"])
        sd = df["close"].rolling(self.params["n"], min_periods=self.params["n"]).std(ddof=0)
        z = (df["close"] - m) / sd.replace(0, np.nan)
        entry = z < self.params["z_entry"]
        exit_ = z > self.params["z_exit"]
        pos = _hold_state(entry, exit_)
        return _session_guard(df, _reset_by_day(df, pos))


class VolumeFilteredTrendStrategy(RuleStrategy):
    def __init__(self, fast=9, slow=21, rel_vol=1.3, vol_n=20):
        super().__init__("成交量过滤", {"fast": fast, "slow": slow, "rel_vol": rel_vol, "vol_n": vol_n},
                         "EMA 趋势 + 相对成交量放大确认才进场；无成交量字段时不交易")

    def compute(self, df):
        if df["volume"].isna().all():
            return pd.Series(0, index=df.index, dtype=int)
        f, s = ind.ema(df["close"], self.params["fast"]), ind.ema(df["close"], self.params["slow"])
        rv = ind.rel_volume(df["volume"], self.params["vol_n"])
        entry = (f > s) & (df["close"] > f) & (rv > self.params["rel_vol"])
        exit_ = f < s
        pos = _hold_state(entry, exit_)
        return _session_guard(df, _reset_by_day(df, pos))


RULE_STRATEGIES: dict[str, type] = {
    "vwap": VwapStrategy, "ema_trend": EmaTrendStrategy, "macd": MacdStrategy, "rsi": RsiStrategy,
    "bollinger": BollingerStrategy, "atr_channel": AtrChannelStrategy, "breakout": BreakoutStrategy,
    "mean_reversion": MeanReversionStrategy, "volume_filter": VolumeFilteredTrendStrategy,
}


def make_strategy(key: str, **params) -> RuleStrategy:
    return RULE_STRATEGIES[key](**params)


def all_strategies() -> list[RuleStrategy]:
    return [cls() for cls in RULE_STRATEGIES.values()]
