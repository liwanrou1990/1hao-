"""Continuous, deployable DEX arbitrage monitor with persistence.

Upgrades single scans into a block-triggered continuous loop:
- Per chain, poll block height; only (re)scan when a NEW block appears
  (incremental trigger), so idle blocks cost one cheap block_number call.
- Persist everything to SQLite (blocks, scans, opportunities, failures) so a
  24-72h run survives restarts and RESUMES from the last observed block.
- Retry with exponential backoff, rate limiting, and error recovery: a failed
  RPC/scan is logged to `failures` and the loop continues.
- Record per-round wall time, block-number RPC latency, block height and data
  staleness. Profit is judged only when the scan completed on a fresh block
  with complete quotes.

Three validation levels are tracked per opportunity:
  1 = quote_positive  (gross spread > 0 from executable quotes)
  2 = fork_positive   (full multi-leg fork net > 0; set by the fork test)
  3 = mainnet_realized(NEVER set here — no real execution)

No wallet, no real execution. Costs are never lowered to fabricate profit.
"""
from __future__ import annotations
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .quotes import block_number
from .scan import scan


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blocks(
  chain TEXT, block INTEGER, observed_at TEXT, PRIMARY KEY(chain, block));
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, wall_ms REAL,
  blocknum_rpc_ms REAL, chains TEXT, n_pairs INTEGER,
  n_positive_spread INTEGER, n_positive_net INTEGER, eth_price REAL);
