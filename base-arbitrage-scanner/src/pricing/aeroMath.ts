/**
 * Port of Aerodrome (Velodrome v2) Pool.getAmountOut math for volatile (x*y=k) and stable (x^3y + xy^3 = k) pools.
 * fee is in basis points (Aerodrome factory: 1 = 0.01%).
 */
const E18 = 10n ** 18n;

export interface AeroPoolModel { reserve0: bigint; reserve1: bigint; decimals0: number; decimals1: number; stable: boolean; feeBps: number; }

function _k(x: bigint, y: bigint, stable: boolean, d0: bigint, d1: bigint): bigint {
  if (stable) {
    const _x = (x * E18) / d0, _y = (y * E18) / d1;
    const _a = (_x * _y) / E18;
    const _b = (_x * _x) / E18 + (_y * _y) / E18;
    return (_a * _b) / E18;
  }
  return x * y;
}
function _f(x0: bigint, y: bigint): bigint { return (x0 * ((((y * y) / E18) * y) / E18)) / E18 + (((((x0 * x0) / E18) * x0) / E18) * y) / E18; }
function _d(x0: bigint, y: bigint): bigint { return (3n * x0 * ((y * y) / E18)) / E18 + ((((x0 * x0) / E18) * x0) / E18); }
function _get_y(x0: bigint, xy: bigint, y: bigint): bigint {
  for (let i = 0; i < 255; i++) {
    const k = _f(x0, y);
    if (k < xy) {
      let dy = ((xy - k) * E18) / _d(x0, y);
      if (dy === 0n) { if (k === xy) return y; if (_k_check(x0, y + 1n, xy)) return y + 1n; dy = 1n; }
      y = y + dy;
    } else {
      let dy = ((k - xy) * E18) / _d(x0, y);
      if (dy === 0n) { if (k === xy || _f(x0, y - 1n) < xy) return y; dy = 1n; }
      y = y - dy;
    }
  }
  throw new Error('!y');
}
function _k_check(x0: bigint, y: bigint, xy: bigint): boolean { return _f(x0, y) > xy; }

export function aeroGetAmountOut(p: AeroPoolModel, amountIn: bigint, tokenInIs0: boolean): bigint {
  const d0 = 10n ** BigInt(p.decimals0), d1 = 10n ** BigInt(p.decimals1);
  amountIn -= (amountIn * BigInt(p.feeBps)) / 10_000n; // remove fee from amount received
  const [reserveA, reserveB] = tokenInIs0 ? [p.reserve0, p.reserve1] : [p.reserve1, p.reserve0];
  if (p.stable) {
    const xy = _k(p.reserve0, p.reserve1, true, d0, d1);
    const r0 = (p.reserve0 * E18) / d0, r1 = (p.reserve1 * E18) / d1;
    const [ra, rb] = tokenInIs0 ? [r0, r1] : [r1, r0];
    const ain = tokenInIs0 ? (amountIn * E18) / d0 : (amountIn * E18) / d1;
    const y = rb - _get_y(ain + ra, xy, rb);
    return (y * (tokenInIs0 ? d1 : d0)) / E18;
  }
  return (amountIn * reserveB) / (reserveA + amountIn);
}

/** Mid price of tokenOut per tokenIn for an infinitesimal trade (used for price impact). */
export function aeroSpotPrice(p: AeroPoolModel, tokenInIs0: boolean): number {
  const tiny = 10n ** BigInt(tokenInIs0 ? p.decimals0 : p.decimals1) / 1000n || 1n;
  const fee = p.feeBps;
  const noFee: AeroPoolModel = { ...p, feeBps: 0 };
  const out = aeroGetAmountOut(noFee, tiny, tokenInIs0);
  void fee;
  return Number(out) / Number(tiny);
}

export function aeroPriceImpactBps(p: AeroPoolModel, amountIn: bigint, amountOut: bigint, tokenInIs0: boolean): number {
  const spot = aeroSpotPrice(p, tokenInIs0);
  const feeAdj = 1 - p.feeBps / 10_000;
  const exec = Number(amountOut) / Number(amountIn);
  // impact excludes the explicit fee component so that fee and impact are not double counted
  const impact = 1 - exec / (spot * feeAdj);
  return Math.max(0, impact * 10_000);
}
