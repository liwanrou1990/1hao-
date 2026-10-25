import { describe, it, expect } from 'vitest';
import { aeroGetAmountOut, aeroPriceImpactBps, type AeroPoolModel } from '../src/pricing/aeroMath.js';

describe('Aerodrome pool math port', () => {
  const vol: AeroPoolModel = { reserve0: 1_000n * 10n ** 18n, reserve1: 4_000_000n * 10n ** 6n, decimals0: 18, decimals1: 6, stable: false, feeBps: 30 };
  it('volatile: constant product after fee', () => {
    const amountIn = 10n ** 18n;
    const out = aeroGetAmountOut(vol, amountIn, true);
    const inAfterFee = amountIn - amountIn * 30n / 10_000n;
    expect(out).toBe(inAfterFee * vol.reserve1 / (vol.reserve0 + inAfterFee));
    expect(Number(out) / 1e6).toBeLessThan(4000 * 0.997);
    expect(Number(out) / 1e6).toBeGreaterThan(4000 * 0.997 * 0.998);
  });
  it('volatile: price impact grows with size and excludes the fee', () => {
    const o1 = aeroGetAmountOut(vol, 10n ** 18n, true), o2 = aeroGetAmountOut(vol, 100n * 10n ** 18n, true);
    const i1 = aeroPriceImpactBps(vol, 10n ** 18n, o1, true), i2 = aeroPriceImpactBps(vol, 100n * 10n ** 18n, o2, true);
    expect(i2).toBeGreaterThan(i1);
    expect(i1).toBeCloseTo(10, 0); // 1 of 1000 reserve ≈ 0.1% ≈ 10 bps
  });
  const stable: AeroPoolModel = { reserve0: 2_000_000n * 10n ** 6n, reserve1: 2_000_000n * 10n ** 6n, decimals0: 6, decimals1: 6, stable: true, feeBps: 5 };
  it('stable: near-balanced pool returns ≈ input minus fee with tiny slippage', () => {
    const amountIn = 100_000n * 10n ** 6n; // 100k of 2M
    const out = aeroGetAmountOut(stable, amountIn, true);
    const ratio = Number(out) / Number(amountIn);
    expect(ratio).toBeLessThan(1 - 0.0005);
    expect(ratio).toBeGreaterThan(1 - 0.0005 - 0.002); // stable curve: < 20 bps slippage for 5% of reserves
  });
  it('stable: slippage is far smaller than a volatile pool of the same depth', () => {
    const volSame: AeroPoolModel = { ...stable, stable: false };
    const amountIn = 100_000n * 10n ** 6n;
    expect(aeroGetAmountOut(stable, amountIn, true)).toBeGreaterThan(aeroGetAmountOut(volSame, amountIn, true));
  });
});