CREATE TABLE IF NOT EXISTS opportunities(
  scan_id INTEGER, chain TEXT, block INTEGER, pair TEXT, size_usd REAL,
  gross_usd REAL, cost_usd REAL, net_usd REAL, level INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS failures(ts TEXT, context TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS chain_rounds(
  scan_id INTEGER, chain TEXT, trigger_block INTEGER, tip_after INTEGER,
  staleness_blocks INTEGER, rpc_url TEXT, ts TEXT);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path):
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    # additive migration for DBs created before chain_rounds existed
    con.execute("CREATE TABLE IF NOT EXISTS chain_rounds("
                "scan_id INTEGER, chain TEXT, trigger_block INTEGER, tip_after INTEGER,"
                "staleness_blocks INTEGER, rpc_url TEXT, ts TEXT)")
    con.commit()
    return con


def _rpc_list(cfg: dict, chain: str) -> list:
    """Ordered RPC endpoints for a chain: config primary + config fallbacks +
    env override (CHAIN_RPC_FALLBACK, e.g. BASE_RPC_FALLBACK). De-duplicated.
    Operators supply real fallbacks at deploy time; the loop fails over on a
    dead primary and keeps using the healthy one."""
    ccfg = cfg["chains"][chain]
    urls = [ccfg.get("rpc")]
    urls += list(ccfg.get("rpc_fallbacks", []) or [])
    env = os.environ.get(f"{chain.upper()}_RPC_FALLBACK")
    if env:
        urls += [u.strip() for u in env.split(",") if u.strip()]
    seen = set(); out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out


def _last_block(con, chain):
    row = con.execute("SELECT MAX(block) FROM blocks WHERE chain=?", (chain,)).fetchone()
    return row[0] if row and row[0] is not None else None


def _fast_round(con, cfg, trig, bn_ms_total, size_usd, tier2_bar,
                tier2_last=None, tier2_min_interval_s=60.0) -> "int|None":
    """One fast round: per advanced chain, Tier-1 batched discovery (all venues,
    2 eth_calls) pinned to the trigger block; record exact round-trip gross per
    pair. Run the slow precise Tier-2 full scan (full cost model + all sizes)
    ONLY when a pair's exact gross reaches `tier2_bar` (default 0 bps): since any
    profitable trade needs net>0 => gross>0, a gross>0 trigger never misses a
    candidate yet almost never fires in an efficient market, so the loop stays
    fast. Tier-2 is additionally throttled to at most once per chain per
    `tier2_min_interval_s` so a persistently near-breakeven pair can't stall
    coverage. Returns the scan_id, or None if every chain was INSUFFICIENT_DATA."""
    if tier2_last is None:
        tier2_last = {}
    from . import perf
    from .scan import scan_chain, _usd_price_map
    from ..market.sources import DefiLlamaSource
    fee_tiers = cfg.get("fee_tiers", [100, 500, 3000])
    tick_spacings = cfg.get("tick_spacings", [1, 50, 100, 200, 2000])
    cur = con.execute("INSERT INTO scans(started_at,wall_ms,blocknum_rpc_ms,chains,"
                      "n_pairs,n_positive_spread,n_positive_net,eth_price) VALUES(?,?,?,?,?,?,?,?)",
                      (_now(), 0.0, bn_ms_total, ",".join(trig.keys()), 0, 0, 0, None))
    scan_id = cur.lastrowid
    total_wall = 0.0
    n_pairs = n_pos_spread = n_pos_net = 0
    any_ok = False
    for chain, (tb, rpc) in trig.items():
        ccfg = cfg["chains"][chain]
        pairs = [tuple(p) for p in cfg.get("pairs", {}).get(chain, [])]
        t1 = perf.tier1_scan(chain, ccfg, pairs, fee_tiers, tick_spacings, size_usd, block=tb)
        total_wall += t1.get("latency_ms", 0.0)
        if t1.get("data_status") != "OK":
            con.execute("INSERT INTO failures VALUES(?,?,?)",
                        (_now(), f"tier1:{chain}", str(t1.get("error"))[:200]))
            continue
        any_ok = True
        for pair, v in t1["best_roundtrip_by_pair"].items():
            n_pairs += 1
            gross_usd = v["gross_bps"] / 1e4 * size_usd
            if gross_usd > 0:
                n_pos_spread += 1
            con.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (scan_id, chain, tb, pair, size_usd, round(gross_usd, 4),
                         None, None, 0, _now()))  # level 0 = Tier-1 gross only
        # Tier-2 precise (full cost + all sizes) ONLY when exact gross clears the
        # bar (default 0: a profitable trade needs gross>0), and at most once per
        # chain per interval so a near-breakeven pair can't stall coverage.
        near = [c for c in t1["candidates"]
                if c.get("gross_bps") is not None and c["gross_bps"] > tier2_bar]
        due = (time.monotonic() - tier2_last.get(chain, -1e9)) >= tier2_min_interval_s
        if near and due:
            tier2_last[chain] = time.monotonic()
            eth, _ = _usd_price_map(DefiLlamaSource())
            if eth is not None:
                rows = scan_chain(chain, ccfg, pairs, fee_tiers, tick_spacings,
                                  cfg.get("capital_sizes", [1000]), cfg.get("costs", {}),
                                  eth, float(cfg.get("min_net_usd", 1)))
                for r in rows:
                    for s in r.get("sizes", []):
                        if s.get("status") == "NO_QUOTE":
                            continue
                        net = s.get("net_usd")
                        if net is not None and net > 0:
                            n_pos_net += 1
                        con.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (scan_id, chain, tb, r["pair"], s.get("size_usd"),
                                     s.get("gross_usd"), s.get("total_cost_usd"), net,
                                     2 if (net or 0) > 0 else 1, _now()))
    if not any_ok:
        con.execute("DELETE FROM scans WHERE id=?", (scan_id,))
        con.commit()
        return None
    con.execute("UPDATE scans SET wall_ms=?,n_pairs=?,n_positive_spread=?,n_positive_net=? WHERE id=?",
                (round(total_wall, 1), n_pairs, n_pos_spread, n_pos_net, scan_id))
    con.commit()
    return scan_id


