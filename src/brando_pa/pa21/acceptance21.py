"""第二十一阶段：72 小时本地服务验收（预登记补充 E）与 30/90/180/365 天前瞻评估（补充 F）。只读。
用法：PYTHONPATH=src python3 -m brando_pa.pa21.acceptance21 accept72 --since "2026-11-12 01:00" [--baseline PATH] [--prod-root ~/1hao-]
      PYTHONPATH=src python3 -m brando_pa.pa21.acceptance21 evaluate"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.sources import utcnow
from ..trendsys.paper import verify_chain
from . import ACCOUNTS, FORWARD_START, OUT_DIR, RESEARCH_INITIAL_USDT
from . import macguard21

CHAINS = ("daily_ledger", "alerts", "events", "runs", "observations", "drift")
FORBIDDEN_CODE = re.compile(r"create_order|place_order|/api/v5/trade|api_key|api_secret|passphrase|submit_order", re.I)
HORIZONS = (30, 90, 180, 365, 1461)


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def static_no_order_api(src_dir: Path | None = None) -> dict:
    d = Path(src_dir or Path(__file__).parent); hits = [f"{p.name}:{i + 1}" for p in sorted(d.glob("*.py")) if p.name != "acceptance21.py" for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()) if FORBIDDEN_CODE.search(line)]
    return {"ok": not hits, "hits": hits}


def accept72(since, out_dir: Path | None = None, now: pd.Timestamp | None = None, baseline: dict | None = None, prod_root: Path | None = None, hours: int = 72, require_platform: str | None = "darwin") -> dict:
    """require_platform：真实验收要求窗口内所有运行都来自同一台 Mac（sys.platform == darwin）；加速模拟传 None，结果注明不可作为真实验收。"""
    out = Path(out_dir or OUT_DIR); now = now or utcnow(); a = pd.Timestamp(since); a = a.tz_localize("UTC") if a.tzinfo is None else a; z = a + pd.Timedelta(hours=hours); C = {}
    runs = [r for r in _read(out / "runs.jsonl") if r.get("kind") == "tick"]
    for r in runs:
        r["_t"] = pd.Timestamp(r["tick_at"])
    win = [r for r in runs if a <= r["_t"] < z]; succ = [r for r in win if r["status"] in ("ok", "data_unavailable")]
    buckets = {int((r["_t"] - a).total_seconds() // 3600) for r in succ}
    C["elapsed"] = {"ok": now >= z, "detail": f"窗口 {a} → {z}；当前 {now}"}
    C["coverage"] = {"ok": len(buckets) >= int(np.ceil(0.95 * hours)), "detail": f"有成功运行的小时 {len(buckets)}/{hours}（需 ≥ {int(np.ceil(0.95 * hours))}）"}
    ts = [a] + sorted(r["_t"] for r in win) + [min(now, z)]; gap = max(((t2 - t1).total_seconds() / 3600 for t1, t2 in zip(ts[:-1], ts[1:])), default=hours)
    C["max_gap"] = {"ok": gap <= 3.0, "detail": f"最大间隔 {gap:.2f} 小时（需 ≤ 3）"}
    err = [r for r in win if r["status"] == "error"]; du = [r for r in win if r["status"] == "data_unavailable"]
    C["errors"] = {"ok": not err, "detail": f"未处理异常 {len(err)} 次" + (f"：{err[0].get('error')}" if err else "")}
    C["data_unavailable"] = {"ok": len(du) <= 0.10 * max(len(win), 1), "detail": f"行情不可用 {len(du)}/{len(win)} 次（需 ≤ 10%，均有记录）"}
    rows = {r["date"]: r for r in _read(out / "daily_ledger.jsonl") if r.get("kind") == "daily"}; obs = {o["date"]: o for o in _read(out / "observations.jsonl")}
    days = [d for d in pd.date_range((a - pd.Timedelta(days=1)).normalize(), (min(now, z) - pd.Timedelta(days=1)).normalize(), freq="D") if a <= d + pd.Timedelta(days=1) < min(now, z) and d >= pd.Timestamp(FORWARD_START, tz="UTC")]
    miss = [str(d.date()) for d in days if str(d.date()) not in rows]; notrt = [str(d.date()) for d in days if obs.get(str(d.date()), {}).get("category") != "实时观察"]
    C["daily_records_realtime"] = {"ok": bool(days) and not miss and not notrt, "detail": f"窗口内收盘日 {len(days)} 个；缺记录 {miss or '无'}；非实时 {notrt or '无'}"}
    ch = {k: verify_chain(out / f"{k}.jsonl") for k in CHAINS}; C["hash_chains"] = {"ok": all(v["ok"] for v in ch.values()) and ch["runs"]["n"] > 0, "detail": {k: v for k, v in ch.items()}}
    ev = _read(out / "events.jsonl"); fz_bad = [r for r in win if r.get("freeze_ok") is False]; mm = [e for e in ev if e["kind"] in ("replay_mismatch", "frozen_rule_violation")]
    C["freeze_and_replay"] = {"ok": not fz_bad and not mm, "detail": f"冻结校验失败 {len(fz_bad)} 次；重放不一致 / 冻结违规事件 {len(mm)} 条"}
    if baseline is None:
        C["mac_unchanged"] = {"ok": False, "detail": "未提供部署前基线（Mac 上运行时必须提供 --baseline）"}
    else:
        g = macguard21.compare(baseline, prod_root); C["mac_unchanged"] = {"ok": g["ok"], "detail": g}
    hosts = sorted({str(r.get("host")) for r in win}); plats = sorted({str(r.get("platform")) for r in win})
    if require_platform is None:
        C["local_mac"] = {"ok": True, "detail": f"加速模拟：未检查运行平台（{plats}），不能作为真实验收"}
    else:
        C["local_mac"] = {"ok": bool(win) and plats == [require_platform] and len(hosts) == 1, "detail": f"窗口内运行平台 {plats}，主机 {hosts}（需全部为同一台 {require_platform} 主机）"}
    so = static_no_order_api(); C["no_order_api"] = {"ok": so["ok"], "detail": "源码中没有下单 / 密钥接口" if so["ok"] else so["hits"]}
    passed = all(v["ok"] for v in C.values())
    return {"window": [str(a), str(z)], "checked_at": str(now), "passed": passed, "verdict": "通过" if passed else "未通过", "criteria": C, "n_ticks_in_window": len(win)}


def progress(since, out_dir: Path | None = None, now: pd.Timestamp | None = None, baseline: dict | None = None, prod_root: Path | None = None) -> dict:
    """验收进行中的读数：已过小时、运行次数、观察起点与已记录天数、当前问题清单（不含“未满 72 小时”及尚无法判断的项）。"""
    from . import FORWARD_START
    out = Path(out_dir or OUT_DIR); now = now or utcnow(); r = accept72(since, out, now=now, baseline=baseline, prod_root=prod_root)
    a = pd.Timestamp(r["window"][0]); el = (now - a).total_seconds() / 3600; rows = [x for x in _read(out / "daily_ledger.jsonl") if x.get("kind") == "daily"]; obs = _read(out / "observations.jsonl")
    pend = {"elapsed"} | ({"coverage"} if el < 72 else set()) | ({"daily_records_realtime"} if "窗口内收盘日 0 个" in str(r["criteria"]["daily_records_realtime"]["detail"]) else set())
    if el < 72:
        hrs = max(int(el), 0); seen = r["criteria"]["coverage"]["detail"]
        r["criteria"]["coverage"]["detail"] = f"{seen}；已过 {hrs} 小时"
    issues = [f"{k}：{v['detail'] if isinstance(v['detail'], str) else json.dumps(v['detail'], ensure_ascii=False, default=str)[:300]}" for k, v in r["criteria"].items() if not v["ok"] and k not in pend]
    cats = pd.Series([o["category"] for o in obs]).value_counts().to_dict() if obs else {}
    return {"acceptance_start_utc": str(a), "acceptance_end_utc": r["window"][1], "hours_elapsed": round(el, 2), "hours_remaining": round(max(0.0, 72 - el), 2), "n_ticks": r["n_ticks_in_window"],
            "forward_start_day": FORWARD_START, "first_record_day": rows[0]["date"] if rows else None, "last_record_day": rows[-1]["date"] if rows else None, "n_days_recorded": len(rows), "observation_categories": cats,
            "status": ("已完成：" + r["verdict"]) if el >= 72 else ("进行中，当前无问题" if not issues else "进行中，有问题"), "issues": issues, "criteria": {k: v["ok"] for k, v in r["criteria"].items()}}


def _metrics(rows: list[dict]) -> dict:
    btc = np.array([r["btc_close"] for r in rows], float); bret = np.diff(btc) / btc[:-1]; out = {}
    for n in ACCOUNTS:
        liq = np.array([r["accounts"][n]["liquidation"] for r in rows], float); w = np.array([r["accounts"][n]["btc_weight"] for r in rows], float); ar = np.diff(liq) / liq[:-1]; up = bret > 0
        cost = rows[-1]["accounts"][n]["fees_cum"] + rows[-1]["accounts"][n]["slippage_impact_cum"]
        out[n] = {"final": float(liq[-1]), "return": float(liq[-1] / RESEARCH_INITIAL_USDT - 1), "max_drawdown": float(min(r["accounts"][n]["max_drawdown"] for r in rows)), "avg_btc_weight": float(w.mean()), "time_in_market": float((w > 0.01).mean()),
                  "up_capture": float(ar[up].mean() / bret[up].mean()) if up.any() and bret[up].mean() != 0 else None, "cost_per_year_pct": float(cost / RESEARCH_INITIAL_USDT * 365 / len(rows)), "max_btc_weight": float(w.max()), "n_fills": int(rows[-1]["accounts"][n]["n_fills_cum"])}
    for n in ACCOUNTS:
        out[n]["return_gap_vs_BH"] = out[n]["return"] - out["BH_BTC"]["return"]; out[n]["return_gap_vs_B2"] = out[n]["return"] - out["B2_original"]["return"]
    return out


def evaluate(out_dir: Path | None = None, today: pd.Timestamp | None = None) -> dict:
    out = Path(out_dir or OUT_DIR); today = today or utcnow(); rows = [r for r in _read(out / "daily_ledger.jsonl") if r.get("kind") == "daily"]; obs = _read(out / "observations.jsonl"); t0 = pd.Timestamp(FORWARD_START, tz="UTC"); W = []
    for h in HORIZONS:
        due = t0 + pd.Timedelta(days=h); lab = f"{h} 天" if h < 1461 else "4 年（约一个牛熊周期）"
        if len(rows) < h:
            W.append({"horizon": lab, "due_date": str(due.date()), "status": "未到期", "detail": f"已记录 {len(rows)} 天，还差 {h - len(rows)} 天"}); continue
        R = rows[:h]; m = _metrics(R); f = m["FIX25_Y"]; btc = np.array([r["btc_close"] for r in R]); bmdd = float((btc / np.maximum.accumulate(btc) - 1).min()); bret = float(btc[-1] / btc[0] - 1)
        rt = sum(1 for o in obs[:h] if o["category"] == "实时观察") / max(len(obs[:h]), 1)
        crit = {"mdd_within_50": {"ok": f["max_drawdown"] >= -0.50, "detail": f"FIX25_Y 最大回撤 {f['max_drawdown']:.1%}"},
                "dd_vs_btc": ({"ok": abs(f["max_drawdown"]) <= 0.5 * abs(bmdd), "detail": f"BTC 最大回撤 {bmdd:.1%}，FIX25_Y {f['max_drawdown']:.1%}"} if bmdd <= -0.30 else {"ok": None, "detail": f"BTC 最大回撤 {bmdd:.1%} 未达 30%，无法检验"}),
                "cost": {"ok": f["cost_per_year_pct"] <= 0.002, "detail": f"年化成本 {f['cost_per_year_pct']:.3%}"},
                "upside": ({"ok": f["return"] >= 0.15 * bret, "detail": f"BTC {bret:+.1%}，FIX25_Y {f['return']:+.1%}"} if bret > 0 else {"ok": None, "detail": f"BTC {bret:+.1%} 未上涨，无法检验"})}
        thin = {n: m[n]["n_fills"] // 2 < 20 for n in ("B2_original", "SMA200")}
        W.append({"horizon": lab, "due_date": str(due.date()), "status": "已到期（描述性）", "realtime_share": rt, "metrics": m, "risk_criteria": crit,
                  "profit_verdict": "不判定：未满 4 年或年度再平衡少于 4 次" if h < 1461 else "只做描述性比较，不宣称验证成功", "trade_sample_insufficient": thin})
    return {"as_of": str(today), "n_days": len(rows), "windows": W, "rule": "任何窗口都不判定“盈利验证成功”；未到期窗口不评估；交易样本不足的对照单独标注"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("--since"); ap.add_argument("--baseline"); ap.add_argument("--prod-root"); ap.add_argument("--out")
    x = ap.parse_args(); o = Path(x.out) if x.out else None
    if x.cmd == "accept72":
        b = json.loads(Path(x.baseline).read_text(encoding="utf-8")) if x.baseline else None; r = accept72(x.since, o, baseline=b, prod_root=Path(x.prod_root).expanduser() if x.prod_root else None)
        (Path(o or OUT_DIR) / "acceptance72.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"verdict": r["verdict"], **{k: ("通过" if v["ok"] else "未通过") + " · " + (v["detail"] if isinstance(v["detail"], str) else "") for k, v in r["criteria"].items()}}, ensure_ascii=False, indent=1)); raise SystemExit(0 if r["passed"] else 1)
    if x.cmd == "progress":
        b = json.loads(Path(x.baseline).read_text(encoding="utf-8")) if x.baseline else None; r = progress(x.since, o, baseline=b, prod_root=Path(x.prod_root).expanduser() if x.prod_root else None)
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str)); raise SystemExit(0)
    if x.cmd == "evaluate":
        r = evaluate(o); (Path(o or OUT_DIR) / "evaluation.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps([{k: w.get(k) for k in ("horizon", "due_date", "status", "detail")} for w in r["windows"]], ensure_ascii=False, indent=1))
