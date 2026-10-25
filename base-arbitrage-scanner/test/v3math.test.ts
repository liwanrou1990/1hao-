import { describe, it, expect } from 'vitest';
import { getSqrtRatioAtTick, getTickAtSqrtRatio, MIN_TICK, MAX_TICK, MIN_SQRT_RATIO, MAX_SQRT_RATIO, Q96, swapExactIn, computeSwapStep, priceToSqrtPriceX96, sqrtPriceToPrice, clPriceImpactBps, type CLPoolModel } from '../src/pricing/v3math.js';

describe('Uniswap V3 TickMath port', () => {
  it('matches reference vectors', () => {
    expect(getSqrtRatioAtTick(0)).toBe(Q96);
    expect(getSqrtRatioAtTick(MIN_TICK)).toBe(MIN_SQRT_RATIO);
    expect(getSqrtRatioAtTick(MAX_TICK)).toBe(MAX_SQRT_RATIO);
    expect(getSqrtRatioAtTick(1)).toBe(79232123823359799118286999568n);
    expect(getSqrtRatioAtTick(-1)).toBe(79224201403219477170569942574n);
  });
  it('tick <-> sqrtRatio round trips', () => {
    for (const t of [-887000, -200000, -1000, -1, 0, 1, 777, 193_000, 500_000, 887000]) {
      const s = getSqrtRatioAtTick(t);
      expect(getTickAtSqrtRatio(s)).toBe(t);
      expect(getTickAtSqrtRatio(s + 1n)).toBe(t);
    }
  });
  it('price helpers are consistent for USDC(6)/WETH(18)-style decimals', () => {
    const s = priceToSqrtPriceX96(1 / 4000, 18, 6); // token0=WETH, token1=USDC? here price1per0 = USDC per WETH = 4000 → pass decimals accordingly
    const p = sqrtPriceToPrice(s, 18, 6);
    expect(p).toBeCloseTo(1 / 4000, 9);
  });
});

function pool(price1per0: number, L: bigint, feePpm = 500): CLPoolModel {
  const sqrtP = priceToSqrtPriceX96(price1per0, 18, 6);
  const tick = getTickAtSqrtRatio(sqrtP);
  return { sqrtPriceX96: sqrtP, tick, liquidity: L, feePpm, tickSpacing: 10, ticks: [{ tick: tick - 50_000, liquidityNet: L }, { tick: tick + 50_000, liquidityNet: -L }] };
}

describe('Uniswap V3 swap math', () => {
  it('computeSwapStep charges exactly feePips on exact-input within range', () => {
    const p = pool(4000, 10n ** 18n);
    const step = computeSwapStep(p.sqrtPriceX96, p.sqrtPriceX96 / 2n, p.liquidity, 1_000_000n, 3000);
    expect(step.amountIn + step.feeAmount).toBe(1_000_000n);
    expect(Math.abs(Number(step.feeAmount) - 3000)).toBeLessThanOrEqual(2); // 0.3% of 1e6 = 3000 (rounding)
  });
  it('exact-input swap: more input → more output, convex (price impact grows), fee ≈ feePpm', () => {
    const p = pool(4000, 10n ** 22n);
    const a = swapExactIn(p, true, 10n ** 18n);  // sell 1 WETH (token0) for USDC
    const b = swapExactIn(p, true, 10n ** 19n);  // sell 10 WETH
    expect(b.amountOut).toBeGreaterThan(a.amountOut);
    expect(Number(b.amountOut)).toBeLessThan(Number(a.amountOut) * 10); // convexity: price impact
    expect(Number(a.feeAmount) / 1e18).toBeCloseTo(0.0005, 6);
    expect(a.sqrtPriceX96After).toBeLessThan(p.sqrtPriceX96);
    expect(clPriceImpactBps(p.sqrtPriceX96, a.sqrtPriceX96After)).toBeGreaterThan(0);
  });
  it('crosses initialized ticks and fails cleanly when liquidity is exhausted', () => {
    const p = pool(4000, 10n ** 18n);
    const small = swapExactIn(p, false, 1_000_000n); // buy WETH with 1 USDC
    expect(small.amountOut).toBeGreaterThan(0n);
    expect(() => swapExactIn(p, false, 10n ** 30n)).toThrow(/insufficient liquidity/);
  });
  it('is deterministic', () => {
    const p = pool(4000, 10n ** 22n);
    expect(swapExactIn(p, true, 5n * 10n ** 18n)).toEqual(swapExactIn(p, true, 5n * 10n ** 18n));
  });
});
