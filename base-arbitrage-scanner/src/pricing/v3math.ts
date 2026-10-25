/**
 * Exact-integer port of Uniswap V3 core math (TickMath, SqrtPriceMath, SwapMath, FullMath).
 * Used for (a) offline fixture pools, (b) price-impact computation from sqrtPrice, (c) unit tests.
 */
export const Q96 = 1n << 96n;
export const Q128 = 1n << 128n;
export const MIN_TICK = -887272;
export const MAX_TICK = 887272;
export const MIN_SQRT_RATIO = 4295128739n;
export const MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342n;
const MAX_U256 = (1n << 256n) - 1n;
const MAX_U160 = (1n << 160n) - 1n;

export function mulDiv(a: bigint, b: bigint, d: bigint): bigint { if (d === 0n) throw new Error('div0'); return (a * b) / d; }
export function mulDivRoundingUp(a: bigint, b: bigint, d: bigint): bigint { const p = a * b; const r = p / d; return p % d === 0n ? r : r + 1n; }
function divRoundingUp(a: bigint, b: bigint): bigint { return a % b === 0n ? a / b : a / b + 1n; }

export function getSqrtRatioAtTick(tick: number): bigint {
  if (tick < MIN_TICK || tick > MAX_TICK) throw new Error('T');
  const absTick = BigInt(Math.abs(tick));
  let ratio = (absTick & 0x1n) !== 0n ? 0xfffcb933bd6fad37aa2d162d1a594001n : 0x100000000000000000000000000000000n;
  const c: [bigint, bigint][] = [
    [0x2n, 0xfff97272373d413259a46990580e213an], [0x4n, 0xfff2e50f5f656932ef12357cf3c7fdccn], [0x8n, 0xffe5caca7e10e4e61c3624eaa0941cd0n],
    [0x10n, 0xffcb9843d60f6159c9db58835c926644n], [0x20n, 0xff973b41fa98c081472e6896dfb254c0n], [0x40n, 0xff2ea16466c96a3843ec78b326b52861n],
    [0x80n, 0xfe5dee046a99a2a811c461f1969c3053n], [0x100n, 0xfcbe86c7900a88aedcffc83b479aa3a4n], [0x200n, 0xf987a7253ac413176f2b074cf7815e54n],
    [0x400n, 0xf3392b0822b70005940c7a398e4b70f3n], [0x800n, 0xe7159475a2c29b7443b29c7fa6e889d9n], [0x1000n, 0xd097f3bdfd2022b8845ad8f792aa5825n],
    [0x2000n, 0xa9f746462d870fdf8a65dc1f90e061e5n], [0x4000n, 0x70d869a156d2a1b890bb3df62baf32f7n], [0x8000n, 0x31be135f97d08fd981231505542fcfa6n],
    [0x10000n, 0x9aa508b5b7a84e1c677de54f3e99bc9n], [0x20000n, 0x5d6af8dedb81196699c329225ee604n], [0x40000n, 0x2216e584f5fa1ea926041bedfe98n],
    [0x80000n, 0x48a170391f7dc42444e8fa2n],
  ];
  for (const [bit, mul] of c) if ((absTick & bit) !== 0n) ratio = (ratio * mul) >> 128n;
  if (tick > 0) ratio = MAX_U256 / ratio;
  return (ratio >> 32n) + ((ratio % (1n << 32n)) === 0n ? 0n : 1n);
}

export function getTickAtSqrtRatio(sqrtPriceX96: bigint): number {
  if (sqrtPriceX96 < MIN_SQRT_RATIO || sqrtPriceX96 >= MAX_SQRT_RATIO) throw new Error('R');
  // binary search on exact function (slower than the assembly log but exact and simple)
  let lo = MIN_TICK, hi = MAX_TICK;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (getSqrtRatioAtTick(mid) <= sqrtPriceX96) lo = mid; else hi = mid - 1;
  }
  return lo;
}

export function getAmount0Delta(sqrtA: bigint, sqrtB: bigint, liquidity: bigint, roundUp: boolean): bigint {
  if (sqrtA > sqrtB) [sqrtA, sqrtB] = [sqrtB, sqrtA];
  const num1 = liquidity << 96n;
  const num2 = sqrtB - sqrtA;
  if (sqrtA <= 0n) throw new Error('sqrtA<=0');
  return roundUp ? divRoundingUp(mulDivRoundingUp(num1, num2, sqrtB), sqrtA) : mulDiv(num1, num2, sqrtB) / sqrtA;
}
export function getAmount1Delta(sqrtA: bigint, sqrtB: bigint, liquidity: bigint, roundUp: boolean): bigint {
  if (sqrtA > sqrtB) [sqrtA, sqrtB] = [sqrtB, sqrtA];
  return roundUp ? mulDivRoundingUp(liquidity, sqrtB - sqrtA, Q96) : mulDiv(liquidity, sqrtB - sqrtA, Q96);
}

