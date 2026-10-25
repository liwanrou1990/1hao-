// node:sqlite is loaded via process.getBuiltinModule so that bundlers/test runners that do not know the builtin still work.
const { DatabaseSync } = (process as any).getBuiltinModule('node:sqlite') as typeof import('node:sqlite');
type DatabaseSync = import('node:sqlite').DatabaseSync;
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export type DataSource = 'ONCHAIN' | 'OFFLINE_FIXTURE' | 'LOCAL_EVM_SYNTHETIC';
export type SimKind = 'FORK_ATOMIC' | 'FIXTURE_SEQUENTIAL' | 'NONE';

export interface CandidateRow {
  run_id: string; mode: 'live' | 'backtest'; data_source: DataSource; block: number; block_time: number; route_id: string; path: string; hops: number;
  tokens: string; dexes: string; pools: string; amount_in: string; amount_out: string; amount_source: string;
  theoretical_spread_usd: number; gross_profit_usd: number; dex_fees_usd: number; price_impact_usd: number; flash_premium_bps: number; flash_premium_usd: number;
  gas_units: number; gas_l2_usd: number; gas_l1_usd: number; priority_fee_usd: number; buffer_usd: number; total_cost_usd: number; net_profit_usd: number; roi: number;
  quote_ok: number; quote_fail_reason: string | null; sim_kind: SimKind; sim_ok: number | null; sim_profit_usd: number | null; sim_gas_used: number | null; sim_fail_reason: string | null; sim_net_profit_usd: number | null;
  executable: number; quotes_json: string;
}

