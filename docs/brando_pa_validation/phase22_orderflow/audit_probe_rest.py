import time, json, httpx
C = httpx.Client(timeout=15, headers={"User-Agent": "research-probe/0.1"})
def get(name, url, params=None, show=None):
    t0 = time.time()
    try:
        r = C.get(url, params=params); dt = (time.time() - t0) * 1000
        hdr = {k: v for k, v in r.headers.items() if any(x in k.lower() for x in ("ratelimit", "limit", "x-mbx", "cb-", "retry", "date", "content-length"))}
        body = r.text[:300] if r.status_code != 200 else None
        out = {"status": r.status_code, "ms": round(dt), "headers": hdr}
        if r.status_code == 200 and show:
            out["sample"] = show(r)
        else:
            out["body"] = body
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
    print(name, json.dumps(out, ensure_ascii=False, default=str)[:900]); print()
now = lambda: time.time() * 1000
# OKX
get("OKX trades", "https://www.okx.com/api/v5/market/trades", {"instId": "BTC-USDT", "limit": 500}, lambda r: (lambda d: {"n": len(d), "first": d[0], "age_ms_newest": now() - int(d[0]["ts"]), "span_s": (int(d[0]["ts"]) - int(d[-1]["ts"])) / 1000})(r.json()["data"]))
get("OKX history-trades", "https://www.okx.com/api/v5/market/history-trades", {"instId": "BTC-USDT", "limit": 100, "type": "2", "after": str(int(now() - 80 * 86400 * 1000))}, lambda r: (lambda d: {"n": len(d), "first": d[0] if d else None})(r.json()["data"]))
get("OKX books 400", "https://www.okx.com/api/v5/market/books", {"instId": "BTC-USDT", "sz": 400}, lambda r: (lambda d: {"bids": len(d["bids"]), "asks": len(d["asks"]), "best": [d["bids"][0], d["asks"][0]], "ts_age_ms": now() - int(d["ts"])})(r.json()["data"][0]))
get("OKX books-full", "https://www.okx.com/api/v5/market/books-full", {"instId": "BTC-USDT", "sz": 5000}, lambda r: (lambda d: {"bids": len(d["bids"]), "asks": len(d["asks"])})(r.json()["data"][0]))
get("OKX rubik taker-volume SPOT 1D", "https://www.okx.com/api/v5/rubik/stat/taker-volume", {"ccy": "BTC", "instType": "SPOT", "period": "1D"}, lambda r: (lambda d: {"n": len(d), "newest": d[0], "oldest": d[-1]})(r.json()["data"]))
get("OKX public time", "https://www.okx.com/api/v5/public/time", None, lambda r: {"server_minus_local_ms": int(r.json()["data"][0]["ts"]) - now()})
get("OKX market-data-history", "https://www.okx.com/api/v5/public/market-data-history", {"module": "1", "instType": "SPOT", "instIdList": "BTC-USDT", "dateAggrType": "daily", "begin": "1704067200000", "end": "1704153600000"}, lambda r: r.json())
get("OKX static archive", "https://static.okx.com/cdn/okex/traderecords/trades/daily/20240101/BTC-USDT-trades-2024-01-01.zip", None, lambda r: {"bytes": len(r.content)})
# Binance
get("Binance api ping", "https://api.binance.com/api/v3/ping")
get("Binance aggTrades", "https://api.binance.com/api/v3/aggTrades", {"symbol": "BTCUSDT", "limit": 5}, lambda r: r.json()[:1])
get("Binance data.binance.vision HEAD-ish", "https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-01-01.zip.CHECKSUM", None, lambda r: r.text[:120])
# Coinbase
get("Coinbase trades BTC-USD", "https://api.exchange.coinbase.com/products/BTC-USD/trades", {"limit": 1000}, lambda r: (lambda d: {"n": len(d), "first": d[0], "cb_after": r.headers.get("cb-after")})(r.json()))
get("Coinbase book L2", "https://api.exchange.coinbase.com/products/BTC-USD/book", {"level": 2}, lambda r: (lambda d: {"bids": len(d["bids"]), "asks": len(d["asks"]), "best": [d["bids"][0], d["asks"][0]], "time": d.get("time")})(r.json()))
get("Coinbase BTC-USDT trades", "https://api.exchange.coinbase.com/products/BTC-USDT/trades", {"limit": 5}, lambda r: r.json()[:1])
get("Coinbase time", "https://api.exchange.coinbase.com/time", None, lambda r: {"server_minus_local_ms": r.json()["epoch"] * 1000 - now()})