export function getNextSqrtPriceFromAmount0RoundingUp(sqrtP: bigint, liquidity: bigint, amount: bigint, add: boolean): bigint {
  if (amount === 0n) return sqrtP;
  const numerator1 = liquidity << 96n;
  const product = amount * sqrtP;
  if (add) {
    if (product / amount === sqrtP) { const denominator = numerator1 + product; if (denominator >= numerator1) return mulDivRoundingUp(numerator1, sqrtP, denominator); }
    return divRoundingUp(numerator1, numerator1 / sqrtP + amount);
  } else {
    if (!(product / amount === sqrtP && numerator1 > product)) throw new Error('price underflow');
    return mulDivRoundingUp(numerator1, sqrtP, numerator1 - product);
  }
}
export function getNextSqrtPriceFromAmount1RoundingDown(sqrtP: bigint, liquidity: bigint, amount: bigint, add: boolean): bigint {
  if (add) {
    const quotient = amount <= MAX_U160 ? (amount << 96n) / liquidity : mulDiv(amount, Q96, liquidity);
    return sqrtP + quotient;
  } else {
    const quotient = amount <= MAX_U160 ? divRoundingUp(amount << 96n, liquidity) : mulDivRoundingUp(amount, Q96, liquidity);
    if (sqrtP <= quotient) throw new Error('price underflow');
    return sqrtP - quotient;
  }
}
export function getNextSqrtPriceFromInput(sqrtP: bigint, liquidity: bigint, amountIn: bigint, zeroForOne: boolean): bigint {
  if (sqrtP <= 0n || liquidity <= 0n) throw new Error('bad input');
  return zeroForOne ? getNextSqrtPriceFromAmount0RoundingUp(sqrtP, liquidity, amountIn, true) : getNextSqrtPriceFromAmount1RoundingDown(sqrtP, liquidity, amountIn, true);
}
export function getNextSqrtPriceFromOutput(sqrtP: bigint, liquidity: bigint, amountOut: bigint, zeroForOne: boolean): bigint {
  if (sqrtP <= 0n || liquidity <= 0n) throw new Error('bad input');
  return zeroForOne ? getNextSqrtPriceFromAmount1RoundingDown(sqrtP, liquidity, amountOut, false) : getNextSqrtPriceFromAmount0RoundingUp(sqrtP, liquidity, amountOut, false);
}

/** SwapMath.computeSwapStep — feePips in ppm (hundredths of a bip). */
export function computeSwapStep(sqrtCurrent: bigint, sqrtTarget: bigint, liquidity: bigint, amountRemaining: bigint, feePips: number) {
  const zeroForOne = sqrtCurrent >= sqrtTarget;
  const exactIn = amountRemaining >= 0n;
  const fee = BigInt(feePips);
  let sqrtNext: bigint, amountIn: bigint, amountOut: bigint, feeAmount: bigint;
  if (exactIn) {
    const amountRemainingLessFee = mulDiv(amountRemaining, 1_000_000n - fee, 1_000_000n);
    amountIn = zeroForOne ? getAmount0Delta(sqrtTarget, sqrtCurrent, liquidity, true) : getAmount1Delta(sqrtCurrent, sqrtTarget, liquidity, true);
    if (amountRemainingLessFee >= amountIn) sqrtNext = sqrtTarget;
    else sqrtNext = getNextSqrtPriceFromInput(sqrtCurrent, liquidity, amountRemainingLessFee, zeroForOne);
  } else {
    amountOut = zeroForOne ? getAmount1Delta(sqrtTarget, sqrtCurrent, liquidity, false) : getAmount0Delta(sqrtCurrent, sqrtTarget, liquidity, false);
    if (-amountRemaining >= amountOut) sqrtNext = sqrtTarget;
    else sqrtNext = getNextSqrtPriceFromOutput(sqrtCurrent, liquidity, -amountRemaining, zeroForOne);
  }
  const max = sqrtTarget === sqrtNext;
  if (zeroForOne) {
    amountIn = max && exactIn ? amountIn! : getAmount0Delta(sqrtNext, sqrtCurrent, liquidity, true);
    amountOut = max && !exactIn ? amountOut! : getAmount1Delta(sqrtNext, sqrtCurrent, liquidity, false);
  } else {
    amountIn = max && exactIn ? amountIn! : getAmount1Delta(sqrtCurrent, sqrtNext, liquidity, true);
    amountOut = max && !exactIn ? amountOut! : getAmount0Delta(sqrtCurrent, sqrtNext, liquidity, false);
  }
  if (!exactIn && amountOut > -amountRemaining) amountOut = -amountRemaining;
  if (exactIn && sqrtNext !== sqrtTarget) feeAmount = amountRemaining - amountIn;
  else feeAmount = mulDivRoundingUp(amountIn, fee, 1_000_000n - fee);
  return { sqrtNext, amountIn, amountOut, feeAmount };
}

