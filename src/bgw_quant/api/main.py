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
from ..reports.hindsight_report import generate_hindsight_report, path_markers
from ..reports.target_report import cum_series, generate_target_report, roundtrip_markers
from ..reports.simple_report import generate_simple_report, sim_markers
from ..reports.compare_report import generate_compare_report
from ..reports.engine_report import generate_engine_report
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


# ---------------------------------------------------------------- 事后最优路径
@app.get("/api/hindsight/summary")
def hindsight_summary(day: str | None = None):
    d = _day(day)
    p = pl()
    return _clean({"day": d, "rows": p.hindsight_summary(d), "tier_issues": p.tiers.issues, "simulated": p.plan.is_simulated,
                   "disclaimer": "已知完整走势的事后最优模拟，不代表实盘未来收益"})


@app.get("/api/hindsight/portfolio")
def hindsight_portfolio(day: str | None = None, tier: str = "T1", point: str = "mid"):
    d = _day(day)
    p = pl()
    t = next((x for x in p.tiers.tiers if x.id == tier), None)
    if t is None:
        raise HTTPException(404, "档位不存在")
    cap = t.representative_capitals((point,))[point]
    res = p.hindsight_portfolio(d, cap, target_return=t.target_daily_pct[0] / 100)
    return _clean({"tier": tier, "point": point, **res.to_dict()})


@app.get("/api/hindsight/{symbol}")
def hindsight_symbol(symbol: str, day: str | None = None, tier: str | None = None, point: str = "mid", capital: float | None = None):
    d = _day(day, symbol)
    p = pl()
    bars = p.hindsight_bars(symbol, d)
    if bars.empty:
        raise HTTPException(404, f"{symbol} {d} 无数据")
    if capital:
        tiers_hit = p.tiers.find(capital)
        target = tiers_hit[0].target_daily_pct[0] / 100 if tiers_hit else None
        r = p.hindsight_symbol(symbol, d, capital, target_return=target)
        rows = [{"tier": tiers_hit[0].id if tiers_hit else "无档位", "point": "custom", "capital_usd": capital, "result": r,
                 "target_daily_pct": list(tiers_hit[0].target_daily_pct) if tiers_hit else [None, None]}]
    else:
        rows = p.hindsight_tiers(symbol, d)
        if tier:
            rows = [x for x in rows if x["tier"] == tier and x["point"] == point] or rows[:1]
    out = {"symbol": symbol, "day": d, "venue": str(bars["venue"].iloc[0]), "source": str(bars["source"].iloc[0]),
           "fallback_native": bool(bars.attrs.get("fallback_native")), "simulated": bool((bars["quality"] & 128).any()),
           "candles": bars_to_candles(bars),
           "tiers": [{"tier": x["tier"], "point": x["point"], "capital_usd": x["capital_usd"], "target_daily_pct": x["target_daily_pct"],
                      **x["result"].to_dict(), "markers_max": path_markers(x["result"].trades, bars),
                      "markers_target": path_markers(x["result"].target_path, bars, "目标")} for x in rows],
           "chart_url": f"/reports/charts/hindsight_{symbol}_{d}_{p.cost_profile_name}.html"}
    return _clean(out)


@app.post("/api/hindsight/report")
def hindsight_report(day: str | None = None, symbols: list[str] | None = Query(default=None), with_portfolio: bool = True):
    d = _day(day)
    out = generate_hindsight_report(pl(), d, symbols, with_portfolio=with_portfolio)
    root = pl().settings.resolve_path(pl().settings.report_dir)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(root))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(root))
    return _clean(out)


