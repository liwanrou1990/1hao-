/**
 * ONCHAIN fork integration test: forks REAL Base mainnet state (Anvil or Hardhat node) at the latest block, verifies the
 * deployments through the fork, deploys FlashArbExecutor on the fork only, and runs the atomic flashLoanSimple→swap→repay
 * simulation on real pools. Requires BASE_RPC_URL. Without a healthy RPC the suite reports BLOCKED and is skipped —
 * it never substitutes synthetic data.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { loadConfig } from '../src/config.js';
import { checkAllRpcs, rankHealthy, renderDiagnosis } from '../src/chain/health.js';
import { createChainClient } from '../src/chain/client.js';
import { verifyDeployments } from '../src/chain/verify.js';
import { OnchainSource } from '../src/chain/onchainSource.js';
import { ForkNode, ForkSimulator, detectForkProvider } from '../src/sim/fork.js';
import { enumerateRoutes } from '../src/scanner/routes.js';
import { ADDR } from '../src/chain/addresses.js';
import { ScanEngine } from '../src/scanner/scan.js';

const cfg = loadConfig(); cfg.forkPort = 8621;
const health = await checkAllRpcs(cfg);
const healthy = rankHealthy(health);
const blocked = healthy.length === 0 || detectForkProvider(cfg.forkProvider) === 'none';
if (blocked) console.warn(`BLOCKED: mainnet fork integration test skipped — no healthy Base RPC or no fork provider.\n${renderDiagnosis(health)}`);

describe.skipIf(blocked)('ONCHAIN mainnet fork: verify → discover → quote → atomic simulate', () => {
  let node: ForkNode; let src: OnchainSource; let latest: bigint;
  beforeAll(async () => {
    const cc = createChainClient(cfg, healthy);
    const v = await verifyDeployments(cc);
    for (const c of v.checks) if (!c.ok) console.error('verify failed:', c.name, c.detail);
    expect(v.ok).toBe(true);
    src = new OnchainSource(cc, cfg, v.tokens);
    latest = await src.latestBlock();
    node = new ForkNode(cfg); await node.start({ forkUrl: healthy[0], blockNumber: latest });
  }, 170_000);
  afterAll(() => node?.stop());

  it('reads the real Aave V3 USDC flash-loan premium and capacity at the pinned block', async () => {
    const fl = await src.flashLoanInfo(src.tokens().USDC.address, latest);
    expect(fl.enabled).toBe(true); expect(fl.premiumBps).toBeGreaterThanOrEqual(0); expect(fl.premiumBps).toBeLessThan(100); expect(fl.availableLiquidity).toBeGreaterThan(0n);
  });

  it('discovers real pools from the official factories and reads their state', async () => {
    const pools = await src.discoverPools();
    expect(pools.length).toBeGreaterThan(3);
    expect(pools.some(p => p.dex === 'UNISWAP_V3')).toBe(true); expect(pools.some(p => p.dex === 'AERODROME_V2' || p.dex === 'AERODROME_CL')).toBe(true);
    const states = await src.readStates(pools, latest);
    expect(states.size).toBeGreaterThan(0);
  });

  it('runs the atomic path on the fork for the best quoted 2-hop route and gets a decoded verdict (VERIFIED_FORK or Unprofitable)', async () => {
    const engine = new ScanEngine(src, cfg); await engine.init();
    const r = await engine.scanBlock(latest);
    const evals = r.evaluations.filter(e => e.ok && e.cost && e.route.hops === 2).sort((a, b) => b.cost!.netProfitUsd - a.cost!.netProfitUsd);
    expect(evals.length).toBeGreaterThan(0);
    const sim = new ForkSimulator(node, ADDR.AAVE_POOL); await sim.deploy();
    const best = evals[0];
    const res = await sim.simulate(best.route, src.tokens().USDC.address, best.amountIn);
    expect(res.kind).toBe('FORK_ATOMIC');
    // Either the path repays (ok) or it reverts with a decoded Unprofitable shortfall; opaque failures are not acceptable.
    expect(res.ok || /Unprofitable|received/.test(res.failReason ?? '')).toBe(true);
    if (res.ok) { expect(res.profit!).toBeGreaterThanOrEqual(0n); expect(res.gasUsed).toBeDefined(); }
    // fork quote vs engine quote must agree on direction (both from the same pinned block)
    const engineGross = best.cost!.grossProfitUsd, forkGross = res.amountOut !== undefined ? Number(res.amountOut - best.amountIn) / 1e6 : null;
    if (forkGross !== null) expect(Math.abs(forkGross - engineGross)).toBeLessThan(Math.max(1, Math.abs(engineGross) * 0.05));
    void enumerateRoutes;
  }, 170_000);
});
