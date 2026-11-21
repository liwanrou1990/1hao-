"""Coinbase BTC 现货主动买卖监测（只读，无任何下单接口）。

每次运行：从 Coinbase 公开逐笔成交（/products/BTC-USD/trades，side = 挂单方，主动方向取反）增量翻页到上次处理的成交编号，
按 UTC 小时累计主动买入 / 卖出量，计算近 1 / 6 / 24 个完整小时的净主动买入（BTC），并按事先固定的规则给出提醒级别：
  - early（初步买盘）：近 6 个完整小时净买入 ≥ +300 BTC，且 12 小时内未提醒过；
  - confirm（买盘确认进入）：近 24 个完整小时净买入由 ≤ 0 转为 > 0；
  - none：其余情况。
首次运行（或状态丢失）会回补最近 25 小时。状态保存在 data_store/brando_pa/cb_watch/state.json（不入库）。
输出一行 JSON：{"alert": ..., "message": ..., ...}。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data_store" / "brando_pa" / "cb_watch" / "state.json"
PRODUCT = "BTC-USD"
EARLY_6H_BTC = 300.0
EARLY_COOLDOWN_H = 12
KEEP_HOURS = 96


def page(client: httpx.Client, after: str | None):
    params = {"limit": 1000} | ({"after": after} if after else {})
    for k in range(6):
        try:
            r = client.get(f"https://api.exchange.coinbase.com/products/{PRODUCT}/trades", params=params)
            if r.status_code == 429:
                time.sleep(1 + k); continue
            r.raise_for_status(); return r.json(), r.headers.get("cb-after")
        except Exception:
            time.sleep(1 + k)
    raise RuntimeError("Coinbase 接口连续失败")


def collect(state: dict) -> dict:
    now = pd.Timestamp.now(tz="UTC"); last_tid = state.get("last_tid"); floor = (now - pd.Timedelta(hours=25)).floor("h")
    buckets = {k: v for k, v in state.get("hours", {}).items()}; newest = None; after = None; n = 0; stale = False
    with httpx.Client(timeout=30) as c:
        while True:
            data, after = page(c, after)
            if not data:
                break
            for t in data:
                tid = int(t["trade_id"]); ts = pd.Timestamp(t["time"])
                if newest is None or tid > newest:
                    newest = tid
                if (last_tid is not None and tid <= last_tid) or ts < floor:
                    continue
                h = ts.floor("h").strftime("%Y-%m-%dT%H"); b = buckets.setdefault(h, [0.0, 0.0])
                size = float(t["size"])
                if t["side"] == "sell":      # 挂单方卖 → 主动买
                    b[0] += size
                else:
                    b[1] += size
                n += 1
            oldest_tid = min(int(t["trade_id"]) for t in data); oldest_ts = min(pd.Timestamp(t["time"]) for t in data)
            if (last_tid is not None and oldest_tid <= last_tid) or (last_tid is None and oldest_ts < floor) or not after:
                break
            if oldest_ts < floor:            # 状态过旧（上次处理距今超过 25 小时）：不无限回翻，从 25 小时前重新累计
                stale = True; break
            time.sleep(0.11)
    cut = (now - pd.Timedelta(hours=KEEP_HOURS)).strftime("%Y-%m-%dT%H")
    state["hours"] = {k: v for k, v in sorted(buckets.items()) if k >= cut}
    if newest is not None:
        state["last_tid"] = newest
    if last_tid is None or stale:
        state["backfilled_from"] = floor.strftime("%Y-%m-%dT%H")
        state["hours"] = {k: v for k, v in state["hours"].items() if k >= state["backfilled_from"]}
    state["new_trades"] = n
    return state


def summarize(state: dict) -> dict:
    now = pd.Timestamp.now(tz="UTC"); cur = now.floor("h")
    def net(k):
        hrs = [(cur - pd.Timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(1, k + 1)]
        have = [h for h in hrs if h in state["hours"]]
        bf = state.get("backfilled_from")
        complete = len(have) == k and (bf is None or min(hrs) >= bf)
        return sum(state["hours"][h][0] - state["hours"][h][1] for h in have), complete
    n1, _ = net(1); n6, c6 = net(6); n24, c24 = net(24)
    alert = "none"; prev24 = state.get("prev_net24")
    if c24 and n24 > 0 and (prev24 is None or prev24 <= 0):
        alert = "confirm"
    elif c6 and n6 >= EARLY_6H_BTC:
        last_early = state.get("last_early_at")
        if last_early is None or now - pd.Timestamp(last_early) >= pd.Timedelta(hours=EARLY_COOLDOWN_H):
            alert = "early"; state["last_early_at"] = str(now)
    if c24:
        state["prev_net24"] = n24
    msg = {"confirm": f"Coinbase BTC 买盘确认进入：近24小时净主动买入 +{n24:,.0f} BTC（近6小时 {n6:+,.0f}）",
           "early": f"Coinbase BTC 初步买盘：近6小时净主动买入 +{n6:,.0f} BTC（近24小时 {n24:+,.0f}）",
           "none": f"Coinbase BTC 未转为买盘：近1小时 {n1:+,.0f}，近6小时 {n6:+,.0f}，近24小时 {n24:+,.0f} BTC" + ("" if c24 else "（24小时数据未满）")}[alert]
    return {"alert": alert, "message": msg, "net_1h": round(n1, 1), "net_6h": round(n6, 1), "net_24h": round(n24, 1), "complete_6h": c6, "complete_24h": c24,
            "new_trades": state.get("new_trades"), "as_of_utc": str(cur)}


def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        state = collect(state)
    except Exception as e:
        print(json.dumps({"alert": "error", "message": f"Coinbase 数据获取失败：{e}"}, ensure_ascii=False)); sys.exit(1)
    out = summarize(state)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