# ---------------------------------------------------------------- 统一收益决策引擎
@app.get("/api/engine/{symbol}")
def engine_symbol(symbol: str, day: str | None = None):
    d = _day(day, symbol)
    p = pl()
    bars = p.hindsight_bars(symbol, d)
    if bars.empty:
        raise HTTPException(404, "无数据")
    out = []
    for c in p.engine_tiers(symbol, d):
        r, th, opt = c["run"], c["theoretical"], c["optimization"]
        out.append({"tier": c["tier"], "capital_usd": c["capital_usd"], "target_usd": c["target_usd"], "classification": c["classification"],
                    "run": {**r.to_dict(), "markers": sim_markers(r.trades, bars), "cum": cum_series(r.cum_curve, bars)},
                    "theoretical": {"profit": th.max_net_profit_usd, "n_trades": th.n_trades, "markers": path_markers(th.trades, bars, "A")},
                    "oos": ({**opt.oos_stats.to_dict(), "days": opt.oos_frame(), "folds": [{"fold": f.fold, "train": [str(f.train[0]), str(f.train[1])],
                             "test": [str(f.test[0]), str(f.test[1])], "params": f.params.to_dict()} for f in opt.folds], "note": opt.note} if opt else None)})
    return _clean({"symbol": symbol, "day": d, "source": str(bars["source"].iloc[0]), "simulated": bool((bars["quality"] & 128).any()),
                   "capability": p.capability(symbol).to_dict(), "candles": bars_to_candles(bars), "tiers": out,
                   "chart_url": f"/reports/charts/engine_{symbol}_{d}_{p.cost_profile_name}.html"})


@app.get("/api/engine-summary")
def engine_summary(day: str | None = None, symbols: list[str] | None = Query(default=None)):
    d = _day(day)
    p = pl()
    return _clean({"day": d, "rows": p.engine_summary(d, symbols or ["NVDA", "SPCX"]), "formal": not p.plan.is_simulated})


@app.post("/api/engine/report")
def engine_report(day: str | None = None, symbols: list[str] | None = Query(default=None)):
    d = _day(day)
    out = generate_engine_report(pl(), d, symbols)
    root = pl().settings.resolve_path(pl().settings.report_dir)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(root))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(root))
    return _clean(out)


# ---------------------------------------------------------------- 档位对比：A 理论最优 vs B 可执行 + 失败原因
@app.get("/api/compare/{symbol}")
def compare_symbol(symbol: str, day: str | None = None):
    d = _day(day, symbol)
    p = pl()
    bars = p.hindsight_bars(symbol, d)
    if bars.empty:
        raise HTTPException(404, "无数据")
    comps = p.compare_tiers(symbol, d)
    out = []
    for c in comps:
        ex, th = c["executable"], c["theoretical"]
        out.append({"tier": c["tier"], "capital_usd": c["capital_usd"], "target_usd": c["target_usd"], "analysis": c["analysis"], "tried": c["tried"],
                    "executable": {**ex.to_dict(), "markers": sim_markers(ex.trades, bars), "cum": cum_series(ex.cum_curve, bars)},
                    "theoretical": {"profit": th.max_net_profit_usd, "n_trades": th.n_trades, "target_met": th.target_met,
                                    "markers": path_markers(th.trades, bars, "A"), "trades": [t.to_dict() for t in th.trades], "notes": th.notes}})
    return _clean({"symbol": symbol, "day": d, "source": str(bars["source"].iloc[0]), "simulated": bool((bars["quality"] & 128).any()),
                   "fallback_native": bool(bars.attrs.get("fallback_native")), "candles": bars_to_candles(bars), "tiers": out,
                   "chart_url": f"/reports/charts/compare_{symbol}_{d}_{p.cost_profile_name}.html"})


@app.get("/api/compare-summary")
def compare_summary(day: str | None = None):
    d = _day(day)
    p = pl()
    return _clean({"day": d, "rows": p.compare_summary(d), "formal": not p.plan.is_simulated})


@app.post("/api/compare/report")
def compare_report(day: str | None = None, symbols: list[str] | None = Query(default=None), wf_symbols: list[str] | None = Query(default=None)):
    d = _day(day)
    out = generate_compare_report(pl(), d, symbols, wf_symbols=wf_symbols)
    root = pl().settings.resolve_path(pl().settings.report_dir)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(root))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(root))
    return _clean(out)


# ---------------------------------------------------------------- 简化版：多空多次交易 · 累计净利润达标
@app.get("/api/simple/{symbol}")
def simple_symbol(symbol: str, day: str | None = None, capital: float | None = None, target_usd: float | None = None):
    d = _day(day, symbol)
    p = pl()
    bars = p.hindsight_bars(symbol, d)
    if bars.empty:
        raise HTTPException(404, "无数据")
    try:
        results = [p.simple_day(symbol, d, capital, target_usd)] if capital else p.simple_tiers(symbol, d)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _clean({"symbol": symbol, "day": d, "source": str(bars["source"].iloc[0]), "venue": str(bars["venue"].iloc[0]),
                   "simulated": bool((bars["quality"] & 128).any()), "fallback_native": bool(bars.attrs.get("fallback_native")),
                   "candles": bars_to_candles(bars),
                   "results": [{**r.to_dict(), "markers": sim_markers(r.trades, bars), "cum": cum_series(r.cum_curve, bars)} for r in results],
                   "chart_url": f"/reports/charts/simple_{symbol}_{d}_{p.cost_profile_name}.html", "tier_issues": p.tiers.issues})


