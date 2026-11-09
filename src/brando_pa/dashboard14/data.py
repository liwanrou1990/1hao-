"""只读数据层：原子读取账户快照 / 事件链 / 行情缓存 / 审计输出，构造页面所需 JSON。
原则：① 只读（任何写入只进 dashboard14_cache 专用目录）；② 三类数据严格分区：mac_prod（原三账户前瞻）、shadow13（第十三阶段影子）、research（历史回测，只读 docs JSON）；
③ 不合并不同账户资金；④ 读取服务正在写的 JSON/JSONL 时容忍半行并重试；⑤ 不导入任何下单代码（仓库本身无真实下单实现，但本模块只引用账本 / 结构 / 审计只读函数）。"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..config import cfg, own_data_dir, report_dir
from ..data.store import BarStore
from ..forward.acceptance import acceptance_status
from ..forward.audit import audit_account
from ..forward.ledger_audit import replay_ledger
from ..forward.upgrade import check_live_trading_disabled
from ..pa11.structure import DailyStructureDetector, atr as _atr, structure_state, swings
from ..pa11.zones import SupportResistanceZones
from ..usdt.engine import liquidation_value

SYMS = ["BTCUSDT", "ETHUSDT"]
PROD_NAMES = ["A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"]
PROD_LABELS = {"A_BTC_SMA200": "账户 A：BTC SMA200", "B_modelB_state_machine": "账户 B：模型 B + 熔断状态机", "REF_ETH_SMA200": "参考：ETH SMA200"}
OKX_INST = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}
TF_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800, "1M": 2592000}


def read_json(p: Path, retries: int = 3):
    for i in range(retries):
        try:
            if not p.exists():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05 * (i + 1))
    return None


def read_events(p: Path) -> list[dict]:
    """逐行解析 JSONL；最后一行若不完整（服务正在写）跳过。"""
    out = []
    if not p.exists():
        return out
    try:
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return out
    for ln in lines:
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


class DashData:
    def __init__(self, c: dict | None = None, prod_dir: Path | None = None, shadow_dir: Path | None = None, bars_root: Path | None = None, shadow_bars_root: Path | None = None, cache_dir: Path | None = None, repo_root: Path | None = None, local_start: str | None = None, online: bool = True):
        self.c = c or cfg(); rd = report_dir(self.c); dd = own_data_dir(self.c)
        self.prod_dir = Path(prod_dir or os.environ.get("BRANDO_DASH_PROD_DIR", rd / "phase8_forward")); self.shadow_dir = Path(shadow_dir or os.environ.get("BRANDO_DASH_SHADOW_DIR", rd / "phase13_forward"))
        self.bars_root = Path(bars_root or os.environ.get("BRANDO_DASH_BARS", dd / "forward_bars")); self.shadow_bars_root = Path(shadow_bars_root or (dd / "phase13_bars"))
        self.cache_dir = Path(cache_dir or os.environ.get("BRANDO_DASH_CACHE", rd / "dashboard14_cache")); self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[3]); self.local_start = local_start or os.environ.get("BRANDO_DASH_LOCAL_START", "2026-11-08 12:23:00"); self.online = online and os.environ.get("BRANDO_DASH_OFFLINE", "") != "1"
        self._ticker_cache = {"ts": 0.0, "data": None}; self._bars_cache: dict = {}

    # ---------- 账户清单 ----------
    def accounts(self) -> list[dict]:
        out = []
        for name in PROD_NAMES:
            d = self.prod_dir / name
            if (d / "events.jsonl").exists():
                out.append({"group": "mac_prod", "group_label": "原 Mac 模拟账户（第九阶段前瞻）", "name": name, "label": PROD_LABELS.get(name, name), "dir": str(d)})
        if self.shadow_dir.exists():
            try:
                from ..pa13.shadow import shadow_specs
                specs = shadow_specs(self.c)
            except Exception:  # noqa: BLE001
                specs = {}
            for d in sorted(p for p in self.shadow_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
                out.append({"group": "shadow13", "group_label": "第十三阶段影子账户（独立前瞻）", "name": d.name, "label": specs.get(d.name, {}).get("label", d.name), "dir": str(d)})
        return out

    def _acc_dir(self, group: str, name: str) -> Path:
        base = self.prod_dir if group == "mac_prod" else self.shadow_dir; d = (base / name).resolve()
        if not str(d).startswith(str(base.resolve())) or not d.exists():
            raise FileNotFoundError(f"unknown account {group}/{name}")
        return d

    # ---------- 行情 ----------
    def _store(self, group: str = "mac_prod") -> BarStore:
        root = self.shadow_bars_root if (group == "shadow13" and (self.shadow_bars_root / "bars").exists()) else self.bars_root
        return BarStore(root=root, fallback_root=None)

    def bars(self, symbol: str, tf: str = "1d", limit: int = 500, before: str | None = None, group: str = "mac_prod") -> dict:
        if symbol not in SYMS or tf not in TF_SECONDS:
            raise ValueError("bad symbol/tf")
        st = self._store(group); base_tf = "1h" if tf in ("1h", "4h") else "1d"
        key = (symbol, base_tf, str(st.root)); p = st.path(symbol, base_tf); mtime = p.stat().st_mtime if p.exists() else 0
        if key not in self._bars_cache or self._bars_cache[key][0] != mtime:
            d = st.load(symbol, base_tf, allow_fallback=False)
            if not d.empty:
                d["ts"] = pd.to_datetime(d["ts"], utc=True); d = d.sort_values("ts").drop_duplicates("ts", keep="last")
            self._bars_cache[key] = (mtime, d)
        d = self._bars_cache[key][1]
        if d.empty:
            return {"symbol": symbol, "tf": tf, "bars": [], "note": "无行情缓存"}
        now = pd.Timestamp.now(tz="UTC"); d = d[d["ts"] + pd.Timedelta(seconds=TF_SECONDS[base_tf]) <= now]                      # 只用已收盘
        if tf != base_tf:
            rule = {"4h": "4h", "1w": "W-MON", "1M": "MS"}[tf]; x = d.set_index("ts").resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
            x = x[x["ts"] + pd.Timedelta(seconds=TF_SECONDS[tf]) <= now] if tf != "1M" else x[x["ts"] + pd.offsets.MonthBegin(1) <= now]
            src = d["source"].iloc[-1] if "source" in d else None; d = x; d["source"] = src
        full_close = d["close"].astype(float)
        d = d.assign(sma50=full_close.rolling(50).mean(), sma100=full_close.rolling(100).mean(), sma200=full_close.rolling(200).mean(), atr14=_atr(d, 14))
        if before:
            d = d[d["ts"] < pd.Timestamp(before, tz="UTC")]
        d = d.tail(int(limit))
        rows = [{"time": int(r.ts.timestamp()), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume), "sma50": None if pd.isna(r.sma50) else float(r.sma50), "sma100": None if pd.isna(r.sma100) else float(r.sma100), "sma200": None if pd.isna(r.sma200) else float(r.sma200), "atr14": None if pd.isna(r.atr14) else float(r.atr14)} for r in d.itertuples()]
        return {"symbol": symbol, "tf": tf, "bars": rows, "source": str(d["source"].iloc[-1]) if "source" in d and len(d) else None, "last_closed_utc": str(d["ts"].iloc[-1] + pd.Timedelta(seconds=TF_SECONDS[tf])) if len(d) else None, "store": str(st.root)}

    def structure(self, symbol: str, as_of: str | None = None, group: str = "mac_prod") -> dict:
        """日线结构：已确认摆动点与支撑/阻力区。as_of 给定时，只返回截至 as_of（含）已确认的结构（“当时已确认”）；同时给出截至最新的结构（“事后观察”）并分开标注。"""
        st = self._store(group); d = st.load(symbol, "1d", allow_fallback=False)
        if d.empty:
            return {"symbol": symbol, "as_of": as_of, "confirmed_at_time": None, "hindsight": None}
        d["ts"] = pd.to_datetime(d["ts"], utc=True); d = d.sort_values("ts").drop_duplicates("ts").reset_index(drop=True); now = pd.Timestamp.now(tz="UTC"); d = d[d["ts"] + pd.Timedelta(days=1) <= now].reset_index(drop=True)
        det = DailyStructureDetector(d); zobj = SupportResistanceZones(det.sw, None, None, det.atr)
        def pack(i: int) -> dict:
            sw = det.sw[det.sw.confirmed_at <= i]; zones = zobj.zones_at(i, det.close[i]); ss = structure_state(det.sw, i)
            return {"as_of_day": str(d["ts"].iloc[i].date()), "close": float(det.close[i]), "atr14": None if np.isnan(det.atr[i]) else float(det.atr[i]), "sma200": None if np.isnan(det.sma[i]) else float(det.sma[i]),
                    "swings": [{"time": int(d["ts"].iloc[int(r.idx)].timestamp()), "kind": r.kind, "price": float(r.price), "confirmed_time": int(d["ts"].iloc[int(r.confirmed_at)].timestamp())} for r in sw.tail(60).itertuples()],
                    "zones": [{"low": z.low, "high": z.high, "strength": z.strength, "kind": z.kind} for z in zones], "hh": ss["hh"], "hl": ss["hl"], "last_high": ss["last_high"], "last_low": ss["last_low"],
                    "trend_line": self._trend_line(d, sw, i)}
        i_last = len(d) - 1; i_as = i_last
        if as_of:
            t = pd.Timestamp(as_of, tz="UTC"); i_as = int((d["ts"] <= t).sum()) - 1; i_as = max(0, min(i_as, i_last))
        return {"symbol": symbol, "as_of": as_of, "confirmed_at_time": pack(i_as) | {"label": "当时已确认（截至所选日期）"}, "hindsight": pack(i_last) | {"label": "事后观察（截至最新已收盘日）"}}

    @staticmethod
    def _trend_line(d: pd.DataFrame, sw: pd.DataFrame, i: int) -> dict | None:
        lows = sw[sw.kind == "L"].tail(2); highs = sw[sw.kind == "H"].tail(2)
        if len(lows) == 2 and lows.price.iloc[1] > lows.price.iloc[0]:
            a, b = lows.iloc[0], lows.iloc[1]; return {"kind": "up", "from": {"time": int(d["ts"].iloc[int(a.idx)].timestamp()), "price": float(a.price)}, "to": {"time": int(d["ts"].iloc[int(b.idx)].timestamp()), "price": float(b.price)}, "direction": "上行（更高低点连线）"}
        if len(highs) == 2 and highs.price.iloc[1] < highs.price.iloc[0]:
            a, b = highs.iloc[0], highs.iloc[1]; return {"kind": "down", "from": {"time": int(d["ts"].iloc[int(a.idx)].timestamp()), "price": float(a.price)}, "to": {"time": int(d["ts"].iloc[int(b.idx)].timestamp()), "price": float(b.price)}, "direction": "下行（更低高点连线）"}
        return None

    def ticker(self) -> dict:
        """实时价格：OKX ticker（last / open24h），缓存 30 s；失败则回退到最新已收盘 1h 收盘并标注。"""
        if time.time() - self._ticker_cache["ts"] < 30 and self._ticker_cache["data"]:
            return self._ticker_cache["data"]
        out = {"fetched_at_utc": str(pd.Timestamp.now(tz="UTC")), "symbols": {}}
        for s in SYMS:
            row = None
            if self.online:
                try:
                    r = httpx.get("https://www.okx.com/api/v5/market/ticker", params={"instId": OKX_INST[s]}, timeout=5.0); r.raise_for_status(); x = r.json()["data"][0]
                    last = float(x["last"]); o24 = float(x["open24h"]); ts = pd.Timestamp(int(x["ts"]), unit="ms", tz="UTC")
                    row = {"price": last, "change_24h": last / o24 - 1 if o24 else None, "bid": float(x["bidPx"]), "ask": float(x["askPx"]), "source": "okx_spot_ticker", "ts_utc": str(ts), "latency_s": float((pd.Timestamp.now(tz="UTC") - ts).total_seconds()), "live": True}
                except Exception as e_:  # noqa: BLE001
                    row = {"error": f"{type(e_).__name__}"}
            if row is None or "error" in row:
                b = self.bars(s, "1h", 25); bars = b["bars"]
                if bars:
                    last = bars[-1]["close"]; ref = bars[0]["close"]; row = (row or {}) | {"price": last, "change_24h": last / ref - 1 if ref else None, "source": f"{b.get('source')} 最新已收盘 1h（非实时）", "ts_utc": b.get("last_closed_utc"), "latency_s": float((pd.Timestamp.now(tz="UTC") - pd.Timestamp(b["last_closed_utc"])).total_seconds()) if b.get("last_closed_utc") else None, "live": False}
                else:
                    row = (row or {}) | {"price": None, "live": False, "source": "无"}
            out["symbols"][s] = row
        self._ticker_cache = {"ts": time.time(), "data": out}; return out

    def trend_state(self, symbol: str) -> dict:
        b = self.bars(symbol, "1d", 260); bars = b["bars"]
        if not bars or bars[-1]["sma200"] is None:
            return {"trend": "未知", "note": "日线不足 200 根"}
        last = bars[-1]; prev20 = bars[-21]["sma200"] if len(bars) > 21 and bars[-21]["sma200"] else None
        return {"trend": "上行趋势（close > SMA200）" if last["close"] > last["sma200"] else "下行 / 无趋势（close ≤ SMA200）", "close": last["close"], "sma200": last["sma200"], "sma200_rising": (prev20 is not None and last["sma200"] > prev20), "sma50": last["sma50"], "sma100": last["sma100"], "last_closed_utc": b.get("last_closed_utc")}

    # ---------- 账户 ----------
    def _nav_rows(self, ev: list[dict], ledger: str = "cost") -> list[dict]:
        rows = []
        for e in ev:
            if e.get("kind") != "nav":
                continue
            l = e["ledgers"][ledger]; rows.append({"ts": e["ts"], "equity": l["equity_usdt"], "liquidation": l["liquidation_usdt"], "cash": l["cash"], "exposure": l.get("exposure"), "realized": l.get("realized_usdt"), "unrealized": l.get("unrealized_usdt"), "fees": l.get("fees_paid"), "holdings": l.get("holdings"), "prices": e.get("prices"), "risk_state": e.get("risk_state"), "mode": e.get("mode", "forward")})
        return rows

    def account_summary(self, group: str, name: str, ledger: str = "cost") -> dict:
        d = self._acc_dir(group, name); ev = read_events(d / "events.jsonl"); st = read_json(d / "state.json") or {}
        nav = self._nav_rows(ev, ledger); fills = [e for e in ev if e.get("kind") == "fill" and e.get("ledger") == ledger]
        initial = float(st.get("initial_usdt", 10000.0)); fee_bps = float(st.get("fee_bps", 10.0)) if ledger == "cost" else 0.0; slip_bps = float(st.get("slippage_bps", 5.0)) if ledger == "cost" else 0.0
        out = {"group": group, "name": name, "ledger": ledger, "initial_usdt": initial, "forward_start_utc": st.get("forward_start_utc"), "last_daily_ts": st.get("last_daily_ts"), "n_runs": st.get("n_runs"), "pending": len(st.get("pending", [])), "fee_bps": fee_bps, "slippage_bps": slip_bps, "n_fills": len(fills), "n_nav": len(nav)}
        if not nav:
            return out | {"status": "无 nav 记录（尚未运行）"}
        last = nav[-1]; L = replay_ledger(fills, initial, fee_bps, slip_bps); prices = last.get("prices") or {}
        eq_r = L.equity(prices) if prices else None
        hold_v = {s: L.qty(s) * float(prices.get(s, 0.0)) for s in SYMS}; liq = liquidation_value(L, prices) if prices else last["liquidation"]
        eqs = pd.Series([r["equity"] for r in nav], index=pd.to_datetime([r["ts"] for r in nav], utc=True)); peak = eqs.cummax(); dd = eqs / peak - 1
        longest = 0; cur = None
        for t, v in dd.items():
            if v < -1e-12:
                cur = cur or t
            elif cur is not None:
                longest = max(longest, (t - cur).days); cur = None
        if cur is not None:
            longest = max(longest, (dd.index[-1] - cur).days + 1)
        today = pd.Timestamp(last["ts"]).normalize(); prev = eqs[eqs.index < today]; today_change = float(eqs.iloc[-1] - prev.iloc[-1]) if len(prev) else float(eqs.iloc[-1] - initial)
        # 持仓周期：用重放账本的卖出记录里的 closed_lots.entry_ts
        hold_days = []
        for rec in L.trade_log:
            if rec["side"] == "SELL":
                for cl in rec.get("closed_lots", []):
                    try:
                        hold_days.append((pd.Timestamp(rec["ts"]) - pd.Timestamp(cl["entry_ts"])).total_seconds() / 86400)
                    except Exception:  # noqa: BLE001
                        pass
        open_days = [(pd.Timestamp(last["ts"]) - pd.Timestamp(l.ts)).total_seconds() / 86400 for ls in L.lots.values() for l in ls if l.qty > 1e-12]
        buy_fee = sum(float(f.get("fee", 0.0)) for f in fills if f["side"] == "BUY"); sell_fee = sum(float(f.get("fee", 0.0)) for f in fills if f["side"] == "SELL")
        slip_cost = sum(abs(float(f["execution_price"]) - float(f["reference_price"])) * float(f["qty"]) for f in fills if f.get("reference_price") is not None)
        consistent = eq_r is None or abs(eq_r - last["equity"]) < 1e-6 * max(1.0, abs(last["equity"]))
        return out | {"as_of_utc": last["ts"], "mark_prices": prices, "equity_usdt": last["equity"], "liquidation_usdt": liq, "cash_usdt": L.cash, "holdings": {s: L.qty(s) for s in SYMS}, "holdings_value_usdt": hold_v, "position_value_usdt": sum(hold_v.values()),
                      "weights": {"BTC": hold_v["BTCUSDT"] / last["equity"] if last["equity"] else 0, "ETH": hold_v["ETHUSDT"] / last["equity"] if last["equity"] else 0, "USDT": L.cash / last["equity"] if last["equity"] else 1},
                      "realized_net_usdt": float(L.realized) - buy_fee, "unrealized_usdt": float(L.unrealized(prices)) if prices else None, "fees_paid_usdt": float(L.fees_paid), "buy_fee_usdt": buy_fee, "sell_fee_usdt": sell_fee, "slippage_cost_usdt": slip_cost,
                      "profit_usdt": last["equity"] - initial, "return": last["equity"] / initial - 1, "liquidation_net_profit_usdt": liq - initial, "exit_cost_if_liquidated_usdt": last["equity"] - liq,
                      "max_drawdown": float(dd.min()), "max_drawdown_usdt": float((eqs - peak).min()), "longest_drawdown_days": int(longest), "today_change_usdt": today_change,
                      "avg_hold_days_closed": float(np.mean(hold_days)) if hold_days else None, "n_closed_lots": len(hold_days), "open_position_days": float(np.mean(open_days)) if open_days else None, "risk_state": last.get("risk_state"), "nav_consistent_with_replay": bool(consistent), "mode": last.get("mode", "forward")}

    def nav_series(self, group: str, name: str, ledger: str = "cost", daily: bool = True) -> dict:
        d = self._acc_dir(group, name); nav = self._nav_rows(read_events(d / "events.jsonl"), ledger)
        if daily and nav:
            df = pd.DataFrame(nav); df["day"] = pd.to_datetime(df["ts"], utc=True).dt.normalize(); df = df.groupby("day").last().reset_index()
            nav = [{"ts": str(r.day), "equity": r.equity, "liquidation": r.liquidation, "cash": r.cash, "exposure": r.exposure} for r in df.itertuples()]
        eq = pd.Series([r["equity"] for r in nav]); dd = (eq / eq.cummax() - 1).tolist() if len(eq) else []
        return {"group": group, "name": name, "ledger": ledger, "points": [{"ts": r["ts"], "equity": r["equity"], "liquidation": r["liquidation"], "cash": r["cash"], "exposure": r.get("exposure"), "drawdown": dd[i]} for i, r in enumerate(nav)]}

    def buyhold_reference(self, group: str, name: str, symbol: str, ledger: str = "cost") -> dict | None:
        """同起点、同本金、同成本的买入持有参考（从该账户 forward_start 后第一根 1h 开盘买入，按最新 nav 标记价估值）。"""
        s = self.account_summary(group, name, ledger)
        if not s.get("forward_start_utc") or not s.get("mark_prices"):
            return None
        b = self.bars(symbol, "1h", 5000, group=group)["bars"]; fs = pd.Timestamp(s["forward_start_utc"]).timestamp(); after = [x for x in b if x["time"] >= fs]
        if not after:
            return None
        entry = after[0]["open"]; fee = s["fee_bps"] / 1e4; slip = s["slippage_bps"] / 1e4; q = (s["initial_usdt"] * 0.999) / (entry * (1 + slip) * (1 + fee)); mark = s["mark_prices"].get(symbol)
        eq = s["initial_usdt"] * 0.001 + q * mark; liq = s["initial_usdt"] * 0.001 + q * mark * (1 - slip) * (1 - fee)
        return {"symbol": symbol, "entry_price": entry, "entry_time_utc": str(pd.Timestamp(after[0]["time"], unit="s", tz="UTC")), "qty": q, "equity_usdt": eq, "liquidation_usdt": liq, "net_profit_usdt": liq - s["initial_usdt"], "comparable": True, "note": "同起点、同本金、同成本口径的买入持有（估算，非账户记录）"}

    def trades(self, group: str | None = None, name: str | None = None, symbol: str | None = None, side: str | None = None, start: str | None = None, end: str | None = None, q: str | None = None, ledger: str = "cost", limit: int = 500, offset: int = 0, sort: str = "desc") -> dict:
        rows = []
        for a in self.accounts():
            if group and a["group"] != group or name and a["name"] != name:
                continue
            ev = read_events(Path(a["dir"]) / "events.jsonl"); orders = {e["order_id"]: e for e in ev if e.get("kind") == "order"}; sigs = [e for e in ev if e.get("kind") == "signal"]
            for f in ev:
                if f.get("kind") != "fill" or f.get("ledger") != ledger:
                    continue
                o = orders.get(f["order_id"], {}); sig = next((s_ for s_ in reversed(sigs) if s_.get("signal_ts") == (f.get("signal_candle_close_time") or o.get("signal_ts"))), None)
                ref = f.get("reference_price"); slip = (float(f["execution_price"]) - float(ref)) * float(f["qty"]) * (1 if f["side"] == "BUY" else -1) if ref is not None else None
                rows.append({"group": a["group"], "account": a["name"], "symbol": f["symbol"], "side": f["side"], "signal_ts": f.get("signal_candle_close_time") or o.get("signal_ts"), "decision_time": f.get("decision_time"), "exec_after": f.get("exec_after"), "execution_time": f.get("execution_time") or f.get("fill_ts"), "price_time": f.get("price_time"), "fill_price": f.get("execution_price"), "reference_price": ref, "qty": f.get("qty"),
                             "fee": f.get("fee"), "slippage_usdt": slip, "net_pnl": f.get("net_pnl"), "status": f.get("order_status"), "price_kind": f.get("price_kind"), "estimated": bool(f.get("estimated")) if f.get("estimated") is not None else None, "late": f.get("late"), "reason": f.get("reason"), "signal_reason": (sig or {}).get("reason"), "target_weights": (sig or {}).get("target_weights"), "risk_state": (sig or {}).get("risk_state"), "market_data_source": f.get("market_data_source"), "order_id": f["order_id"], "event_hash": f.get("hash"), "mode": f.get("mode", "forward"), "cash_after": f.get("cash_after"), "equity_after": f.get("equity_after")})
        df = pd.DataFrame(rows)
        if len(df):
            if symbol: df = df[df.symbol == symbol]
            if side: df = df[df.side == side.upper()]
            if start: df = df[pd.to_datetime(df.execution_time, utc=True) >= pd.Timestamp(start, tz="UTC")]
            if end: df = df[pd.to_datetime(df.execution_time, utc=True) <= pd.Timestamp(end, tz="UTC")]
            if q: df = df[df.apply(lambda r: q.lower() in json.dumps(r.to_dict(), default=str, ensure_ascii=False).lower(), axis=1)]
            df = df.sort_values("execution_time", ascending=(sort == "asc"))
        total = int(len(df)); page = df.iloc[offset:offset + limit] if len(df) else df
        return {"total": total, "offset": offset, "limit": limit, "rows": json.loads(page.to_json(orient="records", date_format="iso")) if len(page) else []}

    def trade_detail(self, group: str, name: str, order_id: str) -> dict:
        d = self._acc_dir(group, name); ev = read_events(d / "events.jsonl")
        related = [e for e in ev if e.get("order_id") == order_id]; sig_ts = next((e.get("signal_ts") or e.get("signal_candle_close_time") for e in related if e.get("signal_ts") or e.get("signal_candle_close_time")), None)
        ctx = [e for e in ev if e.get("kind") in ("signal", "missed_signal", "risk", "market", "vol_trail") and e.get("signal_ts") == sig_ts] if sig_ts else []
        corr = [c for c in read_events(d / "corrections.jsonl") if c.get("order_id") == order_id]
        struct = self.structure(related[0]["symbol"], as_of=str(pd.Timestamp(sig_ts).date()) if sig_ts else None, group=group) if related else None
        return {"order_id": order_id, "events": related, "decision_context": ctx, "corrections": corr, "structure_at_decision": (struct or {}).get("confirmed_at_time"), "structure_hindsight": (struct or {}).get("hindsight"), "note": "决策依据 = 当时 signal/risk/market 事件 + 截至信号日已确认的结构；事后结构单独标注"}

    def positions(self, group: str, name: str, ledger: str = "cost") -> dict:
        d = self._acc_dir(group, name); st = read_json(d / "state.json") or {}; s = self.account_summary(group, name, ledger)
        lots = st.get("ledgers", {}).get(ledger, {}).get("lots", {}); prices = s.get("mark_prices") or {}; tk = self.ticker()["symbols"]; fee = s["fee_bps"] / 1e4; slip = s["slippage_bps"] / 1e4
        out = []
        for sym, ls in lots.items():
            for l in ls:
                if l["qty"] <= 1e-12:
                    continue
                mark = prices.get(sym); live = tk.get(sym, {}).get("price"); tr = self.trend_state(sym); struct = self.structure(sym, group=group)["hindsight"] if True else None
                zones = struct["zones"] if struct else []; res_above = sorted([z for z in zones if z["low"] > (mark or 0)], key=lambda z: z["low"])[:2]
                vt = (st.get("vol_trail") or {}).get(sym) if name == "F_B2_vol_trail" else None
                exits = {"strategy_exit": f"日线收盘 ≤ SMA200（{tr.get('sma200'):,.0f}）→ 次轮卖出" if tr.get("sma200") else "SMA200 未知", "state_machine": (s.get("risk_state") or "—") if "state_machine" in name or name == "B_modelB_state_machine" or name == "A_B2_state_machine" or name == "F_B2_vol_trail" else "无（此账户无熔断）",
                         "vol_trail": (f"最高收盘 {vt['peak_close']:,.0f} − 3×ATR（{struct['atr14']:,.0f}）= {vt['peak_close'] - 3 * struct['atr14']:,.0f}" if (vt and vt.get("peak_close") and struct and struct.get("atr14")) else None), "buyhold": "不退出（买入持有账户）" if "BUYHOLD" in name else None}
                out.append({"symbol": sym, "entry_ts": l["ts"], "entry_price": l["price"], "qty": l["qty"], "mark_price": mark, "live_price": live, "market_value_usdt": (mark or 0) * l["qty"], "unrealized_usdt": ((mark or 0) - l["price"]) * l["qty"], "unrealized_pct": ((mark or 0) / l["price"] - 1) if l["price"] else None,
                            "holding_days": (pd.Timestamp.now(tz="UTC") - pd.Timestamp(l["ts"])).total_seconds() / 86400, "est_net_if_sold_usdt": (mark or 0) * l["qty"] * (1 - slip) * (1 - fee), "exit_conditions": exits,
                            "structure_invalidation_research": {"last_confirmed_swing_low": struct.get("last_low") if struct else None, "label": "研究性分析（第十一阶段结构），非策略退出规则"},
                            "candidate_sell_zones_research": [{"low": z["low"], "high": z["high"], "strength": z["strength"]} for z in res_above] or None, "sell_zone_note": "来自已确认阻力区（研究性分析）；策略本身不设目标价" if res_above else "尚无可信目标（上方无已确认阻力）"})
        return {"group": group, "name": name, "ledger": ledger, "positions": out, "cash_usdt": s.get("cash_usdt"), "equity_usdt": s.get("equity_usdt"), "realized_net_usdt": s.get("realized_net_usdt"), "unrealized_usdt": s.get("unrealized_usdt"), "as_of_utc": s.get("as_of_utc")}

    # ---------- 机会 ----------
    def opportunities(self) -> dict:
        out = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "symbols": {}, "note": "状态只来自冻结策略的最新信号事件；研究性分析（第十一阶段结构检测）单独标注，不构成可执行决策"}
        tk = self.ticker()["symbols"]
        for s in SYMS:
            tr = self.trend_state(s); frozen = []
            for a in self.accounts():
                ev = read_events(Path(a["dir"]) / "events.jsonl"); sig = [e for e in ev if e.get("kind") in ("signal", "missed_signal")]
                if not sig:
                    frozen.append({"account": a["name"], "group": a["group"], "state": "WAIT", "reason": "尚无信号（服务尚未处理任何日线收盘）"}); continue
                e = sig[-1]; tw = (e.get("target_weights") or {}).get(s, 0.0); sgn = e.get("signal")
                stx = read_json(Path(a["dir"]) / "state.json") or {}; held_now = sum(l["qty"] for l in stx.get("ledgers", {}).get("cost", {}).get("lots", {}).get(s, [])) > 1e-9; pend = any(p_["symbol"] == s for p_ in stx.get("pending", []))
                # 状态口径：当前持仓 + 最新目标权重；BUY 只在“目标 > 0 且尚未持有（含挂单待成交）”，HOLD = 已持有且目标 > 0，EXIT = 持有但目标 0 的卖出信号，REDUCE = 持有且目标下调
                state = ("BUY" if (tw > 0 and not held_now) else "HOLD" if (tw > 0 and held_now and sgn in ("HOLD", "BUY")) else "REDUCE" if (tw > 0 and held_now and sgn in ("SELL", "REBALANCE")) else "EXIT" if (tw == 0 and (held_now or (sgn == "SELL" and pend))) else "WAIT")
                frozen.append({"account": a["name"], "group": a["group"], "state": state, "signal": sgn, "signal_ts": e.get("signal_ts"), "decision_time": e.get("decision_time"), "valid_until_utc": str(pd.Timestamp(e["signal_ts"]) + pd.Timedelta(days=1)) if e.get("signal_ts") else None, "target_weight": tw, "held_now": held_now, "pending_order": pend, "trend_on": (e.get("trend_on") or {}).get(s), "risk_state": e.get("risk_state"), "reason": e.get("reason"), "executed": e.get("executed"), "kind": e.get("kind")})
            research = self._research_opportunity(s, tr, tk.get(s, {}))
            out["symbols"][s] = {"price": tk.get(s, {}).get("price"), "trend": tr, "frozen_strategies": frozen, "research": research}
        return out

    def _research_opportunity(self, s: str, tr: dict, tk: dict) -> dict:
        try:
            from ..pa11.signals import BreakoutRetestDetector, EntryRiskRewardEvaluator, PullbackConfirmation, VolumeAndOrderFlowConfirmation
            st = self._store(); d = st.load(s, "1d", allow_fallback=False); d["ts"] = pd.to_datetime(d["ts"], utc=True); d = d.sort_values("ts").drop_duplicates("ts").reset_index(drop=True); d = d[d["ts"] + pd.Timedelta(days=1) <= pd.Timestamp.now(tz="UTC")].reset_index(drop=True)
            det = DailyStructureDetector(d); z = SupportResistanceZones(det.sw, None, None, det.atr); i = len(d) - 1; zones = z.zones_at(i, det.close[i]); ss = structure_state(det.sw, i)
            a = PullbackConfirmation().check(det, z, i, zones); b = BreakoutRetestDetector().check(det, z, i, zones); vol = VolumeAndOrderFlowConfirmation.bar(det, i); sup = SupportResistanceZones.nearest_support(zones, det.close[i])
            plan = None
            for k, r in (("A", a), ("B", b)):
                if r["signal"]:
                    plan = EntryRiskRewardEvaluator(1.5).plan(det.close[i], r["invalidation"], zones, ss, 0.0015, (r["zone"].low, r["zone"].high), r["reason"]) | {"trigger": k}; break
            state = "研究候选（非执行信号）" if plan and plan.get("ok") else ("证据不足" if plan else "WAIT")
            return {"label": "研究性分析 · 第十一阶段结构检测（不是冻结策略，不可执行）", "as_of_day": str(d["ts"].iloc[-1].date()), "state": state, "pullback": {"signal": a["signal"], "reason": a["reason"]}, "breakout_retest": {"signal": b["signal"], "reason": b["reason"]},
                    "support_zone": [sup.low, sup.high, sup.strength] if sup else None, "structure_invalidation": (ss["last_low"] - 0.25 * det.atr[i]) if ss["last_low"] else None, "volume_confirmation": {"vol_rel20": vol["vol_rel20"], "close_pos": vol["close_pos"]}, "plan": plan,
                    "candidate_holding_period": "数周至数月（趋势有效期间）", "est_cost_round_trip_bps": 30, "evidence_note": "历史上该规则 8.8 年仅 5 次通过全部门槛（第十一阶段），证据不足以作为独立买入依据"}
        except Exception as e_:  # noqa: BLE001
            return {"label": "研究性分析不可用", "error": f"{type(e_).__name__}: {e_}"}

    # ---------- 健康 ----------
    def health(self) -> dict:
        h = read_json(self.prod_dir / "health.json"); sc = read_json(self.prod_dir / "startup_check.json"); now = pd.Timestamp.now(tz="UTC")
        hb_age = (now - pd.Timestamp(h["heartbeat_utc"])).total_seconds() if h and h.get("heartbeat_utc") else None
        try:
            acc = acceptance_status(self.prod_dir, int(self.c.get("forward", {}).get("interval_s", 600)), now=now, local_start=self.local_start, write=False)
        except Exception as e_:  # noqa: BLE001
            acc = {"error": f"{type(e_).__name__}: {e_}", "status": "无法计算"}
        guard = check_live_trading_disabled(self.repo_root / "config" / "brando_pa.yaml")
        alerts = read_events(self.prod_dir / "alerts.jsonl")[-20:]; re_ = read_events(self.prod_dir / "runner_events.jsonl")
        checks = [e for e in re_ if e.get("kind") == "data_check"]; hosts = sorted({e.get("host") for e in checks if e.get("host")})
        missed = expired = paused = 0
        for a in self.accounts():
            if a["group"] != "mac_prod":
                continue
            for e in read_events(Path(a["dir"]) / "events.jsonl"):
                missed += e.get("kind") == "missed_signal"; expired += e.get("kind") == "order_expired"; paused += e.get("kind") == "fill_paused"
        bar_age = {}
        for s in SYMS:
            for tf in ("1h", "1d"):
                b = self.bars(s, tf, 2); bar_age[f"{s}_{tf}_h"] = round((now - pd.Timestamp(b["last_closed_utc"])).total_seconds() / 3600, 2) if b.get("last_closed_utc") else None
        local = acc.get("segments", {}).get("local", {}) if isinstance(acc, dict) else {}
        status = local.get("status", acc.get("status", "未知")); passed = status == "通过"
        shadow_h = read_json(self.shadow_dir / "health.json")
        return {"checked_at_utc": str(now), "mac_service": {"heartbeat_utc": h.get("heartbeat_utc") if h else None, "heartbeat_age_s": hb_age, "alive_by_heartbeat": (hb_age is not None and hb_age < 2 * int(h.get("interval_s", 600))) if h else False, "pid": h.get("pid") if h else None, "n_cycles_since_start": h.get("n_cycles") if h else None, "consecutive_failures": h.get("consecutive_failures") if h else None, "interval_s": h.get("interval_s") if h else None, "last_ok_cycle_at": h.get("last_ok_cycle_at") if h else None, "hosts_seen": hosts, "uptime_note": "运行时长 = 本地段起点至最新心跳（含中断）", "host_is_cloud_only": hosts == ["vm"] or hosts == []},
                "acceptance": {"status": status, "passed": passed, "banner": "验收通过" if passed else ("验收进行中（未满 72 小时）" if status == "进行中" else ("验收未通过" if status == "未通过" else status)), "local_start": self.local_start, "local": {k: local.get(k) for k in ("start_utc", "end_utc", "elapsed_hours", "cycles_observed", "cycles_expected", "coverage", "longest_gap_hours", "data_ok_share", "n_data_issues", "n_anomalies", "alerts_by_kind", "hosts", "verdict_note")}, "cloud_legacy": {k: v for k, v in acc.get("segments", {}).get("cloud_legacy", {}).items() if k != "per_day"} if isinstance(acc, dict) else None, "rules": "覆盖率 ≥ 95% 且最长中断 ≤ 2 h，满 72 h 后判定；页面只读取结果"},
                "data": {"bar_age_hours": bar_age, "startup_check": {k: sc.get(k) for k in ("checked_at_utc", "ok", "clock_skew_s")} if sc else None, "data_status": read_json(own_data_dir(self.c) / "forward_data_status.json")},
                "signals": {"missed_signals": missed, "expired_orders": expired, "fill_paused": paused}, "alerts_recent": alerts, "real_trading": {"live_trading_disabled": guard["ok"], "detail": guard["reason"], "api": "disabled (no implementation)"},
                "shadow13_service": {"heartbeat_utc": shadow_h.get("heartbeat_utc") if shadow_h else None, "n_cycles": shadow_h.get("n_cycles") if shadow_h else None, "note": "独立影子服务；未部署到 Mac 时此处为云端一次性运行或空"}}

    # ---------- 分析 ----------
    def analytics(self, group: str, name: str, ledger: str = "cost", start: str | None = None, end: str | None = None) -> dict:
        nav = self.nav_series(group, name, ledger, daily=True)["points"]; s = self.account_summary(group, name, ledger)
        if not nav:
            return {"group": group, "name": name, "status": "无数据"}
        df = pd.DataFrame(nav); df["ts"] = pd.to_datetime(df["ts"], utc=True)
        if start: df = df[df.ts >= pd.Timestamp(start, tz="UTC")]
        if end: df = df[df.ts <= pd.Timestamp(end, tz="UTC")]
        if df.empty:
            return {"group": group, "name": name, "status": "区间内无数据"}
        e = df.set_index("ts")["equity"]
        def changes(rule):
            r = e.resample(rule).last().dropna(); return [{"period": str(k.date()), "equity": float(v), "change": float(v - p) if p is not None else None} for (k, v), p in zip(r.items(), [None] + list(r.values[:-1]))]
        tr = self.trades(group, name, ledger=ledger, limit=100000)["rows"]; sells = [t for t in tr if t["side"] == "SELL" and t.get("net_pnl") is not None]
        pnl = sorted([float(t["net_pnl"]) for t in sells], reverse=True); top5 = sum(pnl[:5]); tot = sum(pnl)
        bh = {sym: self.buyhold_reference(group, name, sym, ledger) for sym in SYMS}
        days = (df.ts.iloc[-1] - df.ts.iloc[0]).days + 1
        regimes = "数据不足（前瞻不足 90 日，不做牛/熊/震荡分段）" if days < 90 else self._regimes(e)
        return {"group": group, "name": name, "ledger": ledger, "category": "forward（前瞻记录，非历史回测）", "window": {"start": str(df.ts.iloc[0]), "end": str(df.ts.iloc[-1]), "days": days}, "daily": changes("1D")[-90:], "weekly": changes("W-MON"), "monthly": changes("1ME"),
                "cum_profit_usdt": float(e.iloc[-1]) - s["initial_usdt"], "max_drawdown": s.get("max_drawdown"), "longest_drawdown_days": s.get("longest_drawdown_days"), "avg_exposure": float(df["exposure"].astype(float).mean()) if "exposure" in df else None, "time_flat": float((df["exposure"].astype(float) < 0.01).mean()) if "exposure" in df else None,
                "costs": {"fees_usdt": s.get("fees_paid_usdt"), "slippage_usdt": s.get("slippage_cost_usdt"), "exit_cost_if_liquidated_usdt": s.get("exit_cost_if_liquidated_usdt")}, "hold_days_closed_avg": s.get("avg_hold_days_closed"), "n_closed": s.get("n_closed_lots"),
                "trade_pnl_distribution": pnl, "top5_pnl_usdt": top5, "top5_share": (top5 / tot) if tot else None, "n_sells": len(sells), "evidence": "统计证据不足（闭合交易 < 10 笔）" if len(sells) < 10 else "可描述",
                "vs_buyhold": {sym: ({"account_liq_net": s.get("liquidation_net_profit_usdt"), "bh_net": bh[sym]["net_profit_usdt"], "excess_usdt": s.get("liquidation_net_profit_usdt") - bh[sym]["net_profit_usdt"], "comparable": True} if bh[sym] else {"comparable": False, "note": "不可直接比较（缺起点或标记价）"}) for sym in SYMS}, "regimes": regimes}

    def _regimes(self, e: pd.Series) -> dict:
        b = self.bars("BTCUSDT", "1d", 2000)["bars"]; px = pd.Series({pd.Timestamp(x["time"], unit="s", tz="UTC"): x["close"] for x in b}); r = px.pct_change(90); m = r.resample("1ME").last(); lab = m.apply(lambda x: "bull" if x > 0.2 else ("bear" if x < -0.2 else "sideways"))
        pe = e.resample("1ME").last().diff(); out = {}
        for k in ("bull", "bear", "sideways"):
            idx = lab[lab == k].index; sel = pe[pe.index.isin(idx)]; out[k] = {"months": int(len(sel)), "pnl_usdt": float(sel.sum()) if len(sel) else 0.0}
        return out

    # ---------- 历史研究（只读 docs JSON，单独分区） ----------
    def research(self) -> dict:
        root = self.repo_root / "docs" / "brando_pa_validation"; out = {"category": "历史回测（开发性分析，2018–2026 已多次查看，非盲测）", "phase10": None, "phase12": None, "phase11_reject_audit": None}
        p10 = read_json(root / "phase10_holding" / "phase10_results.json")
        if p10:
            out["phase10"] = {"latest_complete_day": p10.get("latest_complete_day"), "rankings_full_25": p10.get("rankings", {}).get("full|25"), "rankings_oos_25": p10.get("rankings", {}).get("oos|25"), "cost_note": "单侧 bps"}
        p12 = read_json(root / "phase12_core_pa" / "phase12_results.json")
        if p12:
            rows = [r for r in p12["rows"] if r["window"] == "full" and r["cost_bps"] == 25]; rows.sort(key=lambda r: -r["liquidation_net_profit_usdt"])
            out["phase12"] = {"rows": [{k: r.get(k) for k in ("group", "final_equity", "liquidation_net_profit_usdt", "max_drawdown", "longest_drawdown_days", "n_fills", "total_cost_usdt", "avg_core_exposure", "avg_opp_exposure", "time_flat", "top5_share_of_closed_pnl")} for r in rows[:20]], "window": p12["windows"].get("full"), "cost_note": "单侧 25 bps"}
        out["phase11_reject_audit"] = read_json(root / "phase12_core_pa" / "phase11_reject_audit.json")
        return out

    # ---------- 第十三阶段 ----------
    def phase13(self) -> dict:
        fm = read_json(self.repo_root / "docs" / "brando_pa_validation" / "phase13_forward" / "freeze_manifest.json")
        try:
            from ..pa13.audit13 import shadow_audit
            a = shadow_audit(self.shadow_dir, write=False) if self.shadow_dir.exists() else {"status": "未启动"}
        except Exception as e_:  # noqa: BLE001
            a = {"status": f"审计不可用：{type(e_).__name__}"}
        re_ = read_events(self.shadow_dir / "runner_events.jsonl"); hosts = sorted({e.get("host") for e in re_ if e.get("host")})
        cloud_only = hosts == ["vm"]; started = "accounts" in a and bool(a["accounts"])
        return {"freeze": {"git_commit": fm.get("git_commit") if fm else None, "frozen_at_utc": fm.get("frozen_at_utc") if fm else None, "strategies": fm.get("strategies") if fm else None, "costs": fm.get("costs") if fm else None}, "started": started, "status": "未启动" if not started else ("云容器一次性运行（非 Mac 连续运行）" if cloud_only else "运行中"), "hosts": hosts, "audit": a,
                "note": "六套冻结策略；未启动显示“未启动”，不足观察期显示“未到期”，闭合交易 < 10 笔显示“统计证据不足”；临时容器的一次性运行不算连续前瞻"}

    def meta(self) -> dict:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            commit = None
        return {"name": "Brando Investment Intelligence Dashboard", "phase": 14, "git_commit": commit, "read_only": True, "real_trading": "disabled (no implementation)", "paths": {"prod_dir": str(self.prod_dir), "shadow_dir": str(self.shadow_dir), "bars_root": str(self.bars_root), "shadow_bars_root": str(self.shadow_bars_root), "cache_dir": str(self.cache_dir)}, "data_categories": {"mac_prod": "原 Mac 三账户前瞻记录（events.jsonl / state.json）", "shadow13": "第十三阶段影子账户前瞻记录", "research": "docs/brando_pa_validation 历史回测 JSON（只读）"}, "server_time_utc": str(pd.Timestamp.now(tz="UTC"))}
