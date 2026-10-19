"""K线图（TradingView Lightweight Charts，CDN 加载）+ A/B 买卖点标注，输出独立 HTML。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))


VENDOR_JS = "lightweight-charts.standalone.production.js"


def chart_time_seconds(bars: pd.DataFrame) -> list[int]:
    """图表时间轴：以 America/New_York 本地时刻伪装为 UTC 秒，使 Lightweight Charts 直接显示美东时间。"""
    if bars.empty:
        return []
    ny = pd.to_datetime(bars["ts_ny"], utc=True)
    return ((ny - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).astype(int).tolist()


def ensure_vendor_js(charts_dir: Path) -> bool:
    """把 node_modules 中的 Lightweight Charts 复制到报告目录，离线也能打开独立图表页。"""
    from ..config import PROJECT_ROOT
    src = PROJECT_ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / VENDOR_JS
    dst = charts_dir / "vendor" / VENDOR_JS
    if dst.exists():
        return True
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return True
    return False


def bars_to_candles(bars: pd.DataFrame) -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t, o, h, l, c, v in zip(secs, bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"].fillna(0)):
        out.append({"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)})
    return out


def markers_from_trades(trades: list[dict], bars: pd.DataFrame, label: str, color_buy: str, color_sell: str,
                        buy_key: str = "buy_idx", sell_key: str = "sell_idx") -> list[dict]:
    secs = chart_time_seconds(bars)
    out = []
    for t in trades:
        b, s = t.get(buy_key), t.get(sell_key)
        if b is not None and b < len(secs):
            out.append({"time": int(secs[b]), "position": "belowBar", "color": color_buy, "shape": "arrowUp",
                        "text": f"{label}买 {t.get('buy_exec', t.get('buy_ref', 0)):.2f}"})
        if s is not None and s < len(secs):
            out.append({"time": int(secs[s]), "position": "aboveBar", "color": color_sell, "shape": "arrowDown",
                        "text": f"{label}卖 {t.get('sell_exec', t.get('sell_ref', 0)):.2f}"})
    return sorted(out, key=lambda m: m["time"])


def render_ab_chart_html(bars: pd.DataFrame, ab, strategy_name: str | None = None, out_path: str | Path | None = None) -> str:
    strategy_name = strategy_name or ab.best_strategy
    r = ab.executable[strategy_name]
    candles = bars_to_candles(bars)
    ma = markers_from_trades(ab.hindsight.trades, bars, "A", "#2962FF", "#2962FF")
    mb = markers_from_trades(r.trades, bars, "B", "#00C853", "#D50000")
    tpl = _env.get_template("ab_chart.html.j2")
    html = tpl.render(
        symbol=ab.symbol, day=ab.day, strategy=strategy_name, simulated=ab.data_is_simulated,
        candles_json=json.dumps(candles), markers_a_json=json.dumps(ma), markers_b_json=json.dumps(mb),
        equity_json=json.dumps([{"time": c["time"], "value": float(e)} for c, e in zip(candles, r.equity.to_numpy())]),
        summary=ab.summary_table().to_dict(orient="records"), explanation=ab.gap_explanation,
        trades_a=ab.hindsight.trades, trades_b=r.trades, cost_profile=ab.cost_profile, capital=ab.capital_usd,
        source=str(bars["source"].iloc[0]),
    )
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        ensure_vendor_js(Path(out_path).parent)
        Path(out_path).write_text(html, encoding="utf-8")
    return html