@app.get("/api/simple-summary")
def simple_summary(day: str | None = None):
    d = _day(day)
    p = pl()
    rows = []
    for sym in p.symbols():
        try:
            for r in p.simple_tiers(sym, d):
                x = r.to_dict()
                x.pop("trades"), x.pop("cum_curve")
                rows.append(x)
        except LookupError:
            continue
    return _clean({"day": d, "rows": rows, "formal": not p.plan.is_simulated})


@app.post("/api/simple/report")
def simple_report(day: str | None = None, symbols: list[str] | None = Query(default=None)):
    d = _day(day)
    out = generate_simple_report(pl(), d, symbols)
    root = pl().settings.resolve_path(pl().settings.report_dir)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(root))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(root))
    return _clean(out)


# ---------------------------------------------------------------- 固定收益目标 · 多次交易
@app.get("/api/target/capitals")
def target_capitals():
    p = pl()
    return _clean({"capitals": p.target_capitals(), "config": p.target_cfg.__dict__, "tier_issues": p.tiers.issues})


@app.get("/api/target/summary")
def target_summary(day: str | None = None):
    d = _day(day)
    p = pl()
    return _clean({"day": d, "rows": p.target_summary(d), "formal": not p.plan.is_simulated})


@app.get("/api/target/teaching/{symbol}")
def target_teaching(symbol: str, day: str | None = None, capital: float = 100000, target_pct: float | None = None):
    d = _day(day, symbol)
    p = pl()
    bars = p.hindsight_bars(symbol, d)
    if bars.empty:
        raise HTTPException(404, "无数据")
    try:
        t = p.target_teaching(symbol, d, capital, target_pct)
        s = p.target_strategy_day(symbol, d, capital, target_pct=target_pct)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _clean({"symbol": symbol, "day": d, "capital": capital, "source": str(bars["source"].iloc[0]), "venue": str(bars["venue"].iloc[0]),
                   "simulated": bool((bars["quality"] & 128).any()), "candles": bars_to_candles(bars),
                   "teaching": {**t.to_dict(), "markers": roundtrip_markers(t.trades, bars), "cum": cum_series(t.cum_curve, bars)},
                   "strategy": {**s.to_dict(), "markers": roundtrip_markers(s.trades, bars), "cum": cum_series(s.cum_curve, bars)},
                   "chart_url": f"/reports/charts/target_{symbol}_{d}_{p.cost_profile_name}.html"})


@app.get("/api/target/walkforward/{symbol}")
def target_walkforward(symbol: str, capital: float = 100000, target_pct: float | None = None, train_days: int = 20, test_days: int = 5):
    try:
        wf = pl().target_walkforward(symbol, capital, target_pct, train_days, test_days)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _clean({"symbol": symbol, "capital": capital, "target_return": wf.target_return, "oos_stats": wf.oos_stats.to_dict(),
                   "in_sample_mean": wf.in_sample_stats_mean, "note": wf.note, "oos_days": wf.oos_frame(),
                   "folds": [{"fold": f.fold, "train": [str(f.train[0]), str(f.train[1])], "test": [str(f.test[0]), str(f.test[1])],
                              "params": f.params, "train_stats": f.train_stats.to_dict()} for f in wf.folds]})


@app.post("/api/target/report")
def target_report(day: str | None = None, symbols: list[str] | None = Query(default=None), with_walkforward: bool = True,
                  wf_symbols: list[str] | None = Query(default=None)):
    d = _day(day)
    out = generate_target_report(pl(), d, symbols, with_walkforward=with_walkforward, wf_symbols=wf_symbols)
    root = pl().settings.resolve_path(pl().settings.report_dir)
    out["html_url"] = "/reports/" + str(Path(out["html"]).relative_to(root))
    out["csv_url"] = "/reports/" + str(Path(out["csv"]).relative_to(root))
    return _clean(out)


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
