import asyncio, json, time, zlib, websockets
R = {}
def crc_okx(bids, asks):
    parts = []
    for i in range(25):
        if i < len(bids): parts += [bids[i][0], bids[i][1]]
        if i < len(asks): parts += [asks[i][0], asks[i][1]]
    v = zlib.crc32(":".join(parts).encode()) & 0xffffffff
    return v - (1 << 32) if v >= (1 << 31) else v
async def okx(sec):
    st = {"trades": 0, "trade_msgs": 0, "book_msgs": 0, "book_bytes": 0, "trade_bytes": 0, "lat_ms": [], "cs_ok": 0, "cs_bad": 0, "tid_gaps": 0}
    book = {"bids": {}, "asks": {}}; last_tid = {}
    async with websockets.connect("wss://ws.okx.com:8443/ws/v5/public", max_size=2**24, ping_interval=20) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "trades", "instId": "BTC-USDT"}, {"channel": "trades", "instId": "ETH-USDT"}, {"channel": "books", "instId": "BTC-USDT"}]}))
        t_end = time.time() + sec
        while time.time() < t_end:
            m = await asyncio.wait_for(ws.recv(), 30); now = time.time() * 1000; j = json.loads(m)
            ch = j.get("arg", {}).get("channel")
            if ch == "trades" and "data" in j:
                st["trade_msgs"] += 1; st["trade_bytes"] += len(m)
                for t in j["data"]:
                    st["trades"] += 1; st["lat_ms"].append(now - int(t["ts"])); tid = int(t["tradeId"]); k = t["instId"]
                    if k in last_tid and tid != last_tid[k] + 1: st["tid_gaps"] += 1
                    last_tid[k] = tid
            elif ch == "books" and "data" in j:
                st["book_msgs"] += 1; st["book_bytes"] += len(m); d = j["data"][0]
                if j.get("action") == "snapshot": book = {"bids": {}, "asks": {}}
                for side in ("bids", "asks"):
                    for px, sz, *_ in d[side]:
                        if float(sz) == 0: book[side].pop(px, None)
                        else: book[side][px] = sz
                b = sorted(book["bids"].items(), key=lambda x: -float(x[0])); a = sorted(book["asks"].items(), key=lambda x: float(x[0]))
                if crc_okx(b, a) == d.get("checksum"): st["cs_ok"] += 1
                else: st["cs_bad"] += 1
    L = sorted(st.pop("lat_ms")); st["lat_p50_ms"] = L[len(L)//2] if L else None; st["lat_p95_ms"] = L[int(len(L)*.95)] if L else None; R["okx"] = st
async def cb(sec):
    st = {"matches": 0, "bytes": 0, "lat_ms": [], "seq_gaps": 0}; last = {}
    async with websockets.connect("wss://ws-feed.exchange.coinbase.com", max_size=2**24) as ws:
        await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD", "ETH-USD"], "channels": ["matches", "heartbeat"]}))
        t_end = time.time() + sec
        while time.time() < t_end:
            m = await asyncio.wait_for(ws.recv(), 30); j = json.loads(m); st["bytes"] += len(m)
            if j.get("type") in ("match", "last_match"):
                st["matches"] += 1; import datetime as dt
                st["lat_ms"].append(time.time() * 1000 - dt.datetime.fromisoformat(j["time"].replace("Z", "+00:00")).timestamp() * 1000)
                p = j["product_id"]; tid = int(j["trade_id"])
                if p in last and tid != last[p] + 1: st["seq_gaps"] += 1
                last[p] = tid
            if j.get("type") == "error": st["error"] = j
    L = sorted(st.pop("lat_ms")); st["lat_p50_ms"] = L[len(L)//2] if L else None; st["lat_p95_ms"] = L[int(len(L)*.95)] if L else None; R["coinbase"] = st
async def main(sec=60):
    await asyncio.gather(okx(sec), cb(sec), return_exceptions=False)
try:
    asyncio.run(main())
except Exception as e:
    R["error"] = f"{type(e).__name__}: {e}"
print(json.dumps(R, indent=1))
