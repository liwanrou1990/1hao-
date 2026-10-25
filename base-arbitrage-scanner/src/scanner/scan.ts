import type { Address } from 'viem';
import type { AppConfig } from '../config.js';
import type { MarketSource, PoolRef, PoolState, Quote, FlashLoanInfo, GasInfo } from '../dex/types.js';
import { enumerateRoutes, type Route } from './routes.js';
import { computeCosts, type CostBreakdown } from './cost.js';
import { sqrtPriceToPrice } from '../pricing/v3math.js';
import { aeroSpotPrice } from '../pricing/aeroMath.js';
import { log } from '../log.js';

export interface Evaluation {
  route: Route; amountIn: bigint; amountOut: bigint; quotes: Quote[]; ok: boolean; failReason?: string; cost?: CostBreakdown;
  amountSource: 'LADDER' | 'OPTIMIZER';
}
export interface BlockScanResult {
  block: bigint; timestamp: number; poolsScanned: number; poolsExcluded: Array<{ pool: PoolRef; reason: string }>; routes: number;
  evaluations: Evaluation[]; candidates: Evaluation[]; fl: FlashLoanInfo; gas: GasInfo; states: Map<Address, PoolState>;
  failures: Record<string, number>;
}

const eq = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

export class ScanEngine {
  private pools: PoolRef[] = [];
  private routes: Route[] = [];
  private refPrices = new Map<string, number>();
  constructor(private src: MarketSource, private cfg: AppConfig) {}

  get poolList() { return this.pools; }
  get routeList() { return this.routes; }

  async init(pools?: PoolRef[]) {
    this.pools = pools ?? await this.src.discoverPools();
    const tokens = Object.values(this.src.tokens());
    const usdc = this.src.tokens().USDC; if (!usdc) throw new Error('USDC token not verified; cannot scan');
    this.routes = enumerateRoutes(this.pools, tokens, usdc.address, { multiHop: this.cfg.enableMultiHop, max: this.cfg.maxRoutesPerBlock });
    log.info(`engine: ${this.pools.length} pools, ${this.routes.length} routes (multiHop=${this.cfg.enableMultiHop})`);
  }

  /** mid price (tokenOut per tokenIn, decimal adjusted) of a leg from pool state; used for theoretical spread only */
  private spotPrice(pool: PoolRef, tokenIn: Address, st?: PoolState): number {
    if (!st) return NaN;
    const in0 = eq(tokenIn, pool.token0);
    if (st.sqrtPriceX96 !== undefined) { const p1per0 = sqrtPriceToPrice(st.sqrtPriceX96, pool.decimals0, pool.decimals1); return in0 ? p1per0 : 1 / p1per0; }
    if (st.reserve0 !== undefined && st.reserve1 !== undefined) {
      const raw = aeroSpotPrice({ reserve0: st.reserve0, reserve1: st.reserve1, decimals0: pool.decimals0, decimals1: pool.decimals1, stable: !!pool.stable, feeBps: 0 }, in0);
      return raw * 10 ** (in0 ? pool.decimals0 - pool.decimals1 : pool.decimals1 - pool.decimals0);
    }
    return NaN;
  }

  private updateRefPrices(states: Map<Address, PoolState>) {
    const usdc = this.src.tokens().USDC.address;
    this.refPrices.set(usdc.toLowerCase(), 1);
    // choose, per token, the USDC pool with the largest TVL as reference
    const best = new Map<string, { tvl: number; price: number }>();
    for (const p of this.pools) {
      const st = states.get(p.address); if (!st) continue;
      const is0 = eq(p.token0, usdc), is1 = eq(p.token1, usdc); if (!is0 && !is1) continue;
      const other = (is0 ? p.token1 : p.token0).toLowerCase();
      const sp = this.spotPrice(p, is0 ? p.token1 : p.token0, st); // USDC per other
      if (!Number.isFinite(sp) || sp <= 0) continue;
      const tvl = st.tvlUsd ?? 0;
      const cur = best.get(other); if (!cur || tvl > cur.tvl) best.set(other, { tvl, price: sp });
    }
    for (const [t, v] of best) this.refPrices.set(t, v.price);
  }
  refPriceUsd = (t: Address) => this.refPrices.get(t.toLowerCase());

