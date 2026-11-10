"""OKX 公开每日逐笔成交归档 → 按 UTC 小时聚合（主动买卖额、笔数、大额、OHLC、成交编号连续性）。
归档文件按北京时间（UTC+8）切日：文件 D 覆盖 UTC [D−1 16:00, D 16:00)；这里一律按每笔成交自身的 UTC 时间归入小时。
原始压缩包只在内存中解析，不落盘；每个文件记一行清单（大小、行数、首末成交编号、内部缺口、sha256），可复现。"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from . import LARGE_USDT, SYMS

FIRST_DAY = "2021-09-01"
URL = "https://static.okx.com/cdn/okex/traderecords/trades/daily/{d8}/{sym}-trades-{d}.zip"


def url(sym: str, day: pd.Timestamp) -> str:
    return URL.format(d8=day.strftime("%Y%m%d"), sym=sym, d=day.strftime("%Y-%m-%d"))


def parse_trades(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), usecols=["trade_id", "side", "price", "size", "created_time"], dtype={"trade_id": "int64", "side": "string", "price": "float64", "size": "float64", "created_time": "int64"})
    df["side"] = df["side"].str.lower()
    bad = ~df["side"].isin(["buy", "sell"])
    if bad.any():
        raise ValueError(f"未知的主动方向取值：{df.loc[bad, 'side'].unique()[:5].tolist()}")
    return df.sort_values("trade_id", kind="stable").drop_duplicates("trade_id", keep="first").reset_index(drop=True)      # 归档中有完全重复的行：每笔成交只计一次


def hourly(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    """逐笔 → UTC 小时。side = OKX 主动方（taker）方向。"""
    q = df["price"].to_numpy() * df["size"].to_numpy(); buy = (df["side"] == "buy").to_numpy(); large = q >= LARGE_USDT
    h = pd.to_datetime(df["created_time"], unit="ms", utc=True).dt.floor("h")
    x = pd.DataFrame({"hour": h, "tb": np.where(buy, q, 0.0), "ts": np.where(~buy, q, 0.0), "bb": np.where(buy, df["size"], 0.0), "bs": np.where(~buy, df["size"], 0.0),
                      "nb": buy.astype(int), "ns": (~buy).astype(int), "ltb": np.where(buy & large, q, 0.0), "lts": np.where(~buy & large, q, 0.0), "price": df["price"], "tid": df["trade_id"]})
    g = x.groupby("hour", sort=True)
    out = g[["tb", "ts", "bb", "bs", "nb", "ns", "ltb", "lts"]].sum()
    out["open"] = g["price"].first(); out["high"] = g["price"].max(); out["low"] = g["price"].min(); out["close"] = g["price"].last()
    out["first_tid"] = g["tid"].min(); out["last_tid"] = g["tid"].max(); out["n"] = g["tid"].count()
    out = out.reset_index(); out.insert(0, "symbol", sym); return out


def process(sym: str, day: str, retries: int = 3) -> dict:
    import httpx
    d = pd.Timestamp(day); u = url(sym, d); err = None
    for k in range(retries):
        try:
            r = httpx.get(u, timeout=60)
            if r.status_code == 404:
                return {"symbol": sym, "file_day": day, "status": "missing", "url": u}
            r.raise_for_status(); z = zipfile.ZipFile(io.BytesIO(r.content)); raw = z.read(z.namelist()[0]); df = parse_trades(raw)
            tids = df["trade_id"].to_numpy(); internal_gaps = int((np.diff(tids) > 1).sum()); missing_ids = int((np.diff(tids) - 1).clip(min=0).sum())
            dup = int((np.diff(tids) == 0).sum())
            return {"symbol": sym, "file_day": day, "status": "ok", "url": u, "zip_bytes": len(r.content), "zip_sha256": hashlib.sha256(r.content).hexdigest(), "rows": int(len(df)), "first_tid": int(tids[0]), "last_tid": int(tids[-1]),
                    "internal_gaps": internal_gaps, "missing_ids": missing_ids, "duplicate_ids": dup, "first_ts": int(df["created_time"].min()), "last_ts": int(df["created_time"].max()), "hourly": hourly(df, sym)}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:200]}"; time.sleep(2 * (k + 1))
    return {"symbol": sym, "file_day": day, "status": "error", "url": u, "error": err}


def build(out_dir: Path, start: str = FIRST_DAY, end: str | None = None, syms=SYMS, workers: int = 4, log=print) -> dict:
    """增量构建：已在清单中成功处理的文件跳过。输出 {sym}_hourly.parquet 与 manifest.jsonl。"""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True); man = out_dir / "manifest.jsonl"
    done = {(m["symbol"], m["file_day"]) for m in map(json.loads, man.read_text().splitlines()) if m["status"] in ("ok", "missing")} if man.exists() else set()
    end = end or str((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)).date())
    todo = [(s, str(d.date())) for s in syms for d in pd.date_range(start, end, freq="D") if (s, str(d.date())) not in done]
    parts = {s: [] for s in syms}; n_ok = n_err = 0; t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(process, s, d) for s, d in todo]
        for k, f in enumerate(as_completed(futs)):
            r = f.result(); h = r.pop("hourly", None)
            if h is not None:
                parts[r["symbol"]].append(h); n_ok += 1
            elif r["status"] == "error":
                n_err += 1
            with open(man, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if (k + 1) % 100 == 0:
                log(f"{k + 1}/{len(todo)} files · ok {n_ok} · err {n_err} · {time.time() - t0:.0f}s")
                _flush(out_dir, parts); parts = {s: [] for s in syms}
    _flush(out_dir, parts)
    return {"files_processed": len(todo), "ok": n_ok, "errors": n_err, "seconds": round(time.time() - t0)}


def _flush(out_dir: Path, parts: dict) -> None:
    for s, lst in parts.items():
        if not lst:
            continue
        p = out_dir / f"{s}_hourly.parquet"; new = pd.concat(lst, ignore_index=True)
        if p.exists():
            new = pd.concat([pd.read_parquet(p), new], ignore_index=True)
        new.sort_values(["hour", "first_tid"]).to_parquet(p, index=False)


def repair(out_dir: Path, sym: str) -> dict:
    """事后数据清洗（不改任何条件参数）：首轮清单中含重复编号的文件，以及与相邻文件编号区间重叠的文件对，
    重新下载、合并后按成交编号去重，再替换这些文件覆盖的小时。清单追加 repair 记录，可追溯。"""
    import httpx
    out_dir = Path(out_dir); man = out_dir / "manifest.jsonl"; m = [x for x in map(json.loads, man.read_text().splitlines()) if x["symbol"] == sym and x["status"] == "ok"]
    first = {}
    for x in m:
        first.setdefault(x["file_day"], x)
    days = sorted(first); fix = {d for d in days if first[d].get("duplicate_ids", 0) > 0}
    for a, b in zip(days[:-1], days[1:]):
        if first[b]["first_tid"] <= first[a]["last_tid"]:
            fix |= {a, b}
    groups, cur = [], []
    for d in sorted(fix):
        if cur and (pd.Timestamp(d) - pd.Timestamp(cur[-1])).days > 1:
            groups.append(cur); cur = []
        cur.append(d)
    if cur:
        groups.append(cur)
    h = pd.read_parquet(out_dir / f"{sym}_hourly.parquet"); log = []
    for g in groups:
        dfs = []
        for d in g:
            r = httpx.get(url(sym, pd.Timestamp(d)), timeout=120); r.raise_for_status(); z = zipfile.ZipFile(io.BytesIO(r.content)); dfs.append(pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), usecols=["trade_id", "side", "price", "size", "created_time"]))
        raw = pd.concat(dfs, ignore_index=True); n0 = len(raw); df = parse_trades(raw.to_csv(index=False).encode()); hh = hourly(df, sym)
        lo, hi = pd.Timestamp(g[0], tz="UTC") - pd.Timedelta(hours=8), pd.Timestamp(g[-1], tz="UTC") + pd.Timedelta(hours=16)       # 文件 D 覆盖 UTC [D−1 16:00, D 16:00)
        lo = min(lo, hh["hour"].min()); hi = max(hi, hh["hour"].max() + pd.Timedelta(hours=1))
        h = pd.concat([h[(h["hour"] < lo) | (h["hour"] >= hi)], hh], ignore_index=True)
        rec = {"symbol": sym, "file_day": g[0], "status": "repair", "files": g, "rows_before_dedup": n0, "rows_after_dedup": int(len(df)), "hours_replaced": [str(lo), str(hi)]}; log.append(rec)
        with open(man, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    h.sort_values(["hour", "first_tid"]).to_parquet(out_dir / f"{sym}_hourly.parquet", index=False); return {"groups": len(groups), "log": log}


def load_hourly(out_dir: Path, sym: str) -> pd.DataFrame:
    """合并同一小时的多段（相邻归档文件在 UTC 16:00 交界，不会重叠；此处仍按小时再聚合一次以防万一）。"""
    h = pd.read_parquet(Path(out_dir) / f"{sym}_hourly.parquet").sort_values(["hour", "first_tid"])
    g = h.groupby("hour", sort=True)
    out = g[["tb", "ts", "bb", "bs", "nb", "ns", "ltb", "lts", "n"]].sum()
    out["open"] = g["open"].first(); out["high"] = g["high"].max(); out["low"] = g["low"].min(); out["close"] = g["close"].last(); out["first_tid"] = g["first_tid"].min(); out["last_tid"] = g["last_tid"].max()
    return out.reset_index()


def daily(hourly_df: pd.DataFrame) -> pd.DataFrame:
    h = hourly_df.copy(); h["day"] = h["hour"].dt.floor("D"); g = h.groupby("day", sort=True)
    d = g[["tb", "ts", "bb", "bs", "nb", "ns", "ltb", "lts", "n"]].sum(); d["open"] = g["open"].first(); d["high"] = g["high"].max(); d["low"] = g["low"].min(); d["close"] = g["close"].last()
    d["hours"] = g["hour"].count(); d["first_tid"] = g["first_tid"].min(); d["last_tid"] = g["last_tid"].max()
    d = d.reset_index().rename(columns={"tb": "buy_usdt", "ts": "sell_usdt", "ltb": "large_buy_usdt", "lts": "large_sell_usdt"}).rename(columns={"day": "ts"})
    d["tot"] = d["buy_usdt"] + d["sell_usdt"]; d["net"] = d["buy_usdt"] - d["sell_usdt"]; d["imb"] = d["net"] / d["tot"]; d["cvd"] = d["net"].cumsum(); d["volume"] = d["bb"] + d["bs"]
    d["missing_ids"] = (d["last_tid"] - d["first_tid"] + 1 - d["n"]).astype("int64") + (d["first_tid"] - d["last_tid"].shift(1) - 1).fillna(0).astype("int64")
    return d


def continuity(man_path: Path, sym: str) -> dict:
    """跨文件成交编号连续性：相邻归档文件的首末编号是否衔接；文件缺失；文件内部缺口。"""
    m = [x for x in map(json.loads, Path(man_path).read_text().splitlines()) if x["symbol"] == sym and x["status"] != "repair"]
    ok = sorted([x for x in m if x["status"] == "ok"], key=lambda x: x["file_day"]); cross = []
    for a, b in zip(ok[:-1], ok[1:]):
        if b["first_tid"] != a["last_tid"] + 1:
            cross.append({"between": [a["file_day"], b["file_day"]], "gap_ids": b["first_tid"] - a["last_tid"] - 1})
    return {"files_ok": len(ok), "files_missing": [x["file_day"] for x in m if x["status"] == "missing"], "files_error": [x["file_day"] for x in m if x["status"] == "error"],
            "internal_gap_files": sum(1 for x in ok if x["internal_gaps"] > 0), "internal_missing_ids": sum(x["missing_ids"] for x in ok), "duplicate_ids": sum(x["duplicate_ids"] for x in ok), "cross_file_breaks": cross[:50], "n_cross_file_breaks": len(cross),
            "rows": sum(x["rows"] for x in ok), "zip_bytes": sum(x["zip_bytes"] for x in ok)}


if __name__ == "__main__":
    import sys
    from ..config import own_data_dir
    from . import DATA_SUBDIR
    od = own_data_dir() / DATA_SUBDIR / "okx_archive"; print(build(od, end=sys.argv[1] if len(sys.argv) > 1 else None, workers=int(sys.argv[2]) if len(sys.argv) > 2 else 4))
