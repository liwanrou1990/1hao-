"""永续合约前瞻数据采集（第三阶段）：本环境唯一可达的衍生品数据源是 CoinGecko 的 derivatives 接口（只给“当前”值，无历史）。
每次运行把 Binance 永续 BTCUSDT / ETHUSDT 的 价格、指数价、基差、资金费率、未平仓量、24h 成交量 追加到本地 parquet（带 collected_at），
作为**前瞻性**数据逐步积累；与历史回测严格分开，不回填历史。"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pandas as pd

from ..config import own_data_dir

URL = "https://api.coingecko.com/api/v3/derivatives/exchanges/{ex}?include_tickers=unexpired"
WANT = {"BTCUSDT": ("BTC", "USDT"), "ETHUSDT": ("ETH", "USDT")}


def snapshot(exchange_id: str = "binance_futures", symbols: list[str] | None = None, timeout: float = 15.0, transport=None) -> pd.DataFrame:
    symbols = symbols or list(WANT)
    cli = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "brando_pa/0.3 (read-only research)"})
    r = cli.get(URL.format(ex=exchange_id)); r.raise_for_status()
    d = r.json(); now = datetime.now(timezone.utc)
    rows = []
    for t in d.get("tickers", []):
        for sym, (base, target) in WANT.items():
            if sym in symbols and t.get("base") == base and t.get("target") == target and str(t.get("contract_type", "")).lower() == "perpetual":
                rows.append({"collected_at": now, "exchange": exchange_id, "symbol": sym, "last": _f(t.get("last")), "index_price": _f(t.get("index")), "basis_pct": _f(t.get("basis")), "funding_rate_pct": _f(t.get("funding_rate")),
                             "open_interest_usd": _f(t.get("open_interest_usd")), "volume_24h": _f(t.get("volume_24h") or t.get("converted_volume", {}).get("usd")), "spread_pct": _f(t.get("bid_ask_spread")), "ticker_ts": t.get("last_traded_at"), "source": "coingecko_derivatives"})
    return pd.DataFrame(rows)


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def collect(exchange_id: str = "binance_futures", symbols: list[str] | None = None) -> dict:
    p = own_data_dir() / "derivatives" / "forward_snapshots.parquet"
    try:
        df = snapshot(exchange_id, symbols)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}", "path": str(p)}
    if df.empty:
        return {"ok": False, "error": "无匹配合约", "path": str(p)}
    if p.exists():
        old = pd.read_parquet(p); df = pd.concat([old, df], ignore_index=True)
    p.parent.mkdir(parents=True, exist_ok=True); df.to_parquet(p, index=False)
    return {"ok": True, "rows_total": int(len(df)), "latest": df.tail(len(symbols or WANT)).to_dict("records"), "path": str(p)}