  /** Chain quotes leg by leg for many (route, amount) pairs using batched independent quotes per stage. */
  private async quoteRoutes(items: Array<{ route: Route; amountIn: bigint }>, block: bigint): Promise<Array<{ quotes: Quote[]; amountOut: bigint; ok: boolean; failReason?: string }>> {
    const maxHops = Math.max(0, ...items.map(i => i.route.hops));
    const state = items.map(i => ({ quotes: [] as Quote[], amount: i.amountIn, ok: true, failReason: undefined as string | undefined }));
    for (let h = 0; h < maxHops; h++) {
      const reqKey = new Map<string, number>(); const reqs: Array<{ pool: PoolRef; tokenIn: Address; amountIn: bigint }> = []; const idx: Array<number | undefined> = [];
      items.forEach((it, i) => {
        const s = state[i]; const leg = it.route.legs[h];
        if (!s.ok || !leg) { idx.push(undefined); return; }
        const k = `${leg.pool.address}|${leg.tokenIn}|${s.amount}`;
        let r = reqKey.get(k); if (r === undefined) { r = reqs.length; reqKey.set(k, r); reqs.push({ pool: leg.pool, tokenIn: leg.tokenIn, amountIn: s.amount }); }
        idx.push(r);
      });
      if (!reqs.length) break;
      const quotes = await this.src.quoteExactInputBatch(reqs, block);
      items.forEach((_it, i) => { const r = idx[i]; if (r === undefined) return; const q = quotes[r]; const s = state[i]; s.quotes.push(q); if (!q.ok) { s.ok = false; s.failReason = `leg${h + 1}: ${q.error ?? 'quote failed'}`; } else s.amount = q.amountOut; });
    }
    return state.map(s => ({ quotes: s.quotes, amountOut: s.ok ? s.amount : 0n, ok: s.ok, failReason: s.failReason }));
  }

