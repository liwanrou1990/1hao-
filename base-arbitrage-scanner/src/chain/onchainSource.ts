import { encodeFunctionData, decodeFunctionResult, getAddress, type Address, type Hex } from 'viem';
import type { AppConfig } from '../config.js';
import { ADDR, SLIPSTREAM_TICK_SPACINGS_FALLBACK } from './addresses.js';
import { erc20Abi, aavePoolAbi, opGasPriceOracleAbi, uniswapV3PoolAbi, slipstreamPoolAbi } from './abis.js';
import type { ChainClient } from './client.js';
import { multicall, type Call } from './multicall.js';
import type { MarketSource, PoolRef, PoolState, Quote, FlashLoanInfo, GasInfo, TokenInfo } from '../dex/types.js';
import * as uni from '../dex/uniswapV3.js';
import * as slip from '../dex/slipstream.js';
import * as aero from '../dex/aerodrome.js';
import { clPriceImpactBps, sqrtPriceToPrice } from '../pricing/v3math.js';
import { aeroPriceImpactBps } from '../pricing/aeroMath.js';
import { log } from '../log.js';

const ZERO = '0x0000000000000000000000000000000000000000';
const pct = (ppm: number) => `${(ppm / 10_000).toFixed(ppm % 10_000 === 0 ? 0 : 2)}%`;

/** MarketSource backed by Base mainnet JSON-RPC via viem + Multicall3. Read-only. */
export class OnchainSource implements MarketSource {
  readonly kind = 'ONCHAIN' as const;
  private tokenMap: Record<string, TokenInfo>;
  private refPrices = new Map<string, number>(); // token address (lower) -> USD price (approx, for TVL filter)
  constructor(private cc: ChainClient, private cfg: AppConfig, tokens: Record<string, TokenInfo>) { this.tokenMap = tokens; }
  tokens() { return this.tokenMap; }
  async latestBlock() { return this.cc.call('eth_blockNumber', () => this.cc.client.getBlockNumber()); }
  async blockTimestamp(block: bigint) { const b = await this.cc.call('eth_getBlockByNumber', () => this.cc.client.getBlock({ blockNumber: block })); return Number(b.timestamp); }

