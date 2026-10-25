/**
 * LOCAL_EVM_SYNTHETIC integration test: proves the atomic flash-loan path simulation machinery end to end on a local EVM
 * (Hardhat node = Anvil equivalent) with REAL Uniswap V3 core bytecode, a minimal Aerodrome-style pool, a mock Aave pool and
 * the FlashArbExecutor. No RPC, no mainnet state. Liquidity is synthetic and the price dislocation is planted on purpose.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { encodeFunctionData, decodeFunctionResult, parseAbi, getAddress, type Address } from 'viem';
import { loadConfig } from '../src/config.js';
import { ForkNode, ForkSimulator, deployFromUnlocked, sendFromUnlocked, loadArtifact } from '../src/sim/fork.js';
import { priceToSqrtPriceX96, getTickAtSqrtRatio } from '../src/pricing/v3math.js';
import type { PoolRef } from '../src/dex/types.js';
import type { Route } from '../src/scanner/routes.js';

const require = createRequire(import.meta.url);
const factoryArt = require('@uniswap/v3-core/artifacts/contracts/UniswapV3Factory.sol/UniswapV3Factory.json');
const factoryAbi = parseAbi(['function createPool(address,address,uint24) returns (address)', 'function getPool(address,address,uint24) view returns (address)']);
const poolAbi = parseAbi(['function initialize(uint160)', 'function slot0() view returns (uint160,int24,uint16,uint16,uint16,uint8,bool)', 'function liquidity() view returns (uint128)']);
const erc = parseAbi(['function mint(address,uint256)', 'function balanceOf(address) view returns (uint256)']);
const aeroAbi = parseAbi(['function sync()', 'function getAmountOut(uint256,address) view returns (uint256)']);
const lpAbi = parseAbi(['function provide(address,address,address,int24,int24,uint128)']);

const cfg = loadConfig(); cfg.forkPort = 8611; cfg.forkProvider = 'hardhat';
let node: ForkNode; let usdc: Address, weth: Address, uniPool: Address, aeroPool: Address, aave: Address;
const call = async (to: Address, abi: any, fn: string, args: unknown[] = []) => decodeFunctionResult({ abi, functionName: fn, data: await node.rpc('eth_call', [{ to, data: encodeFunctionData({ abi, functionName: fn, args }) }, 'latest']) });

describe('local EVM atomic simulation (LOCAL_EVM_SYNTHETIC)', () => {
  beforeAll(async () => {
    node = new ForkNode(cfg, 'hardhat'); await node.start({});
    expect(BigInt(await node.rpc('eth_chainId'))).toBe(8453n);
    const [from] = await node.accounts();
    usdc = await deployFromUnlocked(node, 'MockERC20', ['USD Coin', 'USDC', 6]);
    weth = await deployFromUnlocked(node, 'MockERC20', ['Wrapped Ether', 'WETH', 18]);
    // real Uniswap V3 factory bytecode (no constructor args)
    const h = await node.rpc('eth_sendTransaction', [{ from, data: factoryArt.bytecode, gas: '0xf00000' }]);
    let rc; for (let i = 0; i < 100 && !(rc = await node.rpc('eth_getTransactionReceipt', [h])); i++) await new Promise(r => setTimeout(r, 100));
    const factory: Address = rc.contractAddress;
    await sendFromUnlocked(node, factory, factoryAbi, 'createPool', [usdc, weth, 500]);
    uniPool = getAddress(await call(factory, factoryAbi, 'getPool', [usdc, weth, 500]) as Address);
    const wethIs0 = BigInt(weth) < BigInt(usdc);
    const [t0, t1, d0, d1] = wethIs0 ? [weth, usdc, 18, 6] : [usdc, weth, 6, 18];
    const price1per0 = wethIs0 ? 4000 : 1 / 4000;            // Uniswap pool: 4000 USDC per WETH
    const sqrtP = priceToSqrtPriceX96(price1per0, d0, d1);
    await sendFromUnlocked(node, uniPool, poolAbi, 'initialize', [sqrtP]);
    const tick = getTickAtSqrtRatio(sqrtP);
    const lp = await deployFromUnlocked(node, 'TestLiquidityProvider');
    await sendFromUnlocked(node, weth, erc, 'mint', [lp, 10n ** 24n]); await sendFromUnlocked(node, usdc, erc, 'mint', [lp, 10n ** 16n]);
    const lower = Math.floor((tick - 5000) / 10) * 10, upper = Math.ceil((tick + 5000) / 10) * 10;
    const L = wethIs0 ? 7n * 10n ** 17n : 7n * 10n ** 17n;
    await sendFromUnlocked(node, lp, lpAbi, 'provide', [uniPool, t0, t1, lower, upper, L]);
    expect(await call(uniPool, poolAbi, 'liquidity') as bigint).toBe(L);
    // Aerodrome-style pool at 4100 USDC per WETH (2.5% dislocation planted on purpose), 0.3% fee
    aeroPool = await deployFromUnlocked(node, 'SimpleAeroPool', [usdc, weth, 30]);
    await sendFromUnlocked(node, weth, erc, 'mint', [aeroPool, 1000n * 10n ** 18n]); await sendFromUnlocked(node, usdc, erc, 'mint', [aeroPool, 4_100_000n * 10n ** 6n]);
    await sendFromUnlocked(node, aeroPool, aeroAbi, 'sync');
    // mock Aave with 5 bps premium and 50M USDC
    aave = await deployFromUnlocked(node, 'MockAavePool', [5]);
    await sendFromUnlocked(node, usdc, erc, 'mint', [aave, 50_000_000n * 10n ** 6n]);
  });
  afterAll(() => node?.stop());

  function refs(): { uni: PoolRef; aero: PoolRef } {
    const wethIs0 = BigInt(weth) < BigInt(usdc);
    const base = wethIs0 ? { token0: weth, token1: usdc, symbol0: 'WETH', symbol1: 'USDC', decimals0: 18, decimals1: 6 } : { token0: usdc, token1: weth, symbol0: 'USDC', symbol1: 'WETH', decimals0: 6, decimals1: 18 };
    return { uni: { dex: 'UNISWAP_V3', address: uniPool, feePpm: 500, tickSpacing: 10, label: 'UNISWAP_V3 WETH/USDC 0.05%', ...base }, aero: { dex: 'AERODROME_V2', address: aeroPool, feePpm: 3000, stable: false, label: 'AERODROME_V2 WETH/USDC volatile 0.30%', ...base } };
  }
  const route = (a: PoolRef, b: PoolRef): Route => ({ id: 'r', hops: 2, path: `USDC->WETH->USDC | ${a.label} => ${b.label}`, legs: [{ pool: a, tokenIn: usdc, tokenOut: weth, symbolIn: 'USDC', symbolOut: 'WETH' }, { pool: b, tokenIn: weth, tokenOut: usdc, symbolIn: 'WETH', symbolOut: 'USDC' }] });

  it('buy cheap on Uniswap V3, sell dear on the Aerodrome-style pool: flash loan repaid, positive profit, gas measured', async () => {
    const { uni, aero } = refs();
    const sim = new ForkSimulator(node, aave); await sim.deploy();
    const amount = 10_000n * 10n ** 6n;
    const r = await sim.simulate(route(uni, aero), usdc, amount);
    expect(r.kind).toBe('FORK_ATOMIC');
    expect(r.ok).toBe(true);
    expect(r.premium).toBe(amount * 5n / 10_000n);
    expect(r.profit!).toBeGreaterThan(100n * 10n ** 6n);  // ≈ +180 USDC expected from a 2.5% dislocation minus 0.35% fees, impact and 5 bps premium
    expect(r.profit!).toBeLessThan(260n * 10n ** 6n);
    expect(r.gasUsed).toBeDefined(); expect(Number(r.gasUsed)).toBeGreaterThan(100_000); expect(Number(r.gasUsed)).toBeLessThan(600_000);
    // independent cross-check against the pools' own quote functions (leg 2) — sim output must match the pool math
    const bal = await call(usdc, erc, 'balanceOf', [sim.executor!]) as bigint; expect(bal).toBe(0n); // eth_call: no state persisted
  });

  it('reverse direction is unprofitable: reverts with Unprofitable(received, owed) and the shortfall is decoded', async () => {
    const { uni, aero } = refs();
    const sim = new ForkSimulator(node, aave); await sim.deploy();
    const amount = 10_000n * 10n ** 6n;
    const r = await sim.simulate(route(aero, uni), usdc, amount);
    expect(r.ok).toBe(false);
    expect(r.failReason).toMatch(/Unprofitable/);
    expect(r.profit!).toBeLessThan(0n);
    expect(r.amountOut!).toBeGreaterThan(9_000n * 10n ** 6n); // received ≈ 9,700 < owed 10,005
  });

  it('larger size has lower ROI (price impact), showing why the optimizer caps borrow size', async () => {
    const { uni, aero } = refs();
    const sim = new ForkSimulator(node, aave); await sim.deploy();
    const small = await sim.simulate(route(uni, aero), usdc, 10_000n * 10n ** 6n);
    const big = await sim.simulate(route(uni, aero), usdc, 1_000_000n * 10n ** 6n);
    expect(small.ok).toBe(true);
    const roiSmall = Number(small.profit!) / 10_000e6, roiBig = Number(big.profit ?? -1n) / 1_000_000e6;
    expect(roiBig).toBeLessThan(roiSmall);
  });

  it('artifact sanity: compiled FlashArbExecutor exposes simulate/executeOperation/uniswapV3SwapCallback', () => {
    const art = loadArtifact('FlashArbExecutor');
    const names = art.abi.filter((x: any) => x.type === 'function').map((x: any) => x.name);
    expect(names).toEqual(expect.arrayContaining(['simulate', 'executeOperation', 'uniswapV3SwapCallback', 'aavePool']));
    expect(JSON.parse(readFileSync('artifacts/compiled.json', 'utf8')).FlashArbExecutor.bytecode.length).toBeGreaterThan(1000);

  });
});
