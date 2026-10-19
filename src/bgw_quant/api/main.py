"""FastAPI 后端：中文交易员工作台数据接口（V1 研究版）。"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..capital.tiers import admin_update_tiers
from ..config import PROJECT_ROOT, get_settings
from ..costs.model import CostModel, load_cost_profile
from ..execution.guard import ExecutionGuard
from ..pipeline import Pipeline, default_window
from ..reports.charts import bars_to_candles, markers_from_trades
from ..reports.daily import generate_daily_report
from ..training.session import TrainingSession

app = FastAPI(title="Bitget Wallet 美股代币化 AI 量化复盘与交易员训练系统", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_pipeline: Pipeline | None = None
_sessions: dict[str, TrainingSession] = {}
_guard = ExecutionGuard.from_env()


def pl() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
        if not _pipeline.latest_day():
            start, end = default_window(70)
            _pipeline.ingest(None, start, end)
    return _pipeline


def _clean(o: Any) -> Any:
    """把 numpy/NaN/日期转换成 JSON 安全对象。"""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if hasattr(o, "item"):
        return _clean(o.item())
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    if isinstance(o, pd.DataFrame):
        return _clean(o.to_dict(orient="records"))
    if isinstance(o, pd.Series):
        return _clean(o.tolist())
    return o


def _day(day: str | None, symbol: str = "NVDA") -> date:
    if day:
        return date.fromisoformat(day)
    d = pl().latest_day(symbol)
    if d is None:
        raise HTTPException(404, "无数据，请先 ingest")
    return d


@app.get("/api/status")
def status():
    s = pl().status()
    s["execution"] = _guard.status()
    s["server_time_utc"] = datetime.now(timezone.utc).isoformat()
    s["latest_day"] = pl().latest_day()
    return _clean(s)


@app.get("/api/symbols")
def symbols():
    p = pl()
    return _clean({"symbols": p.symbols(), "tradable": p.symbols(tradable_only=True),
                   "days": {s: [d.isoformat() for d in p.store.available_days(s)] for s in p.symbols()}})


@app.get("/api/rankings")
def rankings(day: str | None = None):
    d = _day(day)
    return _clean({"day": d, "simulated": pl().plan.is_simulated, "rows": pl().rankings(d)})


@app.get("/api/bars/{symbol}")
def bars(symbol: str, day: str | None = None, venue: str = "native"):
    d = _day(day, symbol)
    b = pl().day_bars(symbol, d, venue)
    if b.empty:
        raise HTTPException(404, f"{symbol} {d} {venue} 无数据")
    return _clean({"symbol": symbol, "day": d, "venue": venue, "source": b["source"].iloc[0], "simulated": bool((b["quality"] & 128).any()),
                   "candles": bars_to_candles(b)})


@app.get("/api/replay/{symbol}")
def replay(symbol: str, day: str | None = None, capital: float | None = None, strategy: str | None = None):
    d = _day(day, symbol)
    p = pl()
    try:
        ab = p.replay_day(symbol, d, capital)
    except LookupError as e:
        raise HTTPException(404, str(e))
    b = p.day_bars(symbol, d)
    strat = strategy or ab.best_strategy
    if strat not in ab.executable:
        raise HTTPException(404, f"策略 {strat} 不存在")
    r = ab.executable[strat]
    out = ab.to_dict()
    out.update({"candles": bars_to_candles(b), "markers_a": markers_from_trades(ab.hindsight.trades, b, "A", "#2962FF", "#2962FF"),
                "markers_b": markers_from_trades(r.trades, b, "B", "#00C853", "#D50000"), "strategy": strat,
                "equity_b": [float(x) for x in r.equity.to_numpy()], "signals_b": [int(x) for x in r.signals.to_numpy()],
                "summary": ab.summary_table().to_dict(orient="records"), "strategies": list(ab.executable.keys()),
                "chart_url": f"/reports/charts/{symbol}_{d}_{p.cost_profile_name}.html"})
    return _clean(out)


@app.get("/api/trades/{symbol}")
def trades(symbol: str, day: str | None = None):
    d = _day(day, symbol)
    ab = pl().replay_day(symbol, d)
    rows = [{"mode": "A", "strategy": "DP", **t} for t in ab.hindsight.trades]
    for k, r in ab.executable.items():
        rows += [{"mode": "B", "strategy": k, **t} for t in r.trades]
    return _clean({"day": d, "symbol": symbol, "trades": rows})


@app.get("/api/backtest/{symbol}")
def backtest(symbol: str, train_days: int = 20, test_days: int = 5):
    try:
        res = pl().backtest(symbol, train_days, test_days)
    except LookupError as e:
        raise HTTPException(404, str(e))
    out = {k: v for k, v in res.items() if not k.startswith("_")}
    out["oos_daily"] = {e.strategy_key: e.oos_daily.reset_index().to_dict(orient="records") for e in res["_evals"]}
    out["benchmark_daily"] = res["_bench"].reset_index().to_dict(orient="records")
    return _clean(out)


@app.get("/api/stress/{symbol}")
def stress(symbol: str, strategy: str = "vwap", days: int = 10):
    return _clean({"symbol": symbol, "strategy": strategy, "rows": pl().stress(symbol, strategy, days)})


@app.get("/api/tiers")
def tiers():
    return _clean(pl().tiers.to_dict())


@app.put("/api/tiers")
def update_tiers(payload: dict = Body(...)):
    cfg = admin_update_tiers(payload["tiers"], payload.get("accrual_basis"), payload.get("period_unit"))
    pl().tiers = cfg
    return _clean(cfg.to_dict())


@app.get("/api/capacity")
def capacity(symbol: str | None = None):
    return _clean({"cost_profile": pl().cost_profile_name, "rows": pl().capacity(symbol)})


@app.get("/api/attainment/{symbol}")
def attainment(symbol: str, strategy: str | None = None):
    return _clean({"symbol": symbol, "rows": pl().tier_attainment(symbol, strategy), "tier_issues": pl().tiers.issues})


@app.get("/api/ai/{symbol}")
def ai(symbol: str, day: str | None = None):
    d = _day(day, symbol)
    p = pl()
    wf = p.train_ml(symbol)
    sig = p.ai_signals(symbol, d)
    return _clean({"symbol": symbol, "day": d, "summary": wf.summary(), "note": wf.note, "simulated": wf.data_is_simulated,
                   "folds": [f.__dict__ for f in wf.folds][-8:], "signals": sig if not sig.empty else []})


@app.get("/api/execution/status")
def execution_status():
    return _guard.status()


# ---------------------------------------------------------------- 训练模式
@app.post("/api/training/start")
def training_start(payload: dict = Body(...)):
    p = pl()
    symbol = payload.get("symbol", "NVDA")
    d = _day(payload.get("day"), symbol)
    b = p.day_bars(symbol, d)
    if b.empty:
        raise HTTPException(404, "无数据")
    cm = CostModel(load_cost_profile(p.cost_profile_name))
    s = TrainingSession.start(b, cm, payload.get("trader", "anonymous"), float(payload.get("capital", p.capital_usd)),
                              p.cost_profile_name, int(payload.get("warmup", 30)))
    _sessions[s.state.session_id] = s
    return _training_view(s)


def _training_view(s: TrainingSession) -> dict:
    vb = s.visible_bars()
    return _clean({"session_id": s.state.session_id, "symbol": s.state.symbol, "day": s.state.day, "cursor": s.state.cursor,
                   "total_bars": len(s.bars), "position": s.state.position, "pending": s.state.pending_action,
                   "finished": s.state.finished, "candles": bars_to_candles(vb), "decisions": s.state.decisions,
                   "simulated": s.state.data_is_simulated, "score": s.state.score})


def _sess(sid: str) -> TrainingSession:
    if sid not in _sessions:
        raise HTTPException(404, "训练会话不存在")
    return _sessions[sid]


@app.get("/api/training/{sid}")
def training_get(sid: str):
    return _training_view(_sess(sid))


@app.post("/api/training/{sid}/decide")
def training_decide(sid: str, payload: dict = Body(...)):
    s = _sess(sid)
    s.decide(payload.get("action", "hold"))
    return _training_view(s)


@app.post("/api/training/{sid}/advance")
def training_advance(sid: str):
    s = _sess(sid)
    s.advance()
    return _training_view(s)


@app.post("/api/training/{sid}/finish")
def training_finish(sid: str):
    s = _sess(sid)
    p = pl()
    ai_sig = None
    try:
        sig = p.ai_signals(s.state.symbol, date.fromisoformat(s.state.day))
        if not sig.empty:
            best_h = sig.groupby("horizon")["decision"].sum().idxmax()
            ai_sig = sig[sig["horizon"] == best_h].set_index("idx")["decision"]
    except Exception:  # noqa: BLE001
        ai_sig = None
    from ..strategies.rules import all_strategies
    score = s.finish(ai_signals=ai_sig, rule_strategies=all_strategies())
    p.store.save_json("training_sessions", session_id=s.state.session_id, trader=s.state.trader, symbol=s.state.symbol,
                      day=date.fromisoformat(s.state.day), state=json.loads(s.to_json()), created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
    return _clean({"score": score, **_training_view(s)})


@app.get("/api/training/leaderboard/all")
def training_leaderboard():
    df = pl().store.query("SELECT session_id, trader, symbol, day, state, created_at FROM training_sessions ORDER BY created_at DESC LIMIT 100")
    rows = []
    for _, r in df.iterrows():
        st = json.loads(r["state"]) if isinstance(r["state"], str) else r["state"]
        sc = (st or {}).get("score") or {}
        rows.append({"session_id": r["session_id"], "trader": r["trader"], "symbol": r["symbol"], "day": str(r["day"]),
                     "net_return": sc.get("comparison", {}).get("trader", {}).get("net_return"), "grade": sc.get("grade"),
                     "capture": sc.get("capture_ratio_vs_hindsight")})
    return _clean({"rows": rows})


# ---------------------------------------------------------------- 报告
@app.post("/api/report")
def make_report(day: str | None = None, symbols: list[str] | None = Query(default=None), with_ml: bool = True, with_backtest: bool = True):
    d = _day(day)
    out = generate_daily_report(pl(), d, symbols, with_ml=with_ml, with_backtest=with_backtest)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(pl().settings.resolve_path(pl().settings.report_dir)))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(pl().settings.resolve_path(pl().settings.report_dir)))
    return _clean(out)


@app.get("/api/reports")
def list_reports():
    root = pl().settings.resolve_path(pl().settings.report_dir)
    items = []
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_file() and p.suffix in (".html", ".csv", ".pdf"):
                items.append({"name": p.name, "url": "/reports/" + str(p.relative_to(root)), "size": p.stat().st_size,
                              "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()})
    return {"items": items[:200]}


_report_dir = get_settings().resolve_path(get_settings().report_dir)
_report_dir.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(_report_dir)), name="reports")

_dist = PROJECT_ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(str(_dist / "index.html"))
else:
    @app.get("/")
    def index_fallback():
        return JSONResponse({"message": "前端未构建：cd frontend && npm install && npm run build；API 文档见 /docs"})
