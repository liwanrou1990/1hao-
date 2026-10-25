/**
 * OFFLINE_FIXTURE: a deterministic synthetic Base-like market used when no RPC is available.
 * It implements MarketSource with REAL AMM math (Uniswap V3 tick math, Aerodrome stable/volatile curves) over synthetic
 * liquidity. Every result produced from it is labeled OFFLINE_FIXTURE and must never be read as real market data.
 */
import { getAddress, type Address } from 'viem';
import { ADDR } from '../chain/addresses.js';
import type { MarketSource, PoolRef, PoolState, Quote, FlashLoanInfo, GasInfo, TokenInfo } from '../dex/types.js';
import { swapExactIn, priceToSqrtPriceX96, getTickAtSqrtRatio, getSqrtRatioAtTick, clPriceImpactBps, type CLPoolModel, Q96 } from '../pricing/v3math.js';
import { aeroGetAmountOut, aeroPriceImpactBps, type AeroPoolModel } from '../pricing/aeroMath.js';
import type { Route } from '../scanner/routes.js';

export const FIXTURE_TOKENS: Record<string, TokenInfo> = {
  WETH: { symbol: 'WETH', address: getAddress(ADDR.WETH), decimals: 18 },
  USDC: { symbol: 'USDC', address: getAddress(ADDR.USDC), decimals: 6 },
  USDT: { symbol: 'USDT', address: getAddress(ADDR.USDT), decimals: 6 },
  cbBTC: { symbol: 'cbBTC', address: getAddress(ADDR.cbBTC), decimals: 8 },
};
const T = FIXTURE_TOKENS;
const eq = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
function sorted(a: TokenInfo, b: TokenInfo): [TokenInfo, TokenInfo] { return BigInt(a.address) < BigInt(b.address) ? [a, b] : [b, a]; }