export class ScannerDb {
  db: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL;`);
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, mode TEXT, data_source TEXT, started_at INTEGER, finished_at INTEGER, note TEXT);
      CREATE TABLE IF NOT EXISTS pools (address TEXT PRIMARY KEY, dex TEXT, token0 TEXT, token1 TEXT, symbol0 TEXT, symbol1 TEXT, fee_ppm INTEGER, tick_spacing INTEGER, stable INTEGER, label TEXT, verified_at INTEGER);
      CREATE TABLE IF NOT EXISTS pool_snapshots (block INTEGER, pool TEXT, sqrt_price_x96 TEXT, tick INTEGER, liquidity TEXT, reserve0 TEXT, reserve1 TEXT, fee_ppm INTEGER, tvl_usd REAL, PRIMARY KEY(block, pool));
      CREATE TABLE IF NOT EXISTS block_stats (run_id TEXT, block INTEGER, block_time INTEGER, data_source TEXT, pools_scanned INTEGER, pools_excluded INTEGER, routes INTEGER, evaluations INTEGER, candidates INTEGER, simulated INTEGER, sim_passed INTEGER, executable INTEGER, best_net_usd REAL, flash_premium_bps INTEGER, flash_available TEXT, base_fee_wei TEXT, priority_fee_wei TEXT, eth_usdc REAL, failures_json TEXT, PRIMARY KEY(run_id, block));
      CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, mode TEXT, data_source TEXT, block INTEGER, block_time INTEGER, route_id TEXT, path TEXT, hops INTEGER, tokens TEXT, dexes TEXT, pools TEXT,
        amount_in TEXT, amount_out TEXT, amount_source TEXT, theoretical_spread_usd REAL, gross_profit_usd REAL, dex_fees_usd REAL, price_impact_usd REAL, flash_premium_bps INTEGER, flash_premium_usd REAL,
        gas_units INTEGER, gas_l2_usd REAL, gas_l1_usd REAL, priority_fee_usd REAL, buffer_usd REAL, total_cost_usd REAL, net_profit_usd REAL, roi REAL,
        quote_ok INTEGER, quote_fail_reason TEXT, sim_kind TEXT, sim_ok INTEGER, sim_profit_usd REAL, sim_gas_used INTEGER, sim_fail_reason TEXT, sim_net_profit_usd REAL, executable INTEGER, quotes_json TEXT, created_at INTEGER DEFAULT (unixepoch()));
      CREATE INDEX IF NOT EXISTS idx_cand_block ON candidates(block);
      CREATE INDEX IF NOT EXISTS idx_cand_net ON candidates(sim_net_profit_usd);
      CREATE TABLE IF NOT EXISTS ladder_stats (run_id TEXT, block INTEGER, amount_in TEXT, evaluations INTEGER, quote_ok INTEGER, best_net_usd REAL, avg_net_usd REAL, best_sim_net_usd REAL, fail_reasons TEXT, PRIMARY KEY(run_id, block, amount_in));
      CREATE TABLE IF NOT EXISTS rpc_failures (ts INTEGER, run_id TEXT, method TEXT, reason TEXT, attempt INTEGER);
      CREATE TABLE IF NOT EXISTS sim_failures (run_id TEXT, block INTEGER, route_id TEXT, reason TEXT, ts INTEGER);
    `);
  }
  startRun(run_id: string, mode: string, data_source: DataSource, note = '') { this.db.prepare('INSERT OR REPLACE INTO runs(run_id,mode,data_source,started_at,note) VALUES(?,?,?,?,?)').run(run_id, mode, data_source, Date.now(), note); }
  finishRun(run_id: string) { this.db.prepare('UPDATE runs SET finished_at=? WHERE run_id=?').run(Date.now(), run_id); }
  upsertPool(p: { address: string; dex: string; token0: string; token1: string; symbol0: string; symbol1: string; feePpm: number; tickSpacing?: number; stable?: boolean; label: string }) {
    this.db.prepare('INSERT OR REPLACE INTO pools VALUES(?,?,?,?,?,?,?,?,?,?,?)').run(p.address, p.dex, p.token0, p.token1, p.symbol0, p.symbol1, p.feePpm, p.tickSpacing ?? null, p.stable === undefined ? null : p.stable ? 1 : 0, p.label, Date.now());
  }
  insertSnapshot(block: bigint, s: { pool: string; sqrtPriceX96?: bigint; tick?: number; liquidity?: bigint; reserve0?: bigint; reserve1?: bigint; feePpm: number; tvlUsd?: number }) {
    this.db.prepare('INSERT OR REPLACE INTO pool_snapshots VALUES(?,?,?,?,?,?,?,?,?)').run(Number(block), s.pool, s.sqrtPriceX96?.toString() ?? null, s.tick ?? null, s.liquidity?.toString() ?? null, s.reserve0?.toString() ?? null, s.reserve1?.toString() ?? null, s.feePpm, s.tvlUsd ?? null);
  }
  insertBlockStats(r: Record<string, unknown>) {
    this.db.prepare(`INSERT OR REPLACE INTO block_stats(run_id,block,block_time,data_source,pools_scanned,pools_excluded,routes,evaluations,candidates,simulated,sim_passed,executable,best_net_usd,flash_premium_bps,flash_available,base_fee_wei,priority_fee_wei,eth_usdc,failures_json) VALUES(@run_id,@block,@block_time,@data_source,@pools_scanned,@pools_excluded,@routes,@evaluations,@candidates,@simulated,@sim_passed,@executable,@best_net_usd,@flash_premium_bps,@flash_available,@base_fee_wei,@priority_fee_wei,@eth_usdc,@failures_json)`).run(r as any);
  }
  insertCandidate(c: CandidateRow) {
    const cols = Object.keys(c);
    this.db.prepare(`INSERT INTO candidates(${cols.join(',')}) VALUES(${cols.map(k => '@' + k).join(',')})`).run(c as any);
  }
  insertLadderStat(r: { run_id: string; block: number; amount_in: string; evaluations: number; quote_ok: number; best_net_usd: number | null; avg_net_usd: number | null; best_sim_net_usd: number | null; fail_reasons: string }) { this.db.prepare('INSERT OR REPLACE INTO ladder_stats VALUES(@run_id,@block,@amount_in,@evaluations,@quote_ok,@best_net_usd,@avg_net_usd,@best_sim_net_usd,@fail_reasons)').run(r as any); }
  insertRpcFailure(run_id: string, f: { ts: number; method: string; reason: string; attempt: number }) { this.db.prepare('INSERT INTO rpc_failures VALUES(?,?,?,?,?)').run(f.ts, run_id, f.method, f.reason, f.attempt); }
  insertSimFailure(run_id: string, block: bigint, route_id: string, reason: string) { this.db.prepare('INSERT INTO sim_failures VALUES(?,?,?,?,?)').run(run_id, Number(block), route_id, reason, Date.now()); }
  /** last time a (route, amount) candidate was stored with ~same net profit — used for duplicate filtering */
  lastSimilar(route_id: string, amount_in: string, withinBlocks: number, block: number): { block: number; net_profit_usd: number } | undefined {
    return this.db.prepare('SELECT block, net_profit_usd FROM candidates WHERE route_id=? AND amount_in=? AND block>? ORDER BY block DESC LIMIT 1').get(route_id, amount_in, block - withinBlocks) as any;
  }
  all<T = any>(sql: string, ...params: unknown[]): T[] { return this.db.prepare(sql).all(...(params as any)) as T[]; }
  get<T = any>(sql: string, ...params: unknown[]): T | undefined { return this.db.prepare(sql).get(...(params as any)) as T | undefined; }
  close() { this.db.close(); }
}
