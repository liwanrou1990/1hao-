"""明确标注的模拟数据源（SIMULATED）。

用途：在没有 API 密钥/网络权限时让整条流水线可运行、可测试。
约束：
  * 所有输出 source="SIMULATED"、quality 含 SIMULATED 位；任何报表/接口都会显示该标记。
  * 只在 America/New_York 交易日生成原生K线，周末与休市日拒绝生成。
  * 市场环境（regime）按日分配并可查询，供 ML 保留“不同市场环境”测试集。
  * 代币venue 由原生价格加基差噪声派生，带更宽价差，与原生价格分开存放，不混同。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..calendar_utils import is_trading_day, regular_minute_index, trading_days
from .base import ProbeResult
from .schema import DataSourceTag, QualityFlag, Venue, normalize_bars

REGIMES = ["trend_up", "trend_down", "mean_revert", "high_vol", "quiet"]

# 以 2026-10 前后公开大致价位为参考的起始价（仅用于模拟量级，不代表真实报价）
BASE_PRICES = {
    "NVDA": 180.0, "SPY": 660.0, "SPCX": 155.0, "MU": 190.0, "QQQ": 600.0,
    "TSLA": 430.0, "MSFT": 510.0, "SNDK": 150.0, "INTC": 37.0, "META": 720.0,
}
BASE_DAILY_VOL = {  # 年化波动率近似
    "NVDA": 0.45, "SPY": 0.15, "SPCX": 0.60, "MU": 0.55, "QQQ": 0.20,
    "TSLA": 0.60, "MSFT": 0.25, "SNDK": 0.65, "INTC": 0.45, "META": 0.40,
}
# 加密货币（24 小时；价格为量级参考，不代表真实报价）
CRYPTO_BASE_PRICES = {"BTC": 110000.0, "ETH": 3900.0, "SOL": 190.0, "HYPE": 45.0}
CRYPTO_ANN_VOL = {"BTC": 0.50, "ETH": 0.65, "SOL": 0.90, "HYPE": 1.10}
CRYPTO_ADV_USD = {"BTC": 1.5e9, "ETH": 8.0e8, "SOL": 4.0e8, "HYPE": 1.0e8}   # 单一交易所量级的日成交额（钱包内可触达深度未知）
BASE_ADV_SHARES = {  # 日均成交股数（量级）
    "NVDA": 2.0e8, "SPY": 7.0e7, "SPCX": 5.0e6, "MU": 2.5e7, "QQQ": 4.0e7,
    "TSLA": 9.0e7, "MSFT": 2.0e7, "SNDK": 6.0e6, "INTC": 8.0e7, "META": 1.2e7,
}


def _seed(symbol: str, day: date, salt: str = "") -> int:
    h = hashlib.sha256(f"{symbol}|{day.isoformat()}|{salt}".encode()).hexdigest()
    return int(h[:8], 16)


def regime_for_day(symbol: str, day: date) -> str:
    """按“周”分配市场环境，保证同一环境持续若干天，便于分环境测试。"""
    iso = day.isocalendar()
    h = hashlib.sha256(f"{symbol}|{iso.year}|{iso.week}".encode()).hexdigest()
    return REGIMES[int(h[:4], 16) % len(REGIMES)]


@dataclass
class SimParams:
    gap_prob: float = 0.004       # 随机缺失K线概率（用于质量标记测试）
    outlier_prob: float = 0.0005
    token_basis_bps: float = 15.0  # 代币相对原生价格的基差噪声（bps 标准差）
    token_extra_spread_bps: float = 20.0


class SimulatedSource:
    name = "simulated"
    is_simulated = True

    def __init__(self, params: SimParams | None = None, start_price_override: dict[str, float] | None = None):
        self.params = params or SimParams()
        self._price_cache: dict[tuple[str, date], float] = {}
        self.start_price_override = start_price_override or {}

    # ----- 价格路径：按交易日链式推进，保证跨日连续 -----
    def _open_price(self, symbol: str, day: date, anchor: date) -> float:
        """从 anchor 日起链式生成日度收盘，得到 day 的开盘参考价。确定性。"""
        key = (symbol, day)
        if key in self._price_cache:
            return self._price_cache[key]
        price = self.start_price_override.get(symbol, BASE_PRICES.get(symbol, 100.0))
        for d in trading_days(anchor, day):
            if d == day:
                break
            rng = np.random.default_rng(_seed(symbol, d, "daily"))
            vol_d = BASE_DAILY_VOL.get(symbol, 0.4) / np.sqrt(252)
            reg = regime_for_day(symbol, d)
            drift = {"trend_up": 0.4, "trend_down": -0.4, "mean_revert": 0.0, "high_vol": 0.0, "quiet": 0.0}[reg] * vol_d
            mult = {"high_vol": 1.8, "quiet": 0.5}.get(reg, 1.0)
            price *= float(np.exp(drift + rng.normal(0, vol_d * mult)))
        self._price_cache[key] = price
        return price

    def fetch_minute_bars(self, symbol: str, day: date, venue: str = Venue.NATIVE, anchor: date | None = None) -> pd.DataFrame:
        if not is_trading_day(day):
            raise ValueError(f"{day} 不是美股交易日（America/New_York）：拒绝生成原生股票K线")
        anchor = anchor or date(day.year - 1, 1, 2)
        idx = regular_minute_index(day)
        n = len(idx)
        rng = np.random.default_rng(_seed(symbol, day, "native"))   # 原生路径种子与 venue 无关：代币价格派生自同一原生路径
        reg = regime_for_day(symbol, day)
        p0 = self._open_price(symbol, day, anchor)
        ann_vol = BASE_DAILY_VOL.get(symbol, 0.4) * {"high_vol": 1.8, "quiet": 0.5}.get(reg, 1.0)
        minute_vol = ann_vol / np.sqrt(252 * 390)
        # 日内 U 型波动与成交量
        t = np.linspace(0, 1, n)
        u_shape = 0.6 + 1.6 * ((t - 0.5) ** 2) * 4 * 0.5 + 0.4 * np.exp(-((t - 0.0) ** 2) / 0.01)
        drift = {"trend_up": 1.0, "trend_down": -1.0}.get(reg, 0.0) * minute_vol * 0.02
        eps = rng.standard_t(df=4, size=n) / np.sqrt(2)  # 厚尾
        r = drift + minute_vol * u_shape * eps
        if reg == "mean_revert":
            # OU 回归：对累计偏离做拉回
            level = np.zeros(n)
            for i in range(1, n):
                level[i] = level[i - 1] * 0.97 + r[i]
            logp = np.log(p0) + level
        else:
            logp = np.log(p0) + np.cumsum(r)
        close = np.exp(logp)
        open_ = np.concatenate([[p0], close[:-1]])
        intra = np.abs(rng.normal(0, minute_vol * u_shape * 0.8, size=n)) * close
        high = np.maximum(open_, close) + intra
        low = np.minimum(open_, close) - intra * rng.uniform(0.3, 1.0, size=n)
        adv = BASE_ADV_SHARES.get(symbol, 1e7)
        vol_shape = 0.5 + 2.5 * ((t - 0.5) ** 2) * 4 * 0.5 + 1.2 * np.exp(-(t ** 2) / 0.005) + 0.8 * np.exp(-((t - 1) ** 2) / 0.005)
        volume = np.maximum(1, (adv / n) * vol_shape * rng.lognormal(0, 0.5, size=n)).round()
        df = pd.DataFrame({"ts": idx, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
        df["amount"] = df["volume"] * (df["open"] + df["close"]) / 2
        df["txn"] = (volume / 120).round()
        # 注入少量缺口/异常用于质量检测演示
        if self.params.gap_prob > 0 and n > 10:
            drop = rng.random(n) < self.params.gap_prob
            drop[0] = drop[-1] = False
            df = df[~drop]
        q = int(QualityFlag.SIMULATED)
        if venue == Venue.TOKEN:
            # 代币成交价 = 原生价 × (1 + 基差噪声)；成交量远小于原生（基差用独立种子）
            trng = np.random.default_rng(_seed(symbol, day, "token"))
            basis = trng.normal(0, self.params.token_basis_bps / 1e4, size=len(df))
            for c in ["open", "high", "low", "close"]:
                df[c] = df[c] * (1 + basis)
            df["volume"] = (df["volume"] * 0.002).round().clip(lower=0)
            df["amount"] = df["volume"] * df["close"]
            df["txn"] = (df["txn"] * 0.01).round()
            source = str(DataSourceTag.SIMULATED) + ":token"
        else:
            source = str(DataSourceTag.SIMULATED) + ":native"
        bars = normalize_bars(df, symbol=symbol, venue=venue, source=source,
                              chain="sim" if venue == Venue.TOKEN else None,
                              contract="SIMULATED" if venue == Venue.TOKEN else None, quality_extra=q)
        bars.attrs["regime"] = reg
        return bars

    # ----- 加密货币：24 小时（America/New_York 自然日），任意日期 -----
    def _crypto_open_price(self, symbol: str, day: date, anchor: date) -> float:
        key = (symbol, day, "crypto")
        if key in self._price_cache:
            return self._price_cache[key]
        price = self.start_price_override.get(symbol, CRYPTO_BASE_PRICES.get(symbol, 100.0))
        d = anchor
        while d < day:
            rng = np.random.default_rng(_seed(symbol, d, "crypto_daily"))
            vol_d = CRYPTO_ANN_VOL.get(symbol, 0.8) / np.sqrt(365)
            reg = regime_for_day(symbol, d)
            drift = {"trend_up": 0.3, "trend_down": -0.3}.get(reg, 0.0) * vol_d
            mult = {"high_vol": 1.8, "quiet": 0.5}.get(reg, 1.0)
            price *= float(np.exp(drift + rng.normal(0, vol_d * mult)))
            d = d + pd.Timedelta(days=1).to_pytimedelta()
        self._price_cache[key] = price
        return price

    def fetch_crypto_day(self, symbol: str, day: date, anchor: date | None = None) -> pd.DataFrame:
        """生成某 America/New_York 自然日的 1440 根 1 分钟加密货币 K 线（SIMULATED）。"""
        from .crypto import crypto_day_bounds_utc

        anchor = anchor or (day - pd.Timedelta(days=180).to_pytimedelta())
        start, end = crypto_day_bounds_utc(day)
        idx = pd.date_range(start, end - pd.Timedelta(minutes=1), freq="1min", tz="UTC")
        n = len(idx)
        rng = np.random.default_rng(_seed(symbol, day, "crypto"))
        reg = regime_for_day(symbol, day)
        p0 = self._crypto_open_price(symbol, day, anchor)
        ann_vol = CRYPTO_ANN_VOL.get(symbol, 0.8) * {"high_vol": 1.8, "quiet": 0.5}.get(reg, 1.0)
        minute_vol = ann_vol / np.sqrt(365 * 1440)
        t = np.linspace(0, 1, n)
        # 24 小时活跃度：美股时段（NY 09:30–16:00 ≈ t 0.40–0.67）与亚洲早盘略高
        act = 0.7 + 0.8 * np.exp(-((t - 0.55) ** 2) / 0.02) + 0.3 * np.exp(-((t - 0.1) ** 2) / 0.02)
        drift = {"trend_up": 1.0, "trend_down": -1.0}.get(reg, 0.0) * minute_vol * 0.02
        eps = rng.standard_t(df=3, size=n) / np.sqrt(3)
        r = drift + minute_vol * act * eps
        if reg == "mean_revert":
            level = np.zeros(n)
            for i in range(1, n):
                level[i] = level[i - 1] * 0.985 + r[i]
            logp = np.log(p0) + level
        else:
            logp = np.log(p0) + np.cumsum(r)
        close = np.exp(logp)
        open_ = np.concatenate([[p0], close[:-1]])
        intra = np.abs(rng.normal(0, minute_vol * act * 0.8, size=n)) * close
        high = np.maximum(open_, close) + intra
        low = np.minimum(open_, close) - intra * rng.uniform(0.3, 1.0, size=n)
        adv = CRYPTO_ADV_USD.get(symbol, 1e8)
        amount = np.maximum(1.0, (adv / n) * act * rng.lognormal(0, 0.6, size=n))
        df = pd.DataFrame({"ts": idx, "open": open_, "high": high, "low": low, "close": close})
        df["amount"] = amount
        df["volume"] = amount / close
        df["txn"] = (amount / 2000).round()
        if self.params.gap_prob > 0:
            drop = rng.random(n) < self.params.gap_prob * 0.5
            drop[0] = drop[-1] = False
            df = df[~drop]
        bars = normalize_bars(df, symbol=symbol, venue=Venue.TOKEN, source=str(DataSourceTag.SIMULATED) + ":crypto",
                              chain="sim", contract="SIMULATED", quality_extra=int(QualityFlag.SIMULATED))
        bars.attrs["regime"] = reg
        bars.attrs["asset_class"] = "crypto"
        return bars

    def probe(self) -> ProbeResult:
        return ProbeResult(source=self.name, ok=True, has_credentials=False,
                           detail="模拟数据源，仅用于流水线验证，不代表真实行情",
                           history_span="任意交易日（确定性生成）", rate_limit="无", cost_note="无")
