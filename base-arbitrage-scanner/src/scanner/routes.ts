import type { Address } from 'viem';
import type { PoolRef, TokenInfo } from '../dex/types.js';

export interface Leg { pool: PoolRef; tokenIn: Address; tokenOut: Address; symbolIn: string; symbolOut: string; }
export interface Route { id: string; legs: Leg[]; path: string; hops: number; }

const eq = (a: Address, b: Address) => a.toLowerCase() === b.toLowerCase();
function other(p: PoolRef, t: Address): Address { return eq(p.token0, t) ? p.token1 : p.token0; }
function has(p: PoolRef, a: Address, b: Address) { return (eq(p.token0, a) && eq(p.token1, b)) || (eq(p.token0, b) && eq(p.token1, a)); }
function sym(tokens: TokenInfo[], a: Address) { return tokens.find(t => eq(t.address, a))?.symbol ?? a.slice(0, 8); }

/**
 * Enumerate closed-loop routes starting and ending in `base` (USDC):
 *  - 2-hop: base -> T on pool A, T -> base on pool B (A != B; includes same DEX different fee tiers and cross-DEX)
 *  - 3-hop: base -> T1 -> T2 -> base over three distinct pools (when enabled)
 * Both directions are covered because every ordered pair (A,B) is generated.
 */
export function enumerateRoutes(pools: PoolRef[], tokens: TokenInfo[], base: Address, opts: { multiHop: boolean; max: number }): Route[] {
  const routes: Route[] = [];
  const nonBase = tokens.filter(t => !eq(t.address, base));
  const mk = (legs: Leg[]): Route => ({ id: legs.map(l => `${l.pool.address.toLowerCase()}:${l.tokenIn.toLowerCase()}`).join('>'), legs, path: [legs[0].symbolIn, ...legs.map(l => l.symbolOut)].join('->') + ' | ' + legs.map(l => l.pool.label).join(' => '), hops: legs.length });
  const leg = (pool: PoolRef, tokenIn: Address): Leg => ({ pool, tokenIn, tokenOut: other(pool, tokenIn), symbolIn: sym(tokens, tokenIn), symbolOut: sym(tokens, other(pool, tokenIn)) });

  for (const t of nonBase) {
    const ps = pools.filter(p => has(p, base, t.address));
    for (const a of ps) for (const b of ps) if (a.address !== b.address) routes.push(mk([leg(a, base), leg(b, t.address)]));
  }
  if (opts.multiHop) {
    for (const t1 of nonBase) for (const t2 of nonBase) {
      if (eq(t1.address, t2.address)) continue;
      const pa = pools.filter(p => has(p, base, t1.address)), pb = pools.filter(p => has(p, t1.address, t2.address)), pc = pools.filter(p => has(p, t2.address, base));
      for (const a of pa) for (const b of pb) for (const c of pc) if (a.address !== b.address && b.address !== c.address && a.address !== c.address) routes.push(mk([leg(a, base), leg(b, t1.address), leg(c, t2.address)]));
    }
  }
  return routes.slice(0, opts.max);
}
