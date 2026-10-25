#!/usr/bin/env node
import { existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { loadConfig, type AppConfig } from '../config.js';
import { log, setLogLevel } from '../log.js';
import { createChainClient, watchBlocks, type ChainClient } from '../chain/client.js';
import { verifyDeployments } from '../chain/verify.js';
import { OnchainSource } from '../chain/onchainSource.js';
import { OfflineFixtureSource, FixtureSimulator } from '../offline/fixture.js';
import type { MarketSource } from '../dex/types.js';
import { ScanEngine } from '../scanner/scan.js';
import { ScannerDb, type DataSource } from '../storage/db.js';
import { Runner, type SimulateFn, type BlockSummary } from '../runner.js';
import { planBacktest, runBacktest } from '../backtest/historical.js';
import { ForkNode, ForkSimulator, detectForkProvider } from '../sim/fork.js';
import { ADDR } from '../chain/addresses.js';
import { renderLeaderboard, renderBlockStats } from './leaderboard.js';
import { buildReport, writeReport } from '../report.js';
import { exportTable } from '../storage/csv.js';
import { checkAllRpcs, rankHealthy, renderDiagnosis } from '../chain/health.js';
import { writeFileSync } from 'node:fs';

function parseArgs(argv: string[]) { const out: Record<string, string | boolean> = {}; const pos: string[] = []; for (let i = 0; i < argv.length; i++) { const a = argv[i]; if (a.startsWith('--')) { const [k, v] = a.slice(2).split('='); if (v !== undefined) out[k] = v; else if (argv[i + 1] && !argv[i + 1].startsWith('--')) out[k] = argv[++i]; else out[k] = true; } else pos.push(a); } return { opts: out, pos }; }
const { opts, pos } = parseArgs(process.argv.slice(2));
const cmd = pos[0] ?? 'help';
const cfg = loadConfig(); setLogLevel(cfg.logLevel);
if (opts.offline) cfg.offlineFixture = true;
const runId = (opts.run as string) ?? `${cmd}-${new Date().toISOString().replace(/[:.]/g, '-')}`;

async function openSource(): Promise<{ src: MarketSource; cc?: ChainClient; dataSource: DataSource }> {
  if (cfg.offlineFixture) { log.warn('OFFLINE_FIXTURE mode: synthetic market, results are NOT real'); return { src: new OfflineFixtureSource(Number(opts.seed ?? 42), (opts.scenario as any) ?? 'volatile'), dataSource: 'OFFLINE_FIXTURE' }; }
  const health = await checkAllRpcs(cfg);
  const healthy = rankHealthy(health);
  if (!healthy.length) {
    const diag = renderDiagnosis(health);
    const out = join(cfg.reportDir, 'BLOCKED-rpc-diagnosis.md');
    mkdirSync(cfg.reportDir, { recursive: true });
    writeFileSync(out, `# BLOCKED: Base mainnet RPC unavailable\n\n- checked at: ${new Date().toISOString()}\n- consequence: no real-data scan/backtest was run. Synthetic data is NOT used as a substitute (use --offline explicitly for pipeline tests; its output is labeled OFFLINE_FIXTURE).\n\n${diag}\n`);
    log.error('BLOCKED: no healthy Base RPC endpoint.\n' + diag + `\nwritten: ${out}`);
    process.exit(2);
  }
  for (const h of health) log[h.ok ? 'info' : 'warn'](`rpc ${h.masked}: ${h.cls} ${h.detail}`);
  const cc = createChainClient(cfg, healthy);
  const v = await verifyDeployments(cc);
  for (const c of v.checks) log[c.ok ? 'info' : 'error'](`verify ${c.ok ? 'OK ' : 'BAD'} ${c.name}: ${c.detail}`);
  if (!v.ok) { log.error('deployment verification failed; refusing to scan with unverified addresses'); process.exit(3); }
  return { src: new OnchainSource(cc, cfg, v.tokens), cc, dataSource: 'ONCHAIN' };
}

async function makeSimulator(src: MarketSource, cc: ChainClient | undefined, pin: 'live' | 'historical'): Promise<{ simulate?: SimulateFn; close: () => void; label: string }> {
  if (opts['no-sim']) return { close: () => {}, label: 'disabled (--no-sim)' };
  if (src.kind === 'OFFLINE_FIXTURE') { const fs = new FixtureSimulator(src as OfflineFixtureSource); return { simulate: async (route, _asset, amount, block) => { const fl = await src.flashLoanInfo(_asset, block); return fs.simulate(route, amount, block, fl.premiumBps); }, close: () => {}, label: 'FIXTURE_SEQUENTIAL (synthetic)' }; }
  const provider = detectForkProvider(cfg.forkProvider);
  if (provider === 'none') return { close: () => {}, label: 'none available (install Foundry anvil or set FORK_PROVIDER=hardhat)' };
  if (!existsSync('artifacts/compiled.json')) { log.warn('artifacts/compiled.json missing; run `npm run compile-contracts`'); return { close: () => {}, label: 'disabled (contracts not compiled)' }; }
  const node = new ForkNode(cfg, provider);
  const forkUrl = cfg.rpcUrls[0];
  const latest = await src.latestBlock();
  await node.start({ forkUrl, blockNumber: latest });
  const sim = new ForkSimulator(node, ADDR.AAVE_POOL);
  let pinned = latest; await sim.deploy();
  const simulate: SimulateFn = async (route, asset, amount, block) => {
    if (block !== pinned) { await node.resetTo(forkUrl, block); pinned = block; sim.executor = undefined; await sim.deploy(); }
    return sim.simulate(route, asset, amount);
  };
  void cc; void pin;
  return { simulate, close: () => node.stop(), label: `FORK_ATOMIC via ${provider} (${forkUrl.replace(/\/\/.*@/, '//***@').replace(/\/v2\/.*/, '/v2/***')})` };
}

function printBlock(s: BlockSummary, db: ScannerDb, all: BlockSummary[]) {
  if (process.stdout.isTTY) process.stdout.write('\x1b[2J\x1b[H');
  console.log(`=== Base flash-loan arbitrage scanner — run ${runId} — ${new Date().toISOString()} ===`);
  console.log(renderBlockStats(all));
  console.log('\n--- leaderboard (ranked by simulated net profit; only VERIFIED_FORK counts as executable; LIVE_EXECUTED never occurs in phase 1) ---');
  console.log(renderLeaderboard(db, runId, Number(opts.top ?? 15)));
  if (Object.keys(s.failures).length) console.log('\nfailures this block: ' + Object.entries(s.failures).map(([k, v]) => `${k}=${v}`).join(', '));
}

async function cmdEnvCheck() {
  console.log('node', process.version, '| node:sqlite available');
  console.log('chainId expected', cfg.chainId, '| RPC URLs configured:', cfg.rpcUrls.length, '| offline fixture:', cfg.offlineFixture);
  console.log('fork provider:', detectForkProvider(cfg.forkProvider), '| contracts compiled:', existsSync('artifacts/compiled.json'));
  console.log('proxy:', process.env.HTTPS_PROXY ? 'HTTPS_PROXY set (outbound goes through the environment proxy; policy denials show as EGRESS_POLICY_DENIED)' : 'none');
  const health = await checkAllRpcs(cfg);
  console.log(renderDiagnosis(health));
  const ok = rankHealthy(health).length > 0;
  console.log(ok ? `healthy endpoints (ranked): ${rankHealthy(health).map(u => u.replace(/\/\/.*@/, '//***@').replace(/\/(v2|v3)\/[^/?]+/, '/$1/***')).join(', ')}` : 'BLOCKED: no healthy Base RPC. Real-data scan/backtest cannot run; --offline only exercises the pipeline on synthetic data.');
  process.exit(ok ? 0 : 1);
}

async function cmdDiscover() {
  const { src, dataSource } = await openSource();
  const db = new ScannerDb(cfg.dbPath);
  const pools = await src.discoverPools();
  for (const p of pools) db.upsertPool(p);
  const block = await src.latestBlock();
  const states = await src.readStates(pools, block);
  console.log(`data source: ${dataSource}; block ${block}; ${pools.length} verified pools`);
  for (const p of pools) { const s = states.get(p.address); console.log(`- ${p.label.padEnd(48)} ${p.address} tvl≈${s?.tvlUsd?.toFixed(0) ?? '?'} USD ${s?.liquidity !== undefined ? `L=${s.liquidity}` : s?.reserve0 !== undefined ? `r0=${s.reserve0} r1=${s.reserve1}` : 'state unavailable'}`); }
  const fl = await src.flashLoanInfo(src.tokens().USDC.address, block);
  console.log(`Aave V3 USDC flash loan: enabled=${fl.enabled} premium=${fl.premiumBps}bps available=${(Number(fl.availableLiquidity) / 1e6).toLocaleString('en-US')} USDC aToken=${fl.aToken}`);
  db.close();
}

async function cmdScan() {
  const { src, cc, dataSource } = await openSource();
  const db = new ScannerDb(cfg.dbPath); db.startRun(runId, 'live', dataSource, JSON.stringify(opts));
  if (cc) cc.onFailure(f => db.insertRpcFailure(runId, f));
  const engine = new ScanEngine(src, cfg); await engine.init(); for (const p of engine.poolList) db.upsertPool(p);
  const sim = await makeSimulator(src, cc, 'live'); log.info(`simulation: ${sim.label}`);
  const runner = new Runner(engine, src, db, cfg, runId, 'live', dataSource, sim.simulate, Number(opts['max-sim'] ?? 10));
  const maxBlocks = Number(opts.blocks ?? 0); const all: BlockSummary[] = []; let processing = false; let pending: bigint | undefined;
  const handle = async (n: bigint) => {
    if (processing) { pending = n; return; } processing = true;
    try { const s = await runner.runBlock(n); all.push(s); printBlock(s, db, all); }
    catch (e) { log.error(`block ${n} failed:`, e); db.insertRpcFailure(runId, { ts: Date.now(), method: `scan_block_${n}`, reason: String((e as any)?.message ?? e).slice(0, 300), attempt: 0 }); }
    finally { processing = false; if (maxBlocks && all.length >= maxBlocks) finish(); else if (pending !== undefined) { const p = pending; pending = undefined; void handle(p); } }
  };
  const finish = () => { stop(); sim.close(); db.finishRun(runId); const rep = buildReport(db, runId, { title: `Live scan report (${dataSource})`, notes: [`simulation: ${sim.label}`, `blocks processed: ${all.length}`] }); const out = join(cfg.reportDir, `live-${runId}.md`); writeReport(out, rep); console.log(`\nreport written: ${out}`); db.close(); process.exit(0); };
  let stop = () => {};
  if (src.kind === 'OFFLINE_FIXTURE') { const start = await src.latestBlock(); let b = start; const t = setInterval(() => { void handle(b++); }, 400); stop = () => clearInterval(t); }
  else stop = watchBlocks(cc!, cfg, n => { void handle(n); });
  process.on('SIGINT', finish);
}

async function cmdBacktest() {
  const { src, cc, dataSource } = await openSource();
  const hours = Number(opts.hours ?? 24); const sample = Number(opts.sample ?? cfg.backtestSampleEveryBlocks);
  const db = new ScannerDb(cfg.dbPath); db.startRun(runId, 'backtest', dataSource, JSON.stringify({ hours, sample }));
  if (cc) cc.onFailure(f => db.insertRpcFailure(runId, f));
  const engine = new ScanEngine(src, cfg); await engine.init(); for (const p of engine.poolList) db.upsertPool(p);
  const plan = await planBacktest(src, hours, sample, opts.end ? BigInt(opts.end as string) : undefined);
  if (opts['max-blocks']) plan.blocks = plan.blocks.slice(-Number(opts['max-blocks']));
  log.info(`backtest plan: ${plan.rule}${opts['max-blocks'] ? ` (limited to last ${plan.blocks.length} samples)` : ''}`);
  const sim = await makeSimulator(src, cc, 'historical'); log.info(`simulation: ${sim.label}`);
  const runner = new Runner(engine, src, db, cfg, runId, 'backtest', dataSource, sim.simulate, Number(opts['max-sim'] ?? 5));
  const all: BlockSummary[] = [];
  const res = await runBacktest(runner, plan, (s, i) => { all.push(s); if (i % 10 === 0 || i === plan.blocks.length - 1) { console.log(`[${i + 1}/${plan.blocks.length}] ` + renderBlockStats([s], 1).split('\n')[1]); } });
  sim.close(); db.finishRun(runId);
  console.log('\n' + renderBlockStats(all, 20)); console.log('\n' + renderLeaderboard(db, runId, 20));
  const rep = buildReport(db, runId, { title: `Historical backtest report — ${hours}h (${dataSource})`, plan: plan.rule, notes: [`simulation: ${sim.label}`], failedBlocks: res.failedBlocks });
  const out = join(cfg.reportDir, `backtest-${hours}h-${runId}.md`); writeReport(out, rep); console.log(`\nreport written: ${out}`);
  db.close();
}

async function cmdRpcCheck() {
  const health = await checkAllRpcs(cfg);
  const md = `# RPC diagnosis (${new Date().toISOString()})\n\n${renderDiagnosis(health)}\n`;
  mkdirSync(cfg.reportDir, { recursive: true }); writeFileSync(join(cfg.reportDir, 'rpc-diagnosis.md'), md);
  console.log(md); process.exit(rankHealthy(health).length ? 0 : 1);
}
function cmdLeaderboard() { const db = new ScannerDb(cfg.dbPath); console.log(renderLeaderboard(db, opts.run as string | undefined, Number(opts.top ?? 25))); const stats = db.all('SELECT * FROM block_stats ' + (opts.run ? 'WHERE run_id=? ' : '') + 'ORDER BY block DESC LIMIT 15', ...(opts.run ? [opts.run] : [])).reverse(); console.log('\n' + renderBlockStats(stats.map((r: any) => ({ block: r.block, time: r.block_time, poolsScanned: r.pools_scanned, poolsExcluded: r.pools_excluded, routes: r.routes, evaluations: r.evaluations, candidates: r.candidates, simulated: r.simulated, simPassed: r.sim_passed, executable: r.executable, bestNetUsd: r.best_net_usd, bestSimNetUsd: null, failures: {}, states: { NONE: 0, THEORETICAL_SPREAD: 0, QUOTED_ESTIMATE: 0, VERIFIED_FORK: r.executable, LIVE_EXECUTED: 0 }, invalidated: 0 })), 15)); db.close(); }
function cmdReport() { const db = new ScannerDb(cfg.dbPath); const rid = (opts.run as string) ?? db.get('SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1')?.run_id; if (!rid) { console.log('no runs'); return; } const rep = buildReport(db, rid, { title: `Report for run ${rid}` }); const out = (opts.out as string) ?? join(cfg.reportDir, `report-${rid}.md`); writeReport(out, rep); console.log(rep); console.log(`\nwritten: ${out}`); db.close(); }
function cmdExport() { const db = new ScannerDb(cfg.dbPath); const rid = opts.run as string | undefined; const dir = (opts.out as string) ?? join(cfg.reportDir, 'csv'); mkdirSync(dir, { recursive: true }); for (const t of ['candidates', 'block_stats', 'rpc_failures', 'sim_failures', 'opportunities', 'invalidations', 'ladder_stats']) { const n = exportTable(db, t, join(dir, `${t}${rid ? '-' + rid : ''}.csv`), rid ? 'WHERE run_id=?' : '', ...(rid ? [rid] : [])); console.log(`${t}: ${n} rows -> ${join(dir, t + (rid ? '-' + rid : '') + '.csv')}`); } console.log(`pools: ${exportTable(db, 'pools', join(dir, 'pools.csv'))} rows`); console.log(`pool_snapshots: ${exportTable(db, 'pool_snapshots', join(dir, 'pool_snapshots.csv'))} rows`); db.close(); }

const help = `base-arbitrage-scanner (phase 1: read-only + local fork simulation)
  env-check                       check Node, RPC connectivity (chainId 8453), fork provider, compiled contracts
  rpc-check                       classify every RPC endpoint: OK / EGRESS_POLICY_DENIED / DNS_UNRESOLVED / PROVIDER_AUTH_DENIED / WRONG_CHAIN ... (writes reports/rpc-diagnosis.md)
  discover [--offline]            verify deployments on-chain, discover & verify pools, read Aave flash-loan capacity
  scan [--blocks N] [--offline] [--no-sim] [--max-sim N] [--top N]   live per-block scanning with leaderboard
  backtest --hours 24 [--sample 30] [--max-blocks N] [--end BLOCK] [--offline] [--no-sim]   historical replay at sampled blocks
  leaderboard [--run ID] [--top N]
  report [--run ID] [--out FILE]
  export-csv [--run ID] [--out DIR]
flags: --offline uses the deterministic synthetic fixture (labeled OFFLINE_FIXTURE); never real data.`;

(async () => {
  switch (cmd) {
    case 'env-check': return cmdEnvCheck();
    case 'rpc-check': return cmdRpcCheck();
    case 'discover': return cmdDiscover();
    case 'scan': return cmdScan();
    case 'backtest': return cmdBacktest();
    case 'leaderboard': return cmdLeaderboard();
    case 'report': return cmdReport();
    case 'export-csv': return cmdExport();
    default: console.log(help);
  }
})().catch(e => { log.error('fatal:', e); process.exit(1); });