/** deterministic PRNG (mulberry32) seeded by (poolIndex, block) */
function rand(seed: number): () => number { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

interface FixturePool { ref: PoolRef; depthUsd: number; noiseBps: number; /* per-DEX dislocation amplitude */ }

export class OfflineFixtureSource implements MarketSource {
  readonly kind = 'OFFLINE_FIXTURE' as const;
  pools: FixturePool[] = [];
  readonly genesisBlock = 36_000_000n;
  readonly genesisTime = 1_761_350_400; // 2025-10-25T00:00:00Z (fixture clock; not a real chain timestamp)
  constructor(public seed = 42, public scenario: 'calm' | 'volatile' = 'volatile') {
    let counter = 0;
    const mk = (dex: PoolRef['dex'], a: TokenInfo, b: TokenInfo, feePpm: number, depthUsd: number, noiseBps: number, extra: Partial<PoolRef> = {}): FixturePool => {
      const [t0, t1] = sorted(a, b);
      counter += 1;
      const addr = getAddress(`0x${((BigInt(seed + 1) * 0x9e3779b97f4a7c15n + BigInt(counter) * 0x100000000deadbeefn + BigInt(feePpm)) % (1n << 160n)).toString(16).padStart(40, '0')}`);
      const label = `${dex} ${t0.symbol}/${t1.symbol} ${extra.stable !== undefined ? (extra.stable ? 'stable ' : 'volatile ') : ''}${(feePpm / 10_000).toFixed(2)}%${extra.tickSpacing ? ` ts${extra.tickSpacing}` : ''}`;
      return { ref: { dex, address: addr, token0: t0.address, token1: t1.address, symbol0: t0.symbol, symbol1: t1.symbol, decimals0: t0.decimals, decimals1: t1.decimals, feePpm, label, ...extra }, depthUsd, noiseBps };
    };
    this.pools = [
      mk('UNISWAP_V3', T.WETH, T.USDC, 500, 12_000_000, 6, { tickSpacing: 10 }),
      mk('UNISWAP_V3', T.WETH, T.USDC, 3000, 3_000_000, 14, { tickSpacing: 60 }),
      mk('AERODROME_CL', T.WETH, T.USDC, 400, 9_000_000, 7, { tickSpacing: 100 }),
      mk('AERODROME_V2', T.WETH, T.USDC, 3000, 1_500_000, 18, { stable: false }),
      mk('UNISWAP_V3', T.USDC, T.USDT, 100, 6_000_000, 1.5, { tickSpacing: 1 }),
      mk('AERODROME_V2', T.USDC, T.USDT, 500, 4_000_000, 2, { stable: true }),
      mk('UNISWAP_V3', T.cbBTC, T.USDC, 3000, 5_000_000, 12, { tickSpacing: 60 }),
      mk('AERODROME_CL', T.cbBTC, T.USDC, 500, 6_000_000, 9, { tickSpacing: 100 }),
      mk('UNISWAP_V3', T.WETH, T.cbBTC, 500, 4_000_000, 8, { tickSpacing: 10 }),
      mk('UNISWAP_V3', T.WETH, T.USDT, 500, 2_000_000, 10, { tickSpacing: 10 }),
      mk('AERODROME_V2', T.WETH, T.cbBTC, 3000, 30_000, 25, { stable: false }), // below default TVL filter: exercises exclusion path
    ];
  }
  tokens() { return T; }
  async latestBlock() { return this.genesisBlock + BigInt(Math.floor((Date.now() / 1000 - this.genesisTime) / 2)); }
  async blockTimestamp(block: bigint) { return this.genesisTime + Number(block - this.genesisBlock) * 2; }
  async discoverPools() { return this.pools.map(p => p.ref); }

  /** "True" fair prices (USD) per block — a smooth random walk shared by all pools; per-pool noise creates dislocations. */
  fairPrices(block: bigint): Record<string, number> {
    const n = Number(block - this.genesisBlock);
    const r = rand(this.seed * 7919 + Math.floor(n / 1));
    const walk = (base: number, volPerBlock: number, phase: number) => base * (1 + volPerBlock * Math.sin(n / 97 + phase) * 3 + volPerBlock * (r() - 0.5));
    const v = this.scenario === 'volatile' ? 0.0012 : 0.0002;
    return { [T.USDC.address.toLowerCase()]: 1, [T.USDT.address.toLowerCase()]: walk(1.0002, v / 15, 1.1), [T.WETH.address.toLowerCase()]: walk(3900, v, 0.3), [T.cbBTC.address.toLowerCase()]: walk(111_000, v, 2.2) };
  }
  private poolPrice1per0(fp: FixturePool, block: bigint, idx: number): number {
    const fair = this.fairPrices(block);
    const r = rand(this.seed * 31 + idx * 1009 + Number(block % 1_000_000n));
    const burst = this.scenario === 'volatile' && r() < 0.06 ? (r() - 0.5) * 2 * fp.noiseBps * 6 : 0; // occasional large dislocation
    const noise = (r() - 0.5) * 2 * fp.noiseBps + burst; // bps
    const p1per0 = fair[fp.ref.token0.toLowerCase()] / fair[fp.ref.token1.toLowerCase()];
    return p1per0 * (1 + noise / 10_000);
  }
  clModel(fp: FixturePool, block: bigint): CLPoolModel {
    const idx = this.pools.indexOf(fp);
    const price = this.poolPrice1per0(fp, block, idx);
    const sqrtP = priceToSqrtPriceX96(price, fp.ref.decimals0, fp.ref.decimals1);
    const tick = getTickAtSqrtRatio(sqrtP);
    const fair = this.fairPrices(block);
    // liquidity L such that value in a ±2% band ≈ depthUsd (both sides); L = depthUsd / (2 * (sqrt(p)*(1-1/sqrt(1.02))) ... simplified: use token1 value
    const token1Usd = fair[fp.ref.token1.toLowerCase()];
    const sqrtPf = Number(sqrtP) / Number(Q96);
    const bandFrac = 1 - 1 / Math.sqrt(1.02);
    const L = BigInt(Math.floor((fp.depthUsd / 2 / token1Usd) * 10 ** fp.ref.decimals1 / (sqrtPf * bandFrac)));
    const ts = fp.ref.tickSpacing ?? 10;
    const lower = Math.floor((tick - 2000) / ts) * ts, upper = Math.ceil((tick + 2000) / ts) * ts; // ±~22% range
    const wideL = L / 20n; const wl = Math.floor((tick - 20000) / ts) * ts, wu = Math.ceil((tick + 20000) / ts) * ts;
    return { sqrtPriceX96: sqrtP, tick, liquidity: L + wideL, feePpm: fp.ref.feePpm, tickSpacing: ts, ticks: [{ tick: wl, liquidityNet: wideL }, { tick: lower, liquidityNet: L }, { tick: upper, liquidityNet: -L }, { tick: wu, liquidityNet: -wideL }] };
  }
  aeroModel(fp: FixturePool, block: bigint): AeroPoolModel {
    const idx = this.pools.indexOf(fp);
    const price = this.poolPrice1per0(fp, block, idx); // token1 per token0 (decimal adjusted)
    const fair = this.fairPrices(block);
    const usd1 = fair[fp.ref.token1.toLowerCase()];
    const half = fp.depthUsd / 2;
    const reserve1 = BigInt(Math.floor(half / usd1 * 10 ** fp.ref.decimals1));
    const reserve0 = BigInt(Math.floor(half / usd1 / price * 10 ** fp.ref.decimals0));
    return { reserve0, reserve1, decimals0: fp.ref.decimals0, decimals1: fp.ref.decimals1, stable: !!fp.ref.stable, feeBps: fp.ref.feePpm / 100 };
  }
  async readStates(pools: PoolRef[], block: bigint) {
    const out = new Map<Address, PoolState>();
    const fair = this.fairPrices(block);
    for (const p of pools) {
      const fp = this.pools.find(x => x.ref.address === p.address); if (!fp) continue;
      if (p.dex === 'AERODROME_V2') { const m = this.aeroModel(fp, block); out.set(p.address, { pool: p.address, block, reserve0: m.reserve0, reserve1: m.reserve1, feePpm: p.feePpm, tvlUsd: Number(m.reserve0) / 10 ** p.decimals0 * fair[p.token0.toLowerCase()] + Number(m.reserve1) / 10 ** p.decimals1 * fair[p.token1.toLowerCase()] }); }
      else { const m = this.clModel(fp, block); out.set(p.address, { pool: p.address, block, sqrtPriceX96: m.sqrtPriceX96, tick: m.tick, liquidity: m.liquidity, feePpm: p.feePpm, tvlUsd: fp.depthUsd }); }
    }
    return out;
  }
  quoteOne(pool: PoolRef, tokenIn: Address, amountIn: bigint, block: bigint, mutable?: { cl?: CLPoolModel; aero?: AeroPoolModel }): Quote {
    const fp = this.pools.find(x => x.ref.address === pool.address)!;
    const in0 = eq(tokenIn, pool.token0); const tokenOut = in0 ? pool.token1 : pool.token0;
    const base: Quote = { pool: pool.address, tokenIn, tokenOut, amountIn, amountOut: 0n, ok: false, feePaid: amountIn * BigInt(pool.feePpm) / 1_000_000n };
    try {
      if (pool.dex === 'AERODROME_V2') {
        const m = mutable?.aero ?? this.aeroModel(fp, block);
        const out = aeroGetAmountOut(m, amountIn, in0);
        if (out <= 0n || out >= (in0 ? m.reserve1 : m.reserve0)) return { ...base, error: 'insufficient liquidity' };
        const impact = aeroPriceImpactBps(m, amountIn, out, in0);
        if (mutable?.aero) { if (in0) { m.reserve0 += amountIn; m.reserve1 -= out; } else { m.reserve1 += amountIn; m.reserve0 -= out; } }
        return { ...base, ok: true, amountOut: out, priceImpactBps: impact, gasEstimate: 95_000n };
      }
      const m = mutable?.cl ?? this.clModel(fp, block);
      const r = swapExactIn(m, in0, amountIn);
      if (mutable?.cl) { m.sqrtPriceX96 = r.sqrtPriceX96After; m.tick = r.tickAfter; m.liquidity = r.liquidityAfter; }
      return { ...base, ok: true, amountOut: r.amountOut, priceImpactBps: clPriceImpactBps(m.sqrtPriceX96, r.sqrtPriceX96After), sqrtPriceX96After: r.sqrtPriceX96After, gasEstimate: BigInt(90_000 + 30_000 * r.ticksCrossed) };
    } catch (e: any) { return { ...base, error: e?.message ?? String(e) }; }
  }
  async quoteExactInputBatch(reqs: Array<{ pool: PoolRef; tokenIn: Address; amountIn: bigint }>, block: bigint) { return reqs.map(r => this.quoteOne(r.pool, r.tokenIn, r.amountIn, block)); }
  async flashLoanInfo(asset: Address, block: bigint): Promise<FlashLoanInfo> {
    return { asset, enabled: true, premiumBps: 5, availableLiquidity: 25_000_000n * 10n ** 6n, aToken: '0x0000000000000000000000000000000000000001', block };
  }
  async gasInfo(block: bigint, calldataBytes: number): Promise<GasInfo> {
    const fair = this.fairPrices(block);
    return { block, baseFeePerGas: 8_000_000n /* 0.008 gwei */, maxPriorityFeePerGas: 1_000_000n, l1FeeWeiForTypicalTx: BigInt(calldataBytes + 110) * 16n * 1_200_000_000n /* ≈ (bytes × 16 gas/byte) × 1.2 gwei L1 base fee */, ethUsdc: fair[T.WETH.address.toLowerCase()] };
  }
}

/** FIXTURE_SEQUENTIAL: replays the route leg by leg against mutable pool models (state carried between legs). Not an EVM simulation. */
export class FixtureSimulator {
  constructor(private src: OfflineFixtureSource) {}
  simulate(route: Route, amount: bigint, block: bigint, premiumBps: number) {
    const models = new Map<string, { cl?: CLPoolModel; aero?: AeroPoolModel }>();
    let cur = amount;
    for (const leg of route.legs) {
      const fp = this.src.pools.find(x => x.ref.address === leg.pool.address)!;
      let m = models.get(leg.pool.address); if (!m) { m = leg.pool.dex === 'AERODROME_V2' ? { aero: this.src.aeroModel(fp, block) } : { cl: this.src.clModel(fp, block) }; models.set(leg.pool.address, m); }
      const q = this.src.quoteOne(leg.pool, leg.tokenIn, cur, block, m);
      if (!q.ok) return { kind: 'FIXTURE_SEQUENTIAL' as const, ok: false, failReason: `leg ${leg.symbolIn}->${leg.symbolOut}: ${q.error}`, block };
      cur = q.amountOut;
    }
    const premium = amount * BigInt(premiumBps) / 10_000n; const owed = amount + premium; const profit = cur - owed;
    const gasUsed = BigInt(60_000 + 140_000 + route.legs.reduce((s, l) => s + (l.pool.dex === 'AERODROME_V2' ? 95_000 : 130_000), 0));
    return { kind: 'FIXTURE_SEQUENTIAL' as const, ok: profit >= 0n, amountOut: cur, premium, profit, gasUsed, block, failReason: profit >= 0n ? undefined : `Unprofitable: received ${cur} < owed ${owed}` };
  }
}
