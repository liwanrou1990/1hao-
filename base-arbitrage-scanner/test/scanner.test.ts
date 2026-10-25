import { describe, it, expect } from 'vitest';
import { getAddress } from 'viem';
import { enumerateRoutes } from '../src/scanner/routes.js';
import { computeCosts } from '../src/scanner/cost.js';
import { loadConfig } from '../src/config.js';
import { OfflineFixtureSource, FIXTURE_TOKENS, FixtureSimulator } from '../src/offline/fixture.js';
import { ScanEngine } from '../src/scanner/scan.js';
import { ScannerDb } from '../src/storage/db.js';
import { Runner } from '../src/runner.js';
import { rowsToCsv } from '../src/storage/csv.js';
import { buildReport } from '../src/report.js';

const cfg = loadConfig();
const src = new OfflineFixtureSource(7, 'volatile');

describe('route enumeration', () => {
  it('generates both directions, excludes same-pool loops, includes same-DEX different fee tiers and 3-hop', async () => {
    const pools = await src.discoverPools();
    const tokens = Object.values(FIXTURE_TOKENS);
    const r2 = enumerateRoutes(pools, tokens, FIXTURE_TOKENS.USDC.address, { multiHop: false, max: 10_000 });
    for (const r of r2) { expect(r.hops).toBe(2); expect(r.legs[0].pool.address).not.toBe(r.legs[1].pool.address); expect(r.legs[0].symbolIn).toBe('USDC'); expect(r.legs[1].symbolOut).toBe('USDC'); }
    const wethUsdc = pools.filter(p => p.label.includes('WETH/USDC')).length; // 4 pools → 4*3 = 12 ordered pairs
    expect(r2.filter(r => r.legs[0].symbolOut === 'WETH').length).toBe(wethUsdc * (wethUsdc - 1));
    const ids = new Set(r2.map(r => r.id)); expect(ids.size).toBe(r2.length);
    expect(r2.some(r => r.legs[0].pool.dex === 'UNISWAP_V3' && r.legs[1].pool.dex === 'UNISWAP_V3' && r.legs[0].pool.feePpm !== r.legs[1].pool.feePpm)).toBe(true);
    const r3 = enumerateRoutes(pools, tokens, FIXTURE_TOKENS.USDC.address, { multiHop: true, max: 10_000 });
    expect(r3.some(r => r.hops === 3 && r.path.startsWith('USDC->WETH->cbBTC->USDC'))).toBe(true);
    expect(r3.length).toBeGreaterThan(r2.length);
  });
});

describe('cost model', () => {
  it('net = gross - (premium + gasL2 + gasL1 + buffer); fees/impact are reported but not double counted', async () => {
    const pools = await src.discoverPools(); const tokens = Object.values(FIXTURE_TOKENS);
    const route = enumerateRoutes(pools, tokens, FIXTURE_TOKENS.USDC.address, { multiHop: false, max: 1 })[0];
    const amountIn = 10_000n * 10n ** 6n; const amountOut = 10_020n * 10n ** 6n;
    const quotes = [{ pool: route.legs[0].pool.address, tokenIn: route.legs[0].tokenIn, tokenOut: route.legs[0].tokenOut, amountIn, amountOut: 10n ** 18n, ok: true, feePaid: amountIn * BigInt(route.legs[0].pool.feePpm) / 1_000_000n }, { pool: route.legs[1].pool.address, tokenIn: route.legs[1].tokenIn, tokenOut: route.legs[1].tokenOut, amountIn: 10n ** 18n, amountOut, ok: true, feePaid: 10n ** 18n * BigInt(route.legs[1].pool.feePpm) / 1_000_000n }];
    const fl = { asset: FIXTURE_TOKENS.USDC.address, enabled: true, premiumBps: 5, availableLiquidity: 10n ** 12n, block: 1n };
    const gas = { block: 1n, baseFeePerGas: 10_000_000n, maxPriorityFeePerGas: 1_000_000n, l1FeeWeiForTypicalTx: 10n ** 13n, ethUsdc: 4000 };
    const c = computeCosts({ cfg, route, quotes, amountIn, amountOut, usdcDecimals: 6, fl, gas, refPriceUsd: t => t.toLowerCase() === FIXTURE_TOKENS.WETH.address.toLowerCase() ? 4000 : 1, spotPrices: [1 / 4000, 4010] });
    expect(c.grossProfitUsd).toBeCloseTo(20, 6);
    expect(c.flashLoanPremiumUsd).toBeCloseTo(5, 6);
    expect(c.totalCostUsd).toBeCloseTo(c.flashLoanPremiumUsd + c.gasL2Usd + c.gasL1DataUsd + c.executionBufferUsd, 9);
    expect(c.netProfitUsd).toBeCloseTo(c.grossProfitUsd - c.totalCostUsd, 9);
    expect(c.theoreticalSpreadUsd).toBeCloseTo(10_000 * 4010 / 4000 - 10_000, 6);
    expect(c.dexFeesUsd).toBeGreaterThan(0); expect(c.priceImpactUsd).toBeGreaterThanOrEqual(0);
    expect(c.priorityFeeUsd).toBeLessThan(c.gasL2Usd);
    expect(c.roi).toBeCloseTo(c.netProfitUsd / 10_000, 9);
  });
});