  async discoverPools(): Promise<PoolRef[]> {
    const tokens = Object.values(this.tokenMap);
    const pools: PoolRef[] = [];
    // Slipstream tick spacings from the factory (authoritative)
    let tickSpacings: number[] = [...SLIPSTREAM_TICK_SPACINGS_FALLBACK];
    try { const r = await multicall(this.cc, [slip.slipstreamTickSpacingsCall()], 'latest'); if (r[0].success) tickSpacings = slip.decodeTickSpacings(r[0].returnData); } catch (e) { log.warn('tickSpacings() failed, using fallback list', e); }

    const uCalls = uni.uniV3GetPoolCalls(tokens), sCalls = slip.slipstreamGetPoolCalls(tokens, tickSpacings), aCalls = aero.aeroGetPoolCalls(tokens);
    const res = await multicall(this.cc, [...uCalls, ...sCalls, ...aCalls], 'latest');
    const found: Array<{ addr: Address; dex: PoolRef['dex']; a: TokenInfo; b: TokenInfo; fee?: number; tickSpacing?: number; stable?: boolean }> = [];
    uCalls.forEach((c, i) => { const r = res[i]; if (r.success) { const p = uni.decodeAddress(r.returnData); if (p !== ZERO) found.push({ addr: getAddress(p), dex: 'UNISWAP_V3', ...c.meta }); } });
    sCalls.forEach((c, i) => { const r = res[uCalls.length + i]; if (r.success) { const p = uni.decodeAddress(r.returnData); if (p !== ZERO) found.push({ addr: getAddress(p), dex: 'AERODROME_CL', ...c.meta }); } });
    aCalls.forEach((c, i) => { const r = res[uCalls.length + sCalls.length + i]; if (r.success) { const p = uni.decodeAddress(r.returnData); if (p !== ZERO) found.push({ addr: getAddress(p), dex: 'AERODROME_V2', ...c.meta }); } });
    if (!found.length) return pools;

    // Verify each pool: token0/token1/factory/fee read from the pool itself (never trusted from the lookup alone)
    const metaCalls: Call[] = [];
    const spans: number[] = [];
    for (const f of found) {
      const cs = f.dex === 'UNISWAP_V3' ? [...uni.uniV3PoolMetaCalls(f.addr), { target: f.addr, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'fee' }) }]
        : f.dex === 'AERODROME_CL' ? [...slip.slipstreamPoolMetaCalls(f.addr), { target: f.addr, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'fee' }) }]
        : [...aero.aeroPoolMetaCalls(f.addr), aero.aeroFeeCall(f.addr, f.stable!), aero.aeroIsPoolCall(f.addr)];
      spans.push(metaCalls.length); metaCalls.push(...cs);
    }
    const mres = await multicall(this.cc, metaCalls, 'latest');
    const decAddr = (h: Hex) => getAddress(`0x${h.slice(-40)}`);
    found.forEach((f, idx) => {
      const base = spans[idx];
      try {
        const t0 = decAddr(mres[base].returnData), t1 = decAddr(mres[base + 1].returnData), fac = decAddr(mres[base + 2].returnData);
        const expectFactory = f.dex === 'UNISWAP_V3' ? ADDR.UNISWAP_V3_FACTORY : f.dex === 'AERODROME_CL' ? ADDR.SLIPSTREAM_CL_FACTORY : ADDR.AERODROME_POOL_FACTORY;
        if (fac.toLowerCase() !== expectFactory.toLowerCase()) { log.warn(`pool ${f.addr} factory mismatch ${fac} != ${expectFactory}; excluded`); return; }
        const set = new Set([f.a.address.toLowerCase(), f.b.address.toLowerCase()]);
        if (!set.has(t0.toLowerCase()) || !set.has(t1.toLowerCase())) { log.warn(`pool ${f.addr} token mismatch; excluded`); return; }
        const tok0 = f.a.address.toLowerCase() === t0.toLowerCase() ? f.a : f.b, tok1 = tok0 === f.a ? f.b : f.a;
        let feePpm: number, tickSpacing: number | undefined, stable: boolean | undefined;
        if (f.dex === 'UNISWAP_V3') { feePpm = Number(BigInt(mres[base + 3].returnData)); if (feePpm !== f.fee) { log.warn(`pool ${f.addr} fee mismatch; excluded`); return; } }
        else if (f.dex === 'AERODROME_CL') { tickSpacing = Number(BigInt.asIntN(256, BigInt(mres[base + 3].returnData))); feePpm = Number(BigInt(mres[base + 4].returnData)); if (tickSpacing !== f.tickSpacing) { log.warn(`pool ${f.addr} tickSpacing mismatch; excluded`); return; } }
        else { stable = BigInt(mres[base + 3].returnData) === 1n; feePpm = aero.decodeFeeBps(mres[base + 4].returnData) * 100; const isPool = aero.decodeBool(mres[base + 5].returnData); if (!isPool || stable !== f.stable) { log.warn(`pool ${f.addr} isPool/stable mismatch; excluded`); return; } }
        const label = `${f.dex} ${tok0.symbol}/${tok1.symbol} ${f.dex === 'AERODROME_V2' ? (stable ? 'stable' : 'volatile') + ' ' : ''}${pct(feePpm)}${tickSpacing ? ` ts${tickSpacing}` : ''}`;
        pools.push({ dex: f.dex, address: f.addr, token0: tok0.address, token1: tok1.address, symbol0: tok0.symbol, symbol1: tok1.symbol, decimals0: tok0.decimals, decimals1: tok1.decimals, feePpm, tickSpacing, stable, label });
      } catch (e) { log.warn(`pool ${f.addr} metadata decode failed; excluded`, e); }
    });
    return pools;
  }

  async readStates(pools: PoolRef[], block: bigint): Promise<Map<Address, PoolState>> {
    const calls: Call[] = []; const spans: number[] = [];
    for (const p of pools) { spans.push(calls.length); calls.push(...(p.dex === 'UNISWAP_V3' ? uni.uniV3StateCalls(p.address) : p.dex === 'AERODROME_CL' ? slip.slipstreamStateCalls(p.address) : aero.aeroStateCalls(p.address, p.stable!))); }
    const res = await multicall(this.cc, calls, block);
    const out = new Map<Address, PoolState>();
    pools.forEach((p, i) => {
      const b = spans[i];
      try {
        if (p.dex === 'AERODROME_V2') { const s = aero.aeroDecodeState(res[b].returnData, res[b + 1].returnData); out.set(p.address, { pool: p.address, block, reserve0: s.reserve0, reserve1: s.reserve1, feePpm: s.feePpm }); }
        else { const s = p.dex === 'UNISWAP_V3' ? uni.uniV3DecodeState(res[b].returnData, res[b + 1].returnData, res[b + 2].returnData) : slip.slipstreamDecodeState(res[b].returnData, res[b + 1].returnData, res[b + 2].returnData); out.set(p.address, { pool: p.address, block, sqrtPriceX96: s.sqrtPriceX96, tick: s.tick, liquidity: s.liquidity, feePpm: s.feePpm }); }
      } catch (e) { log.debug(`state decode failed for ${p.label}`, e); }
    });
    this.updateRefPrices(pools, out);
    for (const p of pools) { const s = out.get(p.address); if (s) s.tvlUsd = await this.estimateTvlUsd(p, s, block); }
    return out;
  }

  /** Reference USD prices from the discovered pools themselves (USDC = 1). Used only for TVL filtering and gas conversion. */
  private updateRefPrices(pools: PoolRef[], states: Map<Address, PoolState>) {
    const usdc = this.tokenMap.USDC?.address.toLowerCase();
    if (!usdc) return;
    this.refPrices.set(usdc, 1);
    for (const p of pools) {
      const s = states.get(p.address); if (!s) continue;
      const is0usdc = p.token0.toLowerCase() === usdc, is1usdc = p.token1.toLowerCase() === usdc;
      if (!is0usdc && !is1usdc) continue;
      let price1per0: number | undefined;
      if (s.sqrtPriceX96 && s.sqrtPriceX96 > 0n && (s.liquidity ?? 0n) > 0n) price1per0 = sqrtPriceToPrice(s.sqrtPriceX96, p.decimals0, p.decimals1);
      else if (s.reserve0 && s.reserve1 && s.reserve0 > 0n && !p.stable) price1per0 = (Number(s.reserve1) / 10 ** p.decimals1) / (Number(s.reserve0) / 10 ** p.decimals0);
      if (!price1per0 || !Number.isFinite(price1per0)) continue;
      const other = is0usdc ? p.token1.toLowerCase() : p.token0.toLowerCase();
      const usd = is0usdc ? 1 / price1per0 : price1per0;
      // prefer the deepest pool: keep the first good value per block unless this pool is a CL pool with more liquidity (simple heuristic)
      if (!this.refPrices.has(other) || p.dex !== 'AERODROME_V2') this.refPrices.set(other, usd);
    }
  }
  refPriceUsd(token: Address): number | undefined { return this.refPrices.get(token.toLowerCase()); }

  private async estimateTvlUsd(p: PoolRef, s: PoolState, block: bigint): Promise<number | undefined> {
    const p0 = this.refPrices.get(p.token0.toLowerCase()), p1 = this.refPrices.get(p.token1.toLowerCase());
    if (p0 === undefined || p1 === undefined) return undefined;
    if (s.reserve0 !== undefined && s.reserve1 !== undefined) return Number(s.reserve0) / 10 ** p.decimals0 * p0 + Number(s.reserve1) / 10 ** p.decimals1 * p1;
    // CL pools: use actual token balances held by the pool (upper bound of TVL; active liquidity may be lower)
    const r = await multicall(this.cc, [
      { target: p.token0, callData: encodeFunctionData({ abi: erc20Abi, functionName: 'balanceOf', args: [p.address] }) },
      { target: p.token1, callData: encodeFunctionData({ abi: erc20Abi, functionName: 'balanceOf', args: [p.address] }) }], block);
    if (!r[0].success || !r[1].success) return undefined;
    return Number(BigInt(r[0].returnData)) / 10 ** p.decimals0 * p0 + Number(BigInt(r[1].returnData)) / 10 ** p.decimals1 * p1;
  }

  async quoteExactInputBatch(reqs: Array<{ pool: PoolRef; tokenIn: Address; amountIn: bigint }>, block: bigint): Promise<Quote[]> {
    if (!reqs.length) return [];
    const calls = reqs.map(r => r.pool.dex === 'UNISWAP_V3' ? uni.uniV3QuoteCall(r.pool, r.tokenIn, r.amountIn) : r.pool.dex === 'AERODROME_CL' ? slip.slipstreamQuoteCall(r.pool, r.tokenIn, r.amountIn) : aero.aeroQuoteCall(r.pool, r.tokenIn, r.amountIn));
    // Quoter calls are expensive (they simulate the swap); keep chunks small to stay under provider gas caps.
    const res = await multicall(this.cc, calls, block, 24);
    const states = await this.readStates(reqs.map(r => r.pool).filter((p, i, a) => a.findIndex(x => x.address === p.address) === i), block);
    return reqs.map((r, i) => {
      const tokenOut = r.tokenIn.toLowerCase() === r.pool.token0.toLowerCase() ? r.pool.token1 : r.pool.token0;
      const base: Quote = { pool: r.pool.address, tokenIn: r.tokenIn, tokenOut, amountIn: r.amountIn, amountOut: 0n, ok: false, feePaid: r.amountIn * BigInt(r.pool.feePpm) / 1_000_000n };
      const x = res[i];
      if (!x.success || x.returnData === '0x') return { ...base, error: x.returnData && x.returnData !== '0x' ? `revert ${x.returnData.slice(0, 10)}` : 'quote reverted (insufficient liquidity / price limit)' };
      try {
        const st = states.get(r.pool.address);
        if (r.pool.dex === 'AERODROME_V2') {
          const amountOut = aero.aeroDecodeQuote(x.returnData);
          let impact: number | undefined;
          if (st?.reserve0 !== undefined && st.reserve1 !== undefined) impact = aeroPriceImpactBps({ reserve0: st.reserve0, reserve1: st.reserve1, decimals0: r.pool.decimals0, decimals1: r.pool.decimals1, stable: !!r.pool.stable, feeBps: st.feePpm / 100 }, r.amountIn, amountOut, r.tokenIn.toLowerCase() === r.pool.token0.toLowerCase());
          return { ...base, ok: amountOut > 0n, amountOut, priceImpactBps: impact, gasEstimate: BigInt(this.cfg.gasUnitsPerV2Leg), error: amountOut > 0n ? undefined : 'zero output' };
        }
        const q = r.pool.dex === 'UNISWAP_V3' ? uni.uniV3DecodeQuote(x.returnData) : slip.slipstreamDecodeQuote(x.returnData);
        const impact = st?.sqrtPriceX96 ? clPriceImpactBps(st.sqrtPriceX96, q.sqrtPriceX96After) : undefined;
        return { ...base, ok: q.amountOut > 0n, amountOut: q.amountOut, priceImpactBps: impact, sqrtPriceX96After: q.sqrtPriceX96After, gasEstimate: q.gasEstimate, error: q.amountOut > 0n ? undefined : 'zero output' };
      } catch (e: any) { return { ...base, error: `decode: ${e?.message ?? e}` }; }
    });
  }

  async flashLoanInfo(asset: Address, block: bigint): Promise<FlashLoanInfo> {
    const r = await multicall(this.cc, [
      { target: ADDR.AAVE_POOL, callData: encodeFunctionData({ abi: aavePoolAbi, functionName: 'FLASHLOAN_PREMIUM_TOTAL' }) },
      { target: ADDR.AAVE_POOL, callData: encodeFunctionData({ abi: aavePoolAbi, functionName: 'getReserveData', args: [asset] }) },
    ], block);
    if (!r[0].success || !r[1].success) throw new Error('Aave pool read failed (premium/reserveData)');
    const premiumBps = Number(decodeFunctionResult({ abi: aavePoolAbi, functionName: 'FLASHLOAN_PREMIUM_TOTAL', data: r[0].returnData }) as bigint);
    const rd = decodeFunctionResult({ abi: aavePoolAbi, functionName: 'getReserveData', data: r[1].returnData }) as any;
    const aToken = rd.aTokenAddress as Address;
    const cfgBits = BigInt(rd.configuration);
    const active = ((cfgBits >> 56n) & 1n) === 1n, frozen = ((cfgBits >> 57n) & 1n) === 1n, paused = ((cfgBits >> 60n) & 1n) === 1n, flEnabled = ((cfgBits >> 63n) & 1n) === 1n;
    let available = 0n;
    if (aToken && aToken !== ZERO) {
      const b = await multicall(this.cc, [{ target: asset, callData: encodeFunctionData({ abi: erc20Abi, functionName: 'balanceOf', args: [aToken] }) }], block);
      if (b[0].success) available = BigInt(b[0].returnData);
    }
    return { asset, enabled: active && !frozen && !paused && flEnabled && aToken !== ZERO, premiumBps, availableLiquidity: available, aToken, block };
  }

  async gasInfo(block: bigint, calldataBytes: number): Promise<GasInfo> {
    const blk = await this.cc.call('eth_getBlockByNumber', () => this.cc.client.getBlock({ blockNumber: block }));
    const baseFee = blk.baseFeePerGas ?? 0n;
    let prio = 0n;
    try { prio = await this.cc.call('eth_maxPriorityFeePerGas', () => this.cc.client.estimateMaxPriorityFeePerGas()); } catch { prio = 1_000_000n; /* 0.001 gwei fallback typical on Base */ }
    // L1 data fee: ask the OP GasPriceOracle for a tx-sized payload (signature + fixed fields approximated with 0xff bytes)
    const fake = ('0x' + 'ff'.repeat(calldataBytes + 110)) as Hex;
    let l1 = 0n;
    try {
      const r = await multicall(this.cc, [{ target: ADDR.OP_GAS_PRICE_ORACLE, callData: encodeFunctionData({ abi: opGasPriceOracleAbi, functionName: 'getL1Fee', args: [fake] }) }], block);
      if (r[0].success) l1 = BigInt(r[0].returnData);
    } catch (e) { log.debug('getL1Fee failed', e); }
    const weth = this.tokenMap.WETH?.address; const ethUsdc = weth ? this.refPrices.get(weth.toLowerCase()) ?? 0 : 0;
    return { block, baseFeePerGas: baseFee, maxPriorityFeePerGas: prio, l1FeeWeiForTypicalTx: l1, ethUsdc };
  }
}