/** A minimal concentrated-liquidity pool model (ticks with liquidityNet) supporting exact-input swaps. */
export interface CLTick { tick: number; liquidityNet: bigint; }
export interface CLPoolModel { sqrtPriceX96: bigint; tick: number; liquidity: bigint; feePpm: number; tickSpacing: number; ticks: CLTick[]; }

export function nextInitializedTick(ticks: CLTick[], tick: number, lte: boolean): { tick: number; initialized: boolean } {
  if (lte) { let best: number | undefined; for (const t of ticks) if (t.tick <= tick && (best === undefined || t.tick > best)) best = t.tick; return best === undefined ? { tick: MIN_TICK, initialized: false } : { tick: best, initialized: true }; }
  let best: number | undefined; for (const t of ticks) if (t.tick > tick && (best === undefined || t.tick < best)) best = t.tick; return best === undefined ? { tick: MAX_TICK, initialized: false } : { tick: best, initialized: true };
}

export interface CLSwapResult { amountIn: bigint; amountOut: bigint; feeAmount: bigint; sqrtPriceX96After: bigint; tickAfter: number; liquidityAfter: bigint; ticksCrossed: number; }

/** Exact-input swap on the pool model (does not mutate; returns post-state). Throws if liquidity exhausted. */
export function swapExactIn(pool: CLPoolModel, zeroForOne: boolean, amountIn: bigint): CLSwapResult {
  let sqrtP = pool.sqrtPriceX96, tick = pool.tick, liquidity = pool.liquidity;
  let remaining = amountIn, totalOut = 0n, totalFee = 0n, crossed = 0;
  const limit = zeroForOne ? MIN_SQRT_RATIO + 1n : MAX_SQRT_RATIO - 1n;
  let guard = 0;
  while (remaining > 0n && sqrtP !== limit) {
    if (++guard > 5000) throw new Error('swap loop guard');
    const nxt = nextInitializedTick(pool.ticks, zeroForOne ? tick - 1 : tick, zeroForOne);
    // Clamp to tick range
    const nextTick = Math.min(Math.max(nxt.tick, MIN_TICK), MAX_TICK);
    const sqrtNextTick = getSqrtRatioAtTick(nextTick);
    const target = zeroForOne ? (sqrtNextTick < limit ? limit : sqrtNextTick) : (sqrtNextTick > limit ? limit : sqrtNextTick);
    if (liquidity === 0n) {
      // no liquidity in this range: jump to the next initialized tick without swapping
      if (!nxt.initialized) throw new Error('insufficient liquidity');
      sqrtP = target; tick = zeroForOne ? nextTick - 1 : nextTick;
      const t = pool.ticks.find(x => x.tick === nextTick)!; liquidity += zeroForOne ? -t.liquidityNet : t.liquidityNet; crossed++;
      continue;
    }
    const step = computeSwapStep(sqrtP, target, liquidity, remaining, pool.feePpm);
    remaining -= step.amountIn + step.feeAmount; totalOut += step.amountOut; totalFee += step.feeAmount; sqrtP = step.sqrtNext;
    if (sqrtP === sqrtNextTick) {
      if (nxt.initialized) { const t = pool.ticks.find(x => x.tick === nextTick)!; liquidity += zeroForOne ? -t.liquidityNet : t.liquidityNet; crossed++; }
      tick = zeroForOne ? nextTick - 1 : nextTick;
    } else if (sqrtP !== pool.sqrtPriceX96) tick = getTickAtSqrtRatio(sqrtP);
    if (!nxt.initialized && sqrtP === target && remaining > 0n) throw new Error('insufficient liquidity');
  }
  if (remaining > 0n) throw new Error('insufficient liquidity');
  return { amountIn: amountIn - remaining, amountOut: totalOut, feeAmount: totalFee, sqrtPriceX96After: sqrtP, tickAfter: tick, liquidityAfter: liquidity, ticksCrossed: crossed };
}

/** price of token1 per token0 as a float, adjusted for decimals */
export function sqrtPriceToPrice(sqrtPriceX96: bigint, decimals0: number, decimals1: number): number {
  const ratio = Number(sqrtPriceX96) / Number(Q96);
  return ratio * ratio * 10 ** (decimals0 - decimals1);
}
export function priceToSqrtPriceX96(price1Per0: number, decimals0: number, decimals1: number): bigint {
  const raw = price1Per0 * 10 ** (decimals1 - decimals0);
  return BigInt(Math.floor(Math.sqrt(raw) * 2 ** 96));
}

/** Price impact (bps) of a CL swap from pre/post sqrt prices: relative move of the marginal price. */
export function clPriceImpactBps(sqrtBefore: bigint, sqrtAfter: bigint): number {
  const pb = Number(sqrtBefore) ** 2, pa = Number(sqrtAfter) ** 2;
  return Math.abs(pa - pb) / pb * 10_000;
}
