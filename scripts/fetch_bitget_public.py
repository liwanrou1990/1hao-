#!/usr/bin/env python3
"""在任意有网络的机器上运行（只依赖 requests；不需要任何密钥），把 Bitget 公开接口的某日数据保存为 CSV，
再复制到项目 data_in/<SYMBOL>/<YYYY-MM-DD>/ 下，用 `bgwq import-dir` 或 `bgwq playbook-daily` 自动导入。

  python scripts/fetch_bitget_public.py --symbols BTCUSDT ETHUSDT SOLUSDT HYPEUSDT --day 2026-10-22 --out data_in
  可选 --depth-seconds 60 ：对“今天”持续采样盘口快照（历史盘口公开接口不提供，只能实时采集或从 Tardis.dev 等第三方导入）

输出：kline.csv（ts_ms,open,high,low,close,volume,amount,usdt_volume）、trades.csv（tradeId,ts_ms,price,size,side）、depth.csv（ts_ms,bids,asks JSON）。
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BASE = "https://api.bitget.com"
NY = ZoneInfo("America/New_York")


def ny_day_bounds_ms(day: str):
    d = datetime.fromisoformat(day).replace(tzinfo=NY)
    s = d.astimezone(timezone.utc)
    return int(s.timestamp() * 1000), int((s + timedelta(days=1)).timestamp() * 1000)


def get(path, params):
    for i in range(5):
        r = requests.get(BASE + path, params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(1 + i); continue
        r.raise_for_status()
        j = r.json()
        if str(j.get("code")) != "00000":
            raise RuntimeError(j)
        return j["data"]
    raise RuntimeError("rate limited")


def fetch_kline(sym, s_ms, e_ms):
    rows, end = [], e_ms
    while True:
        page = get("/api/v2/spot/market/history-candles", {"symbol": sym, "granularity": "1min", "endTime": str(end), "limit": "200"})
        if not page: break
        rows += page
        oldest = min(int(r[0]) for r in page)
        if oldest <= s_ms or len(page) < 200: break
        end = oldest - 1
        time.sleep(0.06)
    rows = [r for r in rows if s_ms <= int(r[0]) < e_ms]
    return sorted({int(r[0]): r for r in rows}.values(), key=lambda r: int(r[0]))


def fetch_trades(sym, s_ms, e_ms):
    rows, end = [], e_ms
    for _ in range(2000):
        page = get("/api/v2/spot/market/fills-history", {"symbol": sym, "startTime": str(s_ms), "endTime": str(end), "limit": "1000"})
        if not page: break
        rows += page
        oldest = min(int(r["ts"]) for r in page)
        if oldest <= s_ms or len(page) < 1000: break
        end = oldest - 1
        time.sleep(0.06)
    return sorted({r["tradeId"]: r for r in rows}.values(), key=lambda r: int(r["ts"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--day", required=True, help="America/New_York 自然日 YYYY-MM-DD")
    ap.add_argument("--out", default="data_in")
    ap.add_argument("--depth-seconds", type=int, default=0, help=">0 时对当前盘口持续采样（仅当天实时有效）")
    a = ap.parse_args()
    s_ms, e_ms = ny_day_bounds_ms(a.day)
    for sym in a.symbols:
        short = sym.replace("USDT", "")
        d = os.path.join(a.out, short, a.day); os.makedirs(d, exist_ok=True)
        k = fetch_kline(sym, s_ms, e_ms)
        with open(os.path.join(d, "kline.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["ts_ms", "open", "high", "low", "close", "volume", "amount", "usdt_volume"]); w.writerows(k)
        print(sym, "kline", len(k), flush=True)
        t = fetch_trades(sym, s_ms, e_ms)
        with open(os.path.join(d, "trades.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["tradeId", "ts_ms", "price", "size", "side"]); w.writerows([[r["tradeId"], r["ts"], r["price"], r["size"], r["side"]] for r in t])
        print(sym, "trades", len(t), flush=True)
        if a.depth_seconds > 0:
            with open(os.path.join(d, "depth.csv"), "a", newline="") as f:
                w = csv.writer(f)
                if f.tell() == 0: w.writerow(["ts_ms", "bids", "asks"])
                end_at = e_ms / 1000
                while time.time() < end_at:
                    ob = get("/api/v2/spot/market/orderbook", {"symbol": sym, "type": "step0", "limit": "150"})
                    w.writerow([ob.get("ts"), json.dumps(ob["bids"]), json.dumps(ob["asks"])]); f.flush()
                    time.sleep(a.depth_seconds)


if __name__ == "__main__":
    main()
