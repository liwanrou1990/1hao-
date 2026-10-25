import type { AppConfig } from '../config.js';
import type { GasInfo, FlashLoanInfo, Quote } from '../dex/types.js';
import type { Route } from './routes.js';

export interface CostBreakdown {
  amountInUsd: number;
  amountOutUsd: number;
  /** final USDC out - USDC in. DEX fees and price impact are already embedded here (quotes are executable ExactInput amounts). */
  grossProfitUsd: number;
  /** spread implied by mid prices with zero fee and zero impact (theoretical, NOT tradable) */
  theoreticalSpreadUsd: number;
  dexFeesUsd: number;          // sum over legs of amountIn_leg * fee, converted to USD (embedded in gross)
  priceImpactUsd: number;      // theoreticalSpread - gross - dexFees (embedded in gross), floored at 0
  flashLoanPremiumUsd: number; // Aave FLASHLOAN_PREMIUM_TOTAL * amountIn
  gasL2Usd: number;            // gasUnits * (baseFee + priorityFee) in USD
  gasL1DataUsd: number;        // OP-stack L1 data fee in USD
  priorityFeeUsd: number;      // the priority-fee share of gasL2Usd (reported separately)
  executionBufferUsd: number;  // slippage/adverse-move buffer
  totalCostUsd: number;        // flash premium + gas (L2+L1) + buffer   (costs NOT embedded in gross)
  netProfitUsd: number;        // gross - totalCost
  roi: number;                 // net / amountIn
  gasUnits: number;
}

export function estimateGasUnits(route: Route, cfg: AppConfig, quotes?: Quote[]): number {
  let units = cfg.gasUnitsBase + cfg.gasUnitsFlashLoanOverhead;
  route.legs.forEach((l, i) => {
    const q = quotes?.[i];
    if (l.pool.dex === 'AERODROME_V2') units += cfg.gasUnitsPerV2Leg;
    else units += q?.gasEstimate && q.gasEstimate > 0n ? Number(q.gasEstimate) + 25_000 /* callback+transfer overhead */ : cfg.gasUnitsPerV3Leg;
  });
  return units;
}

export function computeCosts(params: {
  cfg: AppConfig; route: Route; quotes: Quote[]; amountIn: bigint; amountOut: bigint; usdcDecimals: number;
  fl: FlashLoanInfo; gas: GasInfo; refPriceUsd: (token: `0x${string}`) => number | undefined; spotPrices: number[] /* per leg: tokenOut per tokenIn mid-price, decimal-adjusted */;
}): CostBreakdown {
  const { cfg, route, quotes, amountIn, amountOut, usdcDecimals, fl, gas } = params;
  const toUsd = (v: bigint, dec: number) => Number(v) / 10 ** dec;
  const amountInUsd = toUsd(amountIn, usdcDecimals), amountOutUsd = toUsd(amountOut, usdcDecimals);
  const grossProfitUsd = amountOutUsd - amountInUsd;
  // theoretical spread: push amountIn through mid prices with no fees/impact
  let theo = amountInUsd; for (const sp of params.spotPrices) theo *= sp;
  const theoreticalSpreadUsd = Number.isFinite(theo) ? theo - amountInUsd : 0;
  let dexFeesUsd = 0;
  route.legs.forEach((l, i) => {
    const q = quotes[i]; if (!q) return;
    const decIn = l.tokenIn.toLowerCase() === l.pool.token0.toLowerCase() ? l.pool.decimals0 : l.pool.decimals1;
    const price = params.refPriceUsd(l.tokenIn) ?? (i === 0 ? 1 : 0);
    dexFeesUsd += toUsd(q.feePaid ?? 0n, decIn) * price;
  });
  const priceImpactUsd = Math.max(0, theoreticalSpreadUsd - grossProfitUsd - dexFeesUsd);
  const flashLoanPremiumUsd = amountInUsd * fl.premiumBps / 10_000;
  const gasUnits = estimateGasUnits(route, cfg, quotes);
  const ethUsd = gas.ethUsdc || 0;
  const weiToUsd = (w: bigint) => Number(w) / 1e18 * ethUsd;
  const gasL2Usd = weiToUsd((gas.baseFeePerGas + gas.maxPriorityFeePerGas) * BigInt(gasUnits));
  const priorityFeeUsd = weiToUsd(gas.maxPriorityFeePerGas * BigInt(gasUnits));
  const gasL1DataUsd = weiToUsd(gas.l1FeeWeiForTypicalTx);
  const executionBufferUsd = amountInUsd * cfg.slippageBufferBps / 10_000;
  const totalCostUsd = flashLoanPremiumUsd + gasL2Usd + gasL1DataUsd + executionBufferUsd;
  const netProfitUsd = grossProfitUsd - totalCostUsd;
  return { amountInUsd, amountOutUsd, grossProfitUsd, theoreticalSpreadUsd, dexFeesUsd, priceImpactUsd, flashLoanPremiumUsd, gasL2Usd, gasL1DataUsd, priorityFeeUsd, executionBufferUsd, totalCostUsd, netProfitUsd, roi: amountInUsd > 0 ? netProfitUsd / amountInUsd : 0, gasUnits };
}