def run_continuous(cfg: dict, db_path, max_rounds: int = 0, max_seconds: int = 0,
                   poll_s: float = 10.0, rate_limit_s: float = 2.0,
                   backoff=(1, 2, 4, 8), fast: bool = False, size_usd: float = 1000.0,
                   tier2_gross_bar_bps: float = 0.0, tier2_min_interval_s: float = 60.0) -> dict:
    """Block-triggered continuous loop. max_rounds/max_seconds=0 means unbounded
    (for server deployment); set them to bound a validation run.

    fast=True uses the Tier-1 batched candidate discovery (2 Multicall eth_calls
    per chain, all venue types) every new block — fast enough to keep up with
    L2 block cadence — and only runs the slow precise Tier-2 full scan when a
    pair's exact round-trip gross reaches `tier2_gross_bar_bps` of breakeven
    (so a genuinely near-profitable pair gets the full cost model + fork, while
    the common deeply-negative case stays cheap). fast=False keeps the original
    full-scan-every-block behavior (the baseline, kept for comparison)."""
    con = _connect(db_path)
    chains = list(cfg.get("chains", {}).keys())
    start = time.monotonic()
    rounds = 0
    scans_done = 0
    tier2_last = {}  # chain -> monotonic ts of last Tier-2 fire (throttle)
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('run_started',?)", (_now(),))
    con.commit()

    def _bn_failover(chain):
        """Try each RPC (primary then fallbacks) with backoff. On success via a
        non-primary endpoint, PROMOTE it into cfg so the scan uses the healthy
        one too. Returns (block, latency_ms, rpc_url) or (None, None, None)."""
        urls = _rpc_list(cfg, chain)
        for idx, rpc in enumerate(urls):
            for w in [0] + list(backoff):
                if w:
                    time.sleep(w)
                t0 = time.monotonic()
                bn = block_number(rpc)
                lat = (time.monotonic() - t0) * 1000
                if bn is not None:
                    if idx > 0 and cfg["chains"][chain].get("rpc") != rpc:
                        con.execute("INSERT INTO failures VALUES(?,?,?)",
                                    (_now(), f"rpc_failover:{chain}",
                                     f"primary dead; switched to fallback #{idx}"))
                        con.commit()
                        cfg["chains"][chain]["rpc"] = rpc  # promote healthy endpoint
                    return bn, lat, rpc
        return None, None, None

    try:
        while True:
            if max_rounds and rounds >= max_rounds:
                break
            if max_seconds and (time.monotonic() - start) >= max_seconds:
                break
            rounds += 1
            # 1) incremental trigger: has any chain advanced?
            advanced = False
            bn_ms_total = 0.0
            trig = {}  # chain -> (trigger_block, rpc_url) for advanced chains
            for chain in chains:
                bn, lat, rpc = _bn_failover(chain)
                if bn is None:
                    con.execute("INSERT INTO failures VALUES(?,?,?)",
                                (_now(), f"block_number:{chain}", "unreachable after retries+failover"))
                    con.commit()
                    continue
                bn_ms_total += (lat or 0)
                last = _last_block(con, chain)
                if last is None or bn > last:
                    advanced = True
                    trig[chain] = (bn, rpc)
                    con.execute("INSERT OR IGNORE INTO blocks VALUES(?,?,?)", (chain, bn, _now()))
            con.commit()
            if not advanced:
                time.sleep(poll_s)
                continue

            # 2) scan on the fresh block(s)
            if fast:
                scan_id = _fast_round(con, cfg, trig, bn_ms_total, size_usd,
                                      tier2_gross_bar_bps, tier2_last, tier2_min_interval_s)
                if scan_id is None:          # every chain INSUFFICIENT_DATA -> no signal
                    time.sleep(rate_limit_s)
                    continue
                scans_done += 1
            else:
                t0 = time.monotonic()
                try:
                    rep = scan(cfg)
                except Exception as e:
                    con.execute("INSERT INTO failures VALUES(?,?,?)", (_now(), "scan", repr(e)[:200]))
                    con.commit()
                    time.sleep(rate_limit_s)
                    continue
                wall = (time.monotonic() - t0) * 1000
                scans_done += 1
                if rep.get("status") != "OK":
                    con.execute("INSERT INTO failures VALUES(?,?,?)",
                                (_now(), "scan_status", rep.get("reason", "")[:200]))
                    con.commit()
                    time.sleep(rate_limit_s)
                    continue

                n_pairs = 0; n_pos_spread = 0; n_pos_net = 0
                cur = con.execute("INSERT INTO scans(started_at,wall_ms,blocknum_rpc_ms,chains,"
                                  "n_pairs,n_positive_spread,n_positive_net,eth_price) "
                                  "VALUES(?,?,?,?,?,?,?,?)",
                                  (_now(), wall, bn_ms_total, ",".join(chains), 0, 0, 0,
                                   rep.get("eth_price_usd")))
                scan_id = cur.lastrowid
                for r in rep.get("same_chain", []):
                    for s in r.get("sizes", []):
                        if s.get("status") == "NO_QUOTE":
                            continue
                        n_pairs += 1
                        gross = s.get("gross_usd"); net = s.get("net_usd")
                        if gross is not None and gross > 0:
                            n_pos_spread += 1
                        level = 1 if (gross is not None and gross > 0) else 0
                        if net is not None and net > 0:
                            n_pos_net += 1
                        con.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (scan_id, r["chain"], r.get("block"), r["pair"],
                                     s.get("size_usd"), gross, s.get("total_cost_usd"), net,
                                     level, _now()))
                con.execute("UPDATE scans SET n_pairs=?,n_positive_spread=?,n_positive_net=? WHERE id=?",
                            (n_pairs, n_pos_spread, n_pos_net, scan_id))
            # 3) per-chain quote staleness: how many blocks the chain advanced
            # DURING the scan (tip_after - trigger_block) = how stale the quotes
            # are by the time results land. Real measurement, one extra bn read.
            for chain, (tb, rpc) in trig.items():
                tip_after = block_number(cfg["chains"][chain]["rpc"])
                stale = (tip_after - tb) if (tip_after is not None and tb is not None) else None
                con.execute("INSERT INTO chain_rounds VALUES(?,?,?,?,?,?,?)",
                            (scan_id, chain, tb, tip_after, stale, rpc, _now()))
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('run_last',?)", (_now(),))
            con.commit()
            time.sleep(rate_limit_s)
    finally:
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('run_ended',?)", (_now(),))
        con.commit()
        con.close()
    return {"rounds": rounds, "scans": scans_done,
            "elapsed_s": round(time.monotonic() - start, 1), "db": str(db_path)}


