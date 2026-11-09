"""只读 HTTP API（FastAPI）。只有 GET；不导入任何下单代码；默认只监听 127.0.0.1；任何写入只进 dashboard14_cache。"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import plotly
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .data import DashData

STATIC = Path(__file__).resolve().parent / "static"


def create_app(data: DashData | None = None) -> FastAPI:
    app = FastAPI(title="Brando Investment Intelligence Dashboard", version="14.0", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    D = data or DashData()

    @app.middleware("http")
    async def _ro(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"error": "read-only dashboard: only GET is allowed"}, status_code=405)
        resp = await call_next(request); resp.headers["Cache-Control"] = "no-store"; resp.headers["X-Brando-Read-Only"] = "1"; return resp

    def _ok(fn, *a, **k):
        try:
            return fn(*a, **k)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/meta")
    def meta(): return D.meta()

    @app.get("/api/accounts")
    def accounts(): return {"accounts": D.accounts()}

    @app.get("/api/overview")
    def overview():
        tk = D.ticker(); accs = D.accounts(); rows = []
        for a in accs:
            try:
                rows.append(a | D.account_summary(a["group"], a["name"]))
            except Exception as e_:  # noqa: BLE001
                rows.append(a | {"status": f"读取失败：{type(e_).__name__}"})
        h = D.health()
        return {"ticker": tk, "trend": {s: D.trend_state(s) for s in ("BTCUSDT", "ETHUSDT")}, "accounts": rows, "service": h["mac_service"], "acceptance": h["acceptance"], "real_trading": h["real_trading"], "data": {"bar_age_hours": h["data"]["bar_age_hours"]},
                "note": "各账户资金相互独立，本页不把不同账户相加为“总资产”；原 Mac 账户与第十三阶段影子账户分区显示"}

    @app.get("/api/accounts/{group}/{name}")
    def account(group: str, name: str, ledger: str = "cost"): return _ok(D.account_summary, group, name, ledger)

    @app.get("/api/accounts/{group}/{name}/nav")
    def nav(group: str, name: str, ledger: str = "cost", daily: bool = True): return _ok(D.nav_series, group, name, ledger, daily)

    @app.get("/api/accounts/{group}/{name}/positions")
    def positions(group: str, name: str, ledger: str = "cost"): return _ok(D.positions, group, name, ledger)

    @app.get("/api/accounts/{group}/{name}/analytics")
    def analytics(group: str, name: str, ledger: str = "cost", start: str | None = None, end: str | None = None): return _ok(D.analytics, group, name, ledger, start, end)

    @app.get("/api/accounts/{group}/{name}/buyhold")
    def buyhold(group: str, name: str, ledger: str = "cost"): return {s: _ok(D.buyhold_reference, group, name, s, ledger) for s in ("BTCUSDT", "ETHUSDT")}

    @app.get("/api/compare")
    def compare(ledger: str = "cost"):
        rows = []
        for a in D.accounts():
            try:
                s = D.account_summary(a["group"], a["name"], ledger); bh = {sym: D.buyhold_reference(a["group"], a["name"], sym, ledger) for sym in ("BTCUSDT", "ETHUSDT")}
                rows.append(a | s | {"vs_buyhold": {sym: ((s.get("liquidation_net_profit_usdt") or 0) - bh[sym]["net_profit_usdt"]) if bh[sym] else None for sym in bh}})
            except Exception as e_:  # noqa: BLE001
                rows.append(a | {"status": f"读取失败：{type(e_).__name__}"})
        groups = {r["group"] for r in rows}
        return {"rows": rows, "comparability": {"within_group": "同组账户：同起点、同本金、同报价与成本口径，可比较", "across_groups": "原 Mac 账户与第十三阶段影子账户起点不同 → 不可直接比较（页面分区显示）" if len(groups) > 1 else None, "vs_buyhold": "买入持有参考按各账户自身起点与成本估算"}}

    @app.get("/api/market/ticker")
    def ticker(): return D.ticker()

    @app.get("/api/market/trend")
    def trend(): return {s: D.trend_state(s) for s in ("BTCUSDT", "ETHUSDT")}

    @app.get("/api/market/bars")
    def bars(symbol: str = "BTCUSDT", tf: str = "1d", limit: int = Query(500, le=5000), before: str | None = None, group: str = "mac_prod"): return _ok(D.bars, symbol, tf, limit, before, group)

    @app.get("/api/market/structure")
    def structure(symbol: str = "BTCUSDT", as_of: str | None = None, group: str = "mac_prod"): return _ok(D.structure, symbol, as_of, group)

    @app.get("/api/opportunities")
    def opportunities(): return D.opportunities()

    @app.get("/api/health")
    def health(): return D.health()

    @app.get("/api/trades")
    def trades(group: str | None = None, name: str | None = None, symbol: str | None = None, side: str | None = None, start: str | None = None, end: str | None = None, q: str | None = None, ledger: str = "cost", limit: int = Query(200, le=5000), offset: int = 0, sort: str = "desc"):
        return D.trades(group, name, symbol, side, start, end, q, ledger, limit, offset, sort)

    @app.get("/api/trades.csv")
    def trades_csv(group: str | None = None, name: str | None = None, symbol: str | None = None, side: str | None = None, start: str | None = None, end: str | None = None, q: str | None = None, ledger: str = "cost"):
        rows = D.trades(group, name, symbol, side, start, end, q, ledger, limit=100000)["rows"]; buf = io.StringIO()
        cols = ["group", "account", "symbol", "side", "signal_ts", "decision_time", "exec_after", "execution_time", "price_time", "fill_price", "reference_price", "qty", "fee", "slippage_usdt", "net_pnl", "status", "price_kind", "estimated", "late", "reason", "signal_reason", "market_data_source", "order_id", "event_hash", "mode"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in rows]
        return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=brando_trades.csv"})

    @app.get("/api/trades/{group}/{name}/{order_id:path}")
    def trade_detail(group: str, name: str, order_id: str): return _ok(D.trade_detail, group, name, order_id)

    @app.get("/api/research")
    def research(): return D.research()

    @app.get("/api/phase13")
    def phase13(): return D.phase13()

    @app.get("/vendor/plotly.min.js")
    def plotly_js():
        p = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"; return FileResponse(str(p), media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    def index(): return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz(): return "ok"

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


def main(argv=None):
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(); ap.add_argument("--host", default=os.environ.get("BRANDO_DASH_HOST", "127.0.0.1")); ap.add_argument("--port", type=int, default=int(os.environ.get("BRANDO_DASH_PORT", "8514")))
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("本阶段只允许本机访问：host 必须是 127.0.0.1（不监听 0.0.0.0；远程访问留待认证方案）")
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