  async scanBlock(block: bigint): Promise<BlockScanResult> {
    const usdc = this.src.tokens().USDC;
    const failures: Record<string, number> = {};
    const fail = (r: string) => { failures[r] = (failures[r] ?? 0) + 1; };
    const [timestamp, states, fl] = await Promise.all([this.src.blockTimestamp(block), this.src.readStates(this.pools, block), this.src.flashLoanInfo(usdc.address, block)]);
    this.updateRefPrices(states);
    // pool filtering
    const poolsExcluded: BlockScanResult['poolsExcluded'] = [];
    const active = this.pools.filter(p => {
      const st = states.get(p.address);
      if (!st) { poolsExcluded.push({ pool: p, reason: 'state_read_failed' }); return false; }
      if (st.sqrtPriceX96 !== undefined && (st.liquidity ?? 0n) === 0n) { poolsExcluded.push({ pool: p, reason: 'zero_active_liquidity' }); return false; }
      if (st.reserve0 !== undefined && (st.reserve0 === 0n || st.reserve1 === 0n)) { poolsExcluded.push({ pool: p, reason: 'empty_reserves' }); return false; }
      if (st.tvlUsd !== undefined && st.tvlUsd < this.cfg.minPoolTvlUsd) { poolsExcluded.push({ pool: p, reason: `tvl_below_min(${st.tvlUsd.toFixed(0)}<${this.cfg.minPoolTvlUsd})` }); return false; }
      return true;
    });
    const activeSet = new Set(active.map(p => p.address));
    const routes = this.routes.filter(r => r.legs.every(l => activeSet.has(l.pool.address)));
    const gas = await this.src.gasInfo(block, 4 + 32 * (3 + 4 * 3));
    if (!gas.ethUsdc) { const w = this.src.tokens().WETH; if (w) gas.ethUsdc = this.refPriceUsd(w.address) ?? 0; }

    // amount ladder, capped by Aave available liquidity
    const ladder = this.cfg.amountLadderUsd.map(u => BigInt(Math.round(u)) * 10n ** BigInt(usdc.decimals));
    const items: Array<{ route: Route; amountIn: bigint; src: Evaluation['amountSource'] }> = [];
    const evaluations: Evaluation[] = [];
    for (const r of routes) for (const a of ladder) {
      if (!fl.enabled) { evaluations.push({ route: r, amountIn: a, amountOut: 0n, quotes: [], ok: false, failReason: 'flashloan_disabled_on_aave', amountSource: 'LADDER' }); fail('flashloan_disabled_on_aave'); continue; }
      if (a > fl.availableLiquidity) { evaluations.push({ route: r, amountIn: a, amountOut: 0n, quotes: [], ok: false, failReason: 'flashloan_capacity_exceeded', amountSource: 'LADDER' }); fail('flashloan_capacity_exceeded'); continue; }
      items.push({ route: r, amountIn: a, src: 'LADDER' });
    }
    const evaluate = (route: Route, amountIn: bigint, q: { quotes: Quote[]; amountOut: bigint; ok: boolean; failReason?: string }, src: Evaluation['amountSource']): Evaluation => {
      if (!q.ok) { fail(q.failReason?.replace(/:.*$/, '') ?? 'quote_failed'); return { route, amountIn, amountOut: 0n, quotes: q.quotes, ok: false, failReason: q.failReason, amountSource: src }; }
      const spot = route.legs.map(l => this.spotPrice(l.pool, l.tokenIn, states.get(l.pool.address)));
      const cost = computeCosts({ cfg: this.cfg, route, quotes: q.quotes, amountIn, amountOut: q.amountOut, usdcDecimals: usdc.decimals, fl, gas, refPriceUsd: this.refPriceUsd, spotPrices: spot });
      return { route, amountIn, amountOut: q.amountOut, quotes: q.quotes, ok: true, cost, amountSource: src };
    };
    const res = await this.quoteRoutes(items, block);
    items.forEach((it, i) => evaluations.push(evaluate(it.route, it.amountIn, res[i], it.src)));

    // optimizer: golden-section search on amount for the routes whose ladder net is best (top 5), bounded by ladder range and Aave capacity
    const okEvals = evaluations.filter(e => e.ok && e.cost);
    const bestByRoute = new Map<string, Evaluation>();
    for (const e of okEvals) { const c = bestByRoute.get(e.route.id); if (!c || e.cost!.netProfitUsd > c.cost!.netProfitUsd) bestByRoute.set(e.route.id, e); }
    const top = [...bestByRoute.values()].sort((a, b) => b.cost!.netProfitUsd - a.cost!.netProfitUsd).slice(0, 5).filter(e => e.cost!.grossProfitUsd > 0);
    for (const t of top) {
      let lo = ladder[0], hi = ladder[ladder.length - 1]; if (fl.availableLiquidity < hi) hi = fl.availableLiquidity;
      if (hi <= lo) continue;
      const phi = 0.618033988749895;
      let a = lo + BigInt(Math.floor(Number(hi - lo) * (1 - phi))), b = lo + BigInt(Math.floor(Number(hi - lo) * phi));
      const cache = new Map<string, number>();
      const f = async (x: bigint[]) => { const need = x.filter(v => !cache.has(v.toString())); if (need.length) { const rs = await this.quoteRoutes(need.map(v => ({ route: t.route, amountIn: v })), block); need.forEach((v, i) => { const ev = evaluate(t.route, v, rs[i], 'OPTIMIZER'); evaluations.push(ev); cache.set(v.toString(), ev.ok ? ev.cost!.netProfitUsd : -Infinity); }); } return x.map(v => cache.get(v.toString())!); };
      for (let iter = 0; iter < 8; iter++) {
        const [fa, fb] = await f([a, b]);
        if (fa > fb) { hi = b; b = a; a = lo + BigInt(Math.floor(Number(hi - lo) * (1 - phi))); } else { lo = a; a = b; b = lo + BigInt(Math.floor(Number(hi - lo) * phi)); }
        if (hi - lo < 100n * 10n ** BigInt(usdc.decimals)) break;
      }
    }
    const candidates = evaluations.filter(e => e.ok && e.cost && e.cost.netProfitUsd > 0).sort((x, y) => y.cost!.netProfitUsd - x.cost!.netProfitUsd);
    return { block, timestamp, poolsScanned: active.length, poolsExcluded, routes: routes.length, evaluations, candidates, fl, gas, states, failures };
  }
}