def health_check(cfg: dict, db_path, max_scan_age_s: float = 300.0) -> dict:
    """Process/RPC/DB health for monitoring & alerting. Checks each chain's RPC
    (with failover), DB writability, and recency of the last scan. Returns a
    dict with an overall `healthy` flag and per-check detail; the CLI maps it to
    an exit code so systemd/cron/an external alerter can act on it."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1) per-chain RPC reachability (primary + fallbacks)
    for chain in cfg.get("chains", {}):
        urls = _rpc_list(cfg, chain)
        reached = None; used = None
        for u in urls:
            bn = block_number(u)
            if bn is not None:
                reached = bn; used = u; break
        add(f"rpc:{chain}", reached is not None,
            {"block": reached, "endpoint_used": used, "endpoints_tried": len(urls)})

    # 2) DB writable + last-scan recency
    try:
        con = _connect(db_path)
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('health_probe',?)", (_now(),))
        con.commit()
        meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
        n_scans = con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        con.close()
        add("db_writable", True, {"path": str(db_path), "scans": n_scans})
        last = meta.get("run_last")
        age = None
        if last:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            except Exception:
                age = None
        # recency only meaningful once a run has recorded scans
        fresh = (age is not None and age <= max_scan_age_s) if n_scans else True
        add("scan_recency", fresh,
            {"run_last": last, "age_s": round(age, 1) if age is not None else None,
             "max_scan_age_s": max_scan_age_s,
             "note": "无扫描记录时视为通过（尚未开始）" if not n_scans else ""})
    except Exception as e:
        add("db_writable", False, {"error": repr(e)[:200]})

    healthy = all(c["ok"] for c in checks)
    return {"generated_at": _now(), "healthy": healthy, "checks": checks,
            "note": ("健康检查供 systemd/cron/外部告警调用；healthy=False 时 CLI 以非零退出码返回。"
                     "RPC 检查含失败切换；scan_recency 在无扫描记录时视为通过。")}


def acceptance_from_db(db_path) -> dict:
    """Build the acceptance report from persisted data only."""
    con = _connect(db_path)
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    blocks = con.execute("SELECT COUNT(*), COUNT(DISTINCT chain) FROM blocks").fetchone()
    n_scans = con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    agg = con.execute("SELECT COALESCE(SUM(n_positive_spread),0), COALESCE(SUM(n_positive_net),0),"
                      "COALESCE(AVG(wall_ms),0), COALESCE(AVG(blocknum_rpc_ms),0) FROM scans").fetchone()
    n_fail = con.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    fork_positive = con.execute("SELECT COUNT(*) FROM opportunities WHERE level>=2").fetchone()[0]
    mainnet_realized = con.execute("SELECT COUNT(*) FROM opportunities WHERE level>=3").fetchone()[0]
    # per-pair best/worst net across the run, by size
    rows = con.execute("SELECT chain,pair,size_usd,MAX(net_usd),MIN(net_usd),AVG(net_usd),COUNT(*) "
                       "FROM opportunities GROUP BY chain,pair,size_usd ORDER BY MAX(net_usd) DESC").fetchall()
    per_path = [{"chain": r[0], "pair": r[1], "size_usd": r[2], "max_net": r[3],
                 "min_net": r[4], "avg_net": round(r[5], 4) if r[5] is not None else None,
                 "n": r[6]} for r in rows]
    # opportunity lifetime: consecutive scans with positive net per (chain,pair,size)
    life = _lifetimes(con)
    per_chain = _per_chain_stats(con)
    # observation span
    span_s = None
    if meta.get("run_started") and meta.get("run_last"):
        try:
            a = datetime.fromisoformat(meta["run_started"]); b = datetime.fromisoformat(meta["run_last"])
            span_s = (b - a).total_seconds()
        except Exception:
            span_s = None
    con.close()
    n_pos_net = agg[1]
    return {
        "run_started": meta.get("run_started"), "run_last": meta.get("run_last"),
        "run_ended": meta.get("run_ended"),
        "continuous_seconds_observed": span_s,
        "blocks_observed": blocks[0], "chains": blocks[1],
        "valid_scans": n_scans,
        "positive_spread_count": agg[0], "positive_net_count": n_pos_net,
        "fork_verified_count": fork_positive, "mainnet_realized_count": mainnet_realized,
        "avg_scan_wall_ms": round(agg[2], 1), "avg_blocknum_rpc_ms": round(agg[3], 1),
        "failures": n_fail,
        "failure_rate": round(n_fail / max(1, n_scans + n_fail), 4),
        "opportunity_lifetime": life,
        "per_chain_stats": per_chain,
        "per_path_net_by_size": per_path,
        "verdict": "NO_TRADE" if n_pos_net == 0 else "HAS_POSITIVE_NET",
        "note": ("连续时长/区块数/扫描次数均来自持久化 DB 的真实记录；positive_net=扣全部成本后净>0；"
                 "fork_verified 需完整多腿 fork 通过；mainnet_realized 永不在此置位（不执行真实交易）。"
                 "若 continuous_seconds_observed 很短，说明是短验证跑，不等于 24-72h 连续监控——"
                 "连续运行请按 deploy/ 部署到常驻服务器。"),
    }


def _per_chain_stats(con):
    """Per-chain scan coverage and quote staleness (真实记录推导).
    - missed_block_ratio: of all blocks that existed in the observed window
      [min_block, max_block], the fraction we did NOT scan (speed vs block
      cadence). High ratio = scan can't keep up with block time.
    - staleness_blocks: blocks the chain advanced DURING a scan (tip_after -
      trigger_block); how stale the quotes are by the time results land."""
    out = []
    chains = [r[0] for r in con.execute("SELECT DISTINCT chain FROM blocks").fetchall()]
    for chain in chains:
        mn, mx, nrec = con.execute(
            "SELECT MIN(block), MAX(block), COUNT(DISTINCT block) FROM blocks WHERE chain=?",
            (chain,)).fetchone()
        span = (mx - mn + 1) if (mn is not None and mx is not None) else 0
        missed = max(0, span - nrec) if span else 0
        missed_ratio = round(missed / span, 4) if span else None
        st = con.execute("SELECT AVG(staleness_blocks), MAX(staleness_blocks), COUNT(*) "
                         "FROM chain_rounds WHERE chain=? AND staleness_blocks IS NOT NULL",
                         (chain,)).fetchone()
        avg_wall = con.execute(
            "SELECT AVG(wall_ms) FROM scans WHERE id IN "
            "(SELECT scan_id FROM chain_rounds WHERE chain=?)", (chain,)).fetchone()[0]
        out.append({
            "chain": chain, "window_block_span": span, "scanned_blocks": nrec,
            "missed_blocks": missed, "missed_block_ratio": missed_ratio,
            "avg_staleness_blocks": round(st[0], 2) if st[0] is not None else None,
            "max_staleness_blocks": st[1], "staleness_samples": st[2],
            "avg_compute_ms": round(avg_wall, 1) if avg_wall is not None else None,
            "note": ("missed_block_ratio 高=扫描速度跟不上该链出块节奏；staleness_blocks=扫描"
                     "期间链前进的区块数（报价过期程度）。缺 tip 读数时为 None，不臆测。")})
    return out


def _lifetimes(con):
    """Max consecutive scans (ordered) with net>0 per (chain,pair,size)."""
    keys = con.execute("SELECT DISTINCT chain,pair,size_usd FROM opportunities").fetchall()
    out = []
    for (chain, pair, size) in keys:
        seq = con.execute("SELECT net_usd FROM opportunities WHERE chain=? AND pair=? AND size_usd=? "
                          "ORDER BY scan_id", (chain, pair, size)).fetchall()
        best = cur = 0
        for (net,) in seq:
            if net is not None and net > 0:
                cur += 1; best = max(best, cur)
            else:
                cur = 0
        out.append({"chain": chain, "pair": pair, "size_usd": size,
                    "max_consecutive_positive_scans": best, "samples": len(seq)})
    return out


def acceptance_markdown(rep: dict) -> str:
    L = ["# DEX 连续监控验收报告", "",
         "> 数据全部来自持久化 SQLite 的真实记录。不执行真实交易；无正净收益则 NO_TRADE。", ""]
    L += [f"- 连续监控真实时长: **{rep['continuous_seconds_observed']} 秒**"
          f"（起 {rep['run_started']} 至 {rep['run_last']}）",
          f"- 已观察区块数: {rep['blocks_observed']}（{rep['chains']} 条链）",
          f"- 有效扫描次数: {rep['valid_scans']}",
          f"- 正价差出现次数(quote_positive): {rep['positive_spread_count']}",
          f"- 扣成本后正收益次数(net>0): {rep['positive_net_count']}",
          f"- Fork 完整验证成功次数: {rep['fork_verified_count']}",
          f"- 主网实际成交次数: {rep['mainnet_realized_count']}（设计上恒为 0，不执行真实交易）",
          f"- 平均扫描耗时: {rep['avg_scan_wall_ms']} ms；平均区块号 RPC 延迟: {rep['avg_blocknum_rpc_ms']} ms",
          f"- 失败次数/失败率: {rep['failures']} / {rep['failure_rate']}",
          f"- **判定: {rep['verdict']}**", "",
          "## 机会持续时间（连续正净收益扫描数）", "",
          "| 链 | 交易对 | 本金USD | 最大连续正收益扫描 | 样本 |",
          "|----|--------|---------|--------------------|------|"]
    for lf in rep["opportunity_lifetime"]:
        L.append(f"| {lf['chain']} | {lf['pair']} | {lf['size_usd']:,} | "
                 f"{lf['max_consecutive_positive_scans']} | {lf['samples']} |")
    L += ["", "## 各链扫描覆盖与报价时效（真实记录推导）", "",
          "| 链 | 窗口区块跨度 | 已扫描区块 | 漏扫区块 | 漏块率 | 平均过期(区块) | 最大过期 | 平均计算(ms) |",
          "|----|--------------|------------|----------|--------|----------------|----------|--------------|"]
    for c in rep.get("per_chain_stats", []):
        L.append(f"| {c['chain']} | {c['window_block_span']} | {c['scanned_blocks']} | "
                 f"{c['missed_blocks']} | {c['missed_block_ratio']} | {c['avg_staleness_blocks']} | "
                 f"{c['max_staleness_blocks']} | {c['avg_compute_ms']} |")
    L += ["", "> 漏块率高=单次扫描跟不上该链出块节奏（应收窄 venue/pair/size 或靠 Tier-1 批量提速）；"
          "过期区块数=扫描期间链前进的区块数。两者均由持久化记录推导，非估计。",
          "", "## 各路径净收益与容量（按本金）", "",
          "| 链 | 交易对 | 本金USD | 最大净 | 最小净 | 平均净 | 样本 |",
          "|----|--------|---------|--------|--------|--------|------|"]
    for p in rep["per_path_net_by_size"]:
        L.append(f"| {p['chain']} | {p['pair']} | {p['size_usd']:,} | {p['max_net']} | "
                 f"{p['min_net']} | {p['avg_net']} | {p['n']} |")
    L += ["", f"> {rep['note']}"]
    if rep["verdict"] == "NO_TRADE":
        L += ["", "## 结论", "",
              "在已记录的连续监控窗口内，未出现扣除全部成本后为正的可执行套利机会 → **NO_TRADE**。"
              "不制造盈利结论。要积累 24–72h，请按 deploy/ 部署常驻运行。"]
    return "\n".join(L)
