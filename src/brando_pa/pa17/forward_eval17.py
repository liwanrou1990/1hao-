"""长期前瞻评估标准（预登记 §3）的计算工具。只读：读取前瞻账户 events.jsonl（nav 事件），不写入账户目录。
指标：清仓口径净资产与净收益、最大回撤、上涨 / 下跌参与度（日收益，对 BTC；B/REF 另对 50/50 与 ETH）、在市时间与持仓 / 空仓天数、
与同起点同成本 BTC（及 ETH、50/50）买入持有的差距；评估时点 30/90/180/365 天；365 天前只描述不评级；评分卡与警报阈值事先固定。
用法（Mac，只读）：PYTHONPATH=src python3 -m brando_pa.pa17.forward_eval17 --log-dir reports_out/brando_pa/phase8_forward --out ~/brando_eval_out"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FEE, SLIP = 10.0, 5.0
HORIZONS = (30, 90, 180, 365)
SCORE = {"mdd_vs_bh": 0.6, "up_capture_min": 0.5, "btc_up_for_capture": 0.20, "net_vs_bh_dd_allowance": 0.5, "long_flat_days": 180, "long_flat_btc_up": 0.30, "whipsaw_window": 90, "whipsaw_round_trips": 4, "whipsaw_cost_share": 0.01}


def _read_events(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def nav_frame(acc_dir: Path, ledger: str = "cost") -> pd.DataFrame:
    """nav 事件 → 每个 UTC 日最后一条：liquidation、equity、holdings、BTC/ETH 价格。"""
    rows = []
    for e in _read_events(acc_dir / "events.jsonl"):
        if e.get("kind") != "nav" or e.get("mode", "forward") == "replay":
            continue
        L = e["ledgers"][ledger]; pr = e.get("prices") or {}
        rows.append({"ts": pd.Timestamp(e["ts"]), "equity": L["equity_usdt"], "liquidation": L["liquidation_usdt"], "holdings": L.get("holdings") or {}, "btc": pr.get("BTCUSDT"), "eth": pr.get("ETHUSDT")})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("ts"); df["day"] = df["ts"].dt.tz_convert("UTC").dt.normalize()
    return df.groupby("day").last().reset_index().rename(columns={"day": "date"})


def _bh_liq(prices: pd.Series, initial: float) -> pd.Series:
    p0 = float(prices.iloc[0]); q = initial / (p0 * (1 + SLIP / 1e4) * (1 + FEE / 1e4))
    return q * prices * (1 - SLIP / 1e4) * (1 - FEE / 1e4)


def _mdd(s: pd.Series) -> float:
    return float((s / s.cummax() - 1).min()) if len(s) else 0.0


def metrics(df: pd.DataFrame, initial: float = 10000.0, fills: list[dict] | None = None) -> dict:
    """df：date, liquidation, holdings(dict), btc[, eth]。所有数字为清仓口径（已扣已发生成本与假设卖出成本）。"""
    if df.empty or len(df) < 2:
        return {"status": "数据不足（少于 2 个日末观测）"}
    s = df.set_index("date"); liq = s["liquidation"].astype(float); days = int((s.index[-1] - s.index[0]).days)
    btc = s["btc"].astype(float); bh = _bh_liq(btc, initial)
    out = {"start": str(s.index[0].date()), "end": str(s.index[-1].date()), "days": days, "net_liquidation_usdt": float(liq.iloc[-1]), "net_profit_usdt": float(liq.iloc[-1] - initial), "max_drawdown": _mdd(liq),
           "bh_btc": {"net_profit_usdt": float(bh.iloc[-1] - initial), "max_drawdown": _mdd(bh), "max_drawdown_usdt": float((bh - bh.cummax()).min())}}
    out["gap_vs_bh_btc_usdt"] = out["net_profit_usdt"] - out["bh_btc"]["net_profit_usdt"]
    if "eth" in s and s["eth"].notna().all():
        eth = s["eth"].astype(float); be = _bh_liq(eth, initial); mix = 0.5 * _bh_liq(btc, initial) + 0.5 * _bh_liq(eth, initial)
        out["bh_eth"] = {"net_profit_usdt": float(be.iloc[-1] - initial), "max_drawdown": _mdd(be)}; out["bh_5050"] = {"net_profit_usdt": float(mix.iloc[-1] - initial), "max_drawdown": _mdd(mix)}
    sr = liq.pct_change().dropna(); br = btc.pct_change().reindex(sr.index)
    out["up_capture_vs_btc"] = float(sr[br > 0].sum() / br[br > 0].sum()) if (br > 0).any() else None
    out["down_capture_vs_btc"] = float(sr[br < 0].sum() / br[br < 0].sum()) if (br < 0).any() else None
    held = s["holdings"].apply(lambda h: any(v > 1e-12 for v in (h or {}).values())); out["time_in_market"] = float(held.mean())
    runs = []; cur = None
    for t, h in held.items():
        if cur is None or cur[0] != h:
            if cur:
                runs.append(cur)
            cur = [h, t, t]
        else:
            cur[2] = t
    runs.append(cur)
    flats = [(r[1], r[2]) for r in runs if not r[0]]; holds = [(r[2] - r[1]).days + 1 for r in runs if r[0]]
    out["holding_runs_days"] = {"n": len(holds), "mean": float(np.mean(holds)) if holds else None, "median": float(np.median(holds)) if holds else None}
    out["longest_flat_days"] = max([(b - a).days + 1 for a, b in flats], default=0)
    cur_flat = flats[-1] if flats and flats[-1][1] == s.index[-1] else None
    out["current_flat"] = {"days": (cur_flat[1] - cur_flat[0]).days + 1, "btc_change_since": float(btc.iloc[-1] / btc.loc[cur_flat[0]] - 1)} if cur_flat else None
    # 警报
    alerts = []
    if cur_flat and out["current_flat"]["days"] > SCORE["long_flat_days"] and out["current_flat"]["btc_change_since"] > SCORE["long_flat_btc_up"]:
        alerts.append("长期踏空警报")
    if fills:
        recent = [f for f in fills if pd.Timestamp(f["ts"]) >= s.index[-1] - pd.Timedelta(days=SCORE["whipsaw_window"])]
        sells = sum(1 for f in recent if f.get("side") == "SELL"); cost = sum(float(f.get("fee") or 0) + abs(float(f.get("slippage_usdt") or 0)) for f in recent)
        if sells >= SCORE["whipsaw_round_trips"] and cost > SCORE["whipsaw_cost_share"] * float(liq.iloc[-1]):
            alerts.append("来回打脸警报")
    out["alerts"] = alerts
    # 评分卡（365 天起）
    if days < 365:
        out["scorecard"] = {"status": f"证据不足：仅 {days} 天（< 365 天只描述，不评级、不宣称跑赢或跑输）"}
    else:
        btc_up = float(btc.iloc[-1] / btc.iloc[0] - 1)
        cap = out["max_drawdown"] >= SCORE["mdd_vs_bh"] * out["bh_btc"]["max_drawdown"]
        part = None if btc_up < SCORE["btc_up_for_capture"] else bool((out["up_capture_vs_btc"] or 0) >= SCORE["up_capture_min"])
        net = out["net_profit_usdt"] >= out["bh_btc"]["net_profit_usdt"] + SCORE["net_vs_bh_dd_allowance"] * out["bh_btc"]["max_drawdown_usdt"]
        passed = [x for x in (cap, part, net) if x is not None]; n_fail = sum(1 for x in passed if not x)
        out["scorecard"] = {"capital_protection": cap, "upside_participation": part if part is not None else "不适用（同期 BTC 涨幅 < 20%）", "net_vs_bh": net,
                            "result": "继续" if n_fail == 0 else ("全部不达标（若连续两个评估年 → 停止评审）" if n_fail == len(passed) else "继续观察（说明不达标原因）")}
    out["horizons"] = {}
    for h in HORIZONS:
        if days >= h:
            sub = df[df["date"] <= df["date"].iloc[0] + pd.Timedelta(days=h)]
            ss = sub.set_index("date"); l2 = ss["liquidation"].astype(float); b2 = _bh_liq(ss["btc"].astype(float), initial)
            out["horizons"][f"{h}d"] = {"net_profit_usdt": float(l2.iloc[-1] - initial), "max_drawdown": _mdd(l2), "bh_btc_net_profit_usdt": float(b2.iloc[-1] - initial), "bh_btc_max_drawdown": _mdd(b2)}
        else:
            out["horizons"][f"{h}d"] = "未到期"
    return out


def evaluate_dir(log_dir: Path, initial: float = 10000.0) -> dict:
    out = {"checked_at_utc": str(pd.Timestamp.now(tz="UTC")), "log_dir": str(log_dir), "standard": "docs/brando_pa_validation/phase17_eval/preregistration.md §3", "score_thresholds": SCORE, "accounts": {}, "note": "只读评估；任何结论都不自动修改策略"}
    for d in sorted(p for p in Path(log_dir).iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        fills = [e for e in _read_events(d / "events.jsonl") if e.get("kind") == "fill" and e.get("ledger") == "cost"]
        fills = [{"ts": f.get("execution_time") or f.get("fill_ts"), "side": f.get("side"), "fee": f.get("fee"), "slippage_usdt": (float(f["execution_price"]) - float(f["reference_price"])) * float(f["qty"]) * (1 if f["side"] == "BUY" else -1) if f.get("reference_price") is not None else 0.0} for f in fills]
        out["accounts"][d.name] = metrics(nav_frame(d), initial, fills)
    return out


def from_backtest(res, bars: dict, start, end, initial: float = 10000.0) -> dict:
    """把日线回测结果转成同一口径，用于历史参考区间（同一评估标准）。"""
    nav = res.nav.copy(); nav["date"] = pd.to_datetime(nav["ts"], utc=True).dt.normalize(); nav = nav[(nav["date"] >= pd.Timestamp(start, tz="UTC")) & (nav["date"] <= pd.Timestamp(end, tz="UTC"))]
    b = bars["BTCUSDT"].copy(); b["date"] = pd.to_datetime(b["ts"], utc=True).dt.normalize(); bp = b.set_index("date")["close"].astype(float)
    df = pd.DataFrame({"date": nav["date"].values, "liquidation": nav["liquidation"].values, "holdings": nav["holdings"].values, "btc": bp.reindex(nav["date"]).values})
    if "ETHUSDT" in bars:
        e = bars["ETHUSDT"].copy(); e["date"] = pd.to_datetime(e["ts"], utc=True).dt.normalize(); df["eth"] = e.set_index("date")["close"].astype(float).reindex(nav["date"]).values
    df["liquidation"] = df["liquidation"] / df["liquidation"].iloc[0] * initial                     # 每个窗口从 10,000 重新起算（比例不变）
    return metrics(df, initial)


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--log-dir", required=True); ap.add_argument("--out", default=None); a = ap.parse_args(argv)
    log_dir = Path(a.log_dir).expanduser().resolve(); r = evaluate_dir(log_dir)
    txt = json.dumps(r, ensure_ascii=False, indent=1, default=str); print(txt)
    if a.out:
        out = Path(a.out).expanduser().resolve()
        if out == log_dir or log_dir in out.parents:
            raise SystemExit("拒绝：输出目录不能在前瞻账户目录之内（只读）")
        out.mkdir(parents=True, exist_ok=True); (out / f"forward_eval_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%MZ}.json").write_text(txt, encoding="utf-8")


if __name__ == "__main__":
    main()
