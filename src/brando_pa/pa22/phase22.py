"""第二十二阶段运行器：归档 + 实时 → 日表 → 证据等级 → 独立样本验证 → 前瞻事件（只追加）→ 结果文件。只读研究，不交易。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import own_data_dir
from ..trendsys.paper import _append
from . import DATA_SUBDIR, OUT_DIR, SYMS
from . import archive22, collector22, flow22, validate22


def combined_hourly(data_dir: Path, sym: str) -> pd.DataFrame:
    """同一小时：归档（完整日文件）优先，实时采集只补归档尚未发布的小时。"""
    a = archive22.load_hourly(data_dir / "okx_archive", sym); a["source"] = "okx_archive"
    lv = collector22.load_live_hourly(data_dir, "okx", sym)
    if not lv.empty:
        lv = lv[~lv["hour"].isin(a["hour"])].assign(source="okx_live"); a = pd.concat([a, lv], ignore_index=True).sort_values("hour")
    return a.reset_index(drop=True)


def live_daily(data_dir: Path, ex: str, sym: str) -> pd.DataFrame:
    h = collector22.load_live_hourly(data_dir, ex, sym)
    return archive22.daily(h) if not h.empty else pd.DataFrame()


def rubik_check(daily: pd.DataFrame, ccy: str) -> dict:
    """与 OKX 官方日主动买卖量统计（rubik，最近 180 天，现货该币种全部交易对汇总）比对失衡度方向；只作为方向口径的旁证。"""
    try:
        import httpx
        d = httpx.get("https://www.okx.com/api/v5/rubik/stat/taker-volume", params={"ccy": ccy, "instType": "SPOT", "period": "1D"}, timeout=20).json()["data"]
        r = pd.DataFrame(d, columns=["ts", "sell", "buy"]).astype(float); r["ts"] = pd.to_datetime(r["ts"], unit="ms", utc=True); r["imb_rubik"] = (r["buy"] - r["sell"]) / (r["buy"] + r["sell"])
        m = daily[["ts", "imb"]].merge(r[["ts", "imb_rubik"]], on="ts")
        return {"days": int(len(m)), "corr": float(m["imb"].corr(m["imb_rubik"])), "same_sign_share": float((np.sign(m["imb"]) == np.sign(m["imb_rubik"])).mean())}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def run(data_dir: Path | None = None, out_dir: Path | None = None, check_rubik: bool = True) -> dict:
    dd = Path(data_dir or own_data_dir() / DATA_SUBDIR); out = Path(out_dir or OUT_DIR); out.mkdir(parents=True, exist_ok=True); daily, states, cur, cont, rub, cb = {}, {}, {}, {}, {}, {}
    for s in SYMS:
        h = combined_hourly(dd, s); d = archive22.daily(h); d["source"] = h.groupby(h["hour"].dt.floor("D"))["source"].agg(lambda x: "+".join(sorted(set(x)))).to_numpy()
        daily[s] = d; st = flow22.evaluate(d); states[s] = st
        full = st[(d["hours"] == 24).to_numpy()]; last = full.iloc[-1].to_dict() if len(full) else {}
        cbd = live_daily(dd, "coinbase", s); cb3 = None
        if not cbd.empty and len(cbd) >= 3:
            t = cbd.tail(3); cb3 = float(t["net"].sum() / t["tot"].sum())
        okx3 = last.get("imb3"); agree = None if cb3 is None or okx3 is None else bool(np.sign(cb3) == np.sign(okx3))
        level = last.get("level", "证据不足")
        if level == "买盘确认" and agree is not True:
            level = "观察中"; last["why"] = (last.get("why") or "") + "；前瞻要求 OKX 与 Coinbase 最近 3 天失衡度同号，尚无足够 Coinbase 数据或方向不一致"
        cur[s] = {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in last.items()} | {"forward_level": level, "coinbase_imb3": cb3, "cross_exchange_agree": agree, "data_through_utc": str(h["hour"].max() + pd.Timedelta(hours=1)), "last_source": d["source"].iloc[-1]}
        cont[s] = archive22.continuity(dd / "okx_archive" / "manifest.jsonl", s) if (dd / "okx_archive" / "manifest.jsonl").exists() else {}
        if check_rubik:
            rub[s] = rubik_check(d, s.split("-")[0])
        d.to_csv(out / f"daily_{s}.csv", index=False); st.to_csv(out / f"states_{s}.csv", index=False)
    S = validate22.study(daily, states); V = validate22.verdict(S["results"])
    fe = out / "forward_events.jsonl"; have = {(e["symbol"], e["day"]) for e in map(json.loads, fe.read_text().splitlines())} if fe.exists() else set()
    for s, c in cur.items():
        if c.get("forward_level") == "买盘确认" and (s, c["ts"][:10]) not in have:
            _append(fe, {"symbol": s, "day": c["ts"][:10], "close": c["close"], "zone": c.get("zone"), "imb3_okx": c.get("imb3"), "imb3_coinbase": c.get("coinbase_imb3"), "note": "前瞻事件；到期后按 30/90/180 天评估"})
    R = {"generated_at_utc": str(pd.Timestamp.now(tz="UTC")), "category": "研究：OKX 真实逐笔成交（2021-09 起，价格历史已看过，订单流此前未用于设计）+ 实时采集", "preregistration": "docs/brando_pa_validation/phase22_orderflow/preregistration.md",
         "params": flow22.P, "current": cur, "archive_continuity": cont, "rubik_crosscheck": rub, "validation": S["results"], "verdict": V,
         "event_counts": {s: {lv: int((states[s]["level"] == lv).sum()) for lv in flow22.LEVELS} for s in SYMS}, "daily_range": {s: [str(daily[s]["ts"].iloc[0].date()), str(daily[s]["ts"].iloc[-1].date())] for s in SYMS}, "real_trading": "disabled (no implementation)"}
    (out / "phase22_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.DataFrame(S["events"]).to_csv(out / "confirm_events.csv", index=False); return R


if __name__ == "__main__":
    r = run(); print(json.dumps({"verdict": r["verdict"]["verdict"], "current": {s: r["current"][s].get("forward_level") for s in r["current"]}}, ensure_ascii=False))