describe('offline fixture + engine + runner + db', () => {
  it('is deterministic per block and never marks fixture simulations as executable', async () => {
    const engine = new ScanEngine(src, cfg); await engine.init();
    const b = src.genesisBlock + 1234n;
    const r1 = await engine.scanBlock(b), r2 = await engine.scanBlock(b);
    expect(r1.evaluations.length).toBe(r2.evaluations.length);
    expect(r1.evaluations.map(e => e.amountOut.toString())).toEqual(r2.evaluations.map(e => e.amountOut.toString()));
    expect(r1.poolsScanned).toBe(10); expect(r1.poolsExcluded.length).toBe(1); expect(r1.poolsExcluded[0].reason).toMatch(/tvl_below_min/);
    expect(r1.fl.premiumBps).toBe(5);
    // ladder respects Aave capacity
    const cap = r1.fl.availableLiquidity; for (const e of r1.evaluations) if (e.ok) expect(e.amountIn <= cap).toBe(true);
    const db = new ScannerDb(':memory:'); db.startRun('t', 'live', 'OFFLINE_FIXTURE');
    const fs = new FixtureSimulator(src);
    const runner = new Runner(engine, src, db, cfg, 't', 'live', 'OFFLINE_FIXTURE', async (route, _a, amount, block) => fs.simulate(route, amount, block, 5));
    let cands = 0, sims = 0;
    for (let i = 0n; i < 40n; i++) { const s = await runner.runBlock(b + i * 7n); cands += s.candidates; sims += s.simulated; expect(s.executable).toBe(0); }
    expect(sims).toBeGreaterThan(0); expect(cands).toBeGreaterThanOrEqual(sims);
    const rows = db.all('SELECT * FROM candidates');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r: any) => r.executable === 0)).toBe(true);        // FIXTURE_SEQUENTIAL can never be executable
    expect(rows.some((r: any) => r.sim_kind === 'FIXTURE_SEQUENTIAL' && r.sim_ok === 1)).toBe(true);
    const hadFailures = db.all('SELECT failures_json FROM block_stats').some((r: any) => Object.keys(JSON.parse(r.failures_json)).length > 0);
    if (hadFailures) expect(rows.some((r: any) => r.quote_ok === 0 && r.quote_fail_reason)).toBe(true); // failures with reasons are stored
    // the quote failure path itself: an absurd size exhausts synthetic liquidity and is reported, never silently zero
    const pools = await src.discoverPools(); const q = src.quoteOne(pools[0], FIXTURE_TOKENS.USDC.address, 10n ** 30n, b);
    expect(q.ok).toBe(false); expect(q.error).toMatch(/insufficient liquidity/);
    const csv = rowsToCsv(rows); expect(csv.split('\n')[0]).toContain('sim_net_profit_usd'); expect(csv.split('\n').length).toBe(rows.length + 2);
    const rep = buildReport(db, 't', { title: 'test' });
    expect(rep).toContain('OFFLINE_FIXTURE'); expect(rep).toContain('None can exist in this run'); expect(rep).toContain('Optimal borrow amount');
    db.close();
  });
  it('duplicate filtering suppresses unchanged un-simulated rows', async () => {
    const calm = new OfflineFixtureSource(3, 'calm');
    const engine = new ScanEngine(calm, cfg); await engine.init();
    const db = new ScannerDb(':memory:'); db.startRun('d', 'live', 'OFFLINE_FIXTURE');
    const runner = new Runner(engine, calm, db, cfg, 'd', 'live', 'OFFLINE_FIXTURE', undefined);
    const b = calm.genesisBlock + 50n;
    await runner.runBlock(b); const n1 = db.get('SELECT COUNT(*) n FROM candidates').n;
    await runner.runBlock(b); const n2 = db.get('SELECT COUNT(*) n FROM candidates').n; // identical block → identical net → suppressed
    expect(n2).toBe(n1);
    db.close();
  });
});
