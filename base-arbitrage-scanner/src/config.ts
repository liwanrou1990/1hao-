import 'dotenv/config';

/** All runtime configuration is read from the environment (.env). Nothing secret is hard-coded. */
export interface AppConfig {
  rpcUrls: string[];               // BASE_RPC_URLS (comma separated) or BASE_RPC_URL
  wsUrl?: string;                  // optional websocket for newHeads
  chainId: number;                 // must be 8453
  dbPath: string;
  reportDir: string;
  rpcMaxRps: number;
  rpcMaxConcurrency: number;
  rpcRetries: number;
  amountLadderUsd: number[];       // borrow sizes to test
  minPoolTvlUsd: number;           // pools below this are excluded
  minNetProfitUsd: number;         // leaderboard "executable" threshold (after sim)
  slippageBufferBps: number;       // execution-cost buffer applied on net profit (adverse price move between quote and inclusion)
  maxRoutesPerBlock: number;
  enableMultiHop: boolean;
  forkProvider: 'auto' | 'anvil' | 'hardhat' | 'none';
  forkPort: number;
  forkTimeoutMs: number;
  offlineFixture: boolean;         // run on deterministic synthetic market (no RPC) - results are labeled OFFLINE_FIXTURE
  backtestSampleEveryBlocks: number;
  dedupeBlocks: number;            // suppress identical candidate (route+amount) re-insertion within N blocks unless net profit changes
  gasUnitsFlashLoanOverhead: number;
  gasUnitsPerV3Leg: number;
  gasUnitsPerV2Leg: number;
  gasUnitsBase: number;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
}

function num(name: string, def: number): number {
  const v = process.env[name];
  if (v === undefined || v === '') return def;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`env ${name} is not a number: ${v}`);
  return n;
}
function bool(name: string, def: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === '') return def;
  return ['1', 'true', 'yes', 'on'].includes(v.toLowerCase());
}

export function loadConfig(): AppConfig {
  const urls = (process.env.BASE_RPC_URLS ?? process.env.BASE_RPC_URL ?? '')
    .split(',').map(s => s.trim()).filter(Boolean);
  const ladder = (process.env.AMOUNT_LADDER_USD ?? '1000,10000,50000,100000,500000,1000000')
    .split(',').map(s => Number(s.trim())).filter(n => Number.isFinite(n) && n > 0);
  const fp = (process.env.FORK_PROVIDER ?? 'auto') as AppConfig['forkProvider'];
  return {
    rpcUrls: urls,
    wsUrl: process.env.BASE_WS_URL || undefined,
    chainId: num('CHAIN_ID', 8453),
    dbPath: process.env.DB_PATH ?? 'data/scanner.sqlite',
    reportDir: process.env.REPORT_DIR ?? 'reports',
    rpcMaxRps: num('RPC_MAX_RPS', 20),
    rpcMaxConcurrency: num('RPC_MAX_CONCURRENCY', 6),
    rpcRetries: num('RPC_RETRIES', 4),
    amountLadderUsd: ladder,
    minPoolTvlUsd: num('MIN_POOL_TVL_USD', 50_000),
    minNetProfitUsd: num('MIN_NET_PROFIT_USD', 1),
    slippageBufferBps: num('SLIPPAGE_BUFFER_BPS', 5),
    maxRoutesPerBlock: num('MAX_ROUTES_PER_BLOCK', 400),
    enableMultiHop: bool('ENABLE_MULTIHOP', true),
    forkProvider: ['auto', 'anvil', 'hardhat', 'none'].includes(fp) ? fp : 'auto',
    forkPort: num('FORK_PORT', 8546),
    forkTimeoutMs: num('FORK_TIMEOUT_MS', 90_000),
    offlineFixture: bool('OFFLINE_FIXTURE', false),
    backtestSampleEveryBlocks: num('BACKTEST_SAMPLE_EVERY_BLOCKS', 30),
    dedupeBlocks: num('DEDUPE_BLOCKS', 10),
    gasUnitsFlashLoanOverhead: num('GAS_UNITS_FLASHLOAN', 140_000),
    gasUnitsPerV3Leg: num('GAS_UNITS_V3_LEG', 130_000),
    gasUnitsPerV2Leg: num('GAS_UNITS_V2_LEG', 95_000),
    gasUnitsBase: num('GAS_UNITS_BASE', 60_000),
    logLevel: (process.env.LOG_LEVEL as AppConfig['logLevel']) ?? 'info',
  };
}

export const BASE_CHAIN_ID = 8453;
export const BLOCK_TIME_SECONDS = 2; // Base L2 block time
