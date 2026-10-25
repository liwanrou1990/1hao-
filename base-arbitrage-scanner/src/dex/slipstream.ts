import { encodeFunctionData, decodeFunctionResult, type Address, type Hex } from 'viem';
import { slipstreamFactoryAbi, slipstreamPoolAbi, slipstreamQuoterV2Abi } from '../chain/abis.js';
import { ADDR } from '../chain/addresses.js';
import type { PoolRef, TokenInfo } from './types.js';

export const slipstreamTickSpacingsCall = () => ({ target: ADDR.SLIPSTREAM_CL_FACTORY, callData: encodeFunctionData({ abi: slipstreamFactoryAbi, functionName: 'tickSpacings' }) });
export const decodeTickSpacings = (d: Hex) => (decodeFunctionResult({ abi: slipstreamFactoryAbi, functionName: 'tickSpacings', data: d }) as readonly number[]).map(Number);

export function slipstreamGetPoolCalls(tokens: TokenInfo[], tickSpacings: number[]) {
  const calls: Array<{ target: Address; callData: Hex; meta: { a: TokenInfo; b: TokenInfo; tickSpacing: number } }> = [];
  for (let i = 0; i < tokens.length; i++) for (let j = i + 1; j < tokens.length; j++) for (const ts of tickSpacings)
    calls.push({ target: ADDR.SLIPSTREAM_CL_FACTORY, callData: encodeFunctionData({ abi: slipstreamFactoryAbi, functionName: 'getPool', args: [tokens[i].address, tokens[j].address, ts] }), meta: { a: tokens[i], b: tokens[j], tickSpacing: ts } });
  return calls;
}
export function slipstreamStateCalls(pool: Address) {
  return [
    { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'slot0' }) },
    { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'liquidity' }) },
    { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'fee' }) },
  ];
}
export function slipstreamDecodeState(slot0: Hex, liq: Hex, fee: Hex) {
  const s = decodeFunctionResult({ abi: slipstreamPoolAbi, functionName: 'slot0', data: slot0 }) as readonly [bigint, number, number, number, number, boolean];
  return { sqrtPriceX96: s[0], tick: Number(s[1]), liquidity: decodeFunctionResult({ abi: slipstreamPoolAbi, functionName: 'liquidity', data: liq }) as bigint, feePpm: Number(decodeFunctionResult({ abi: slipstreamPoolAbi, functionName: 'fee', data: fee }) as number) };
}
export function slipstreamQuoteCall(pool: PoolRef, tokenIn: Address, amountIn: bigint) {
  const tokenOut = tokenIn.toLowerCase() === pool.token0.toLowerCase() ? pool.token1 : pool.token0;
  return { target: ADDR.SLIPSTREAM_QUOTER_V2, callData: encodeFunctionData({ abi: slipstreamQuoterV2Abi, functionName: 'quoteExactInputSingle', args: [{ tokenIn, tokenOut, amountIn, tickSpacing: pool.tickSpacing!, sqrtPriceLimitX96: 0n }] }) };
}
export function slipstreamDecodeQuote(data: Hex) {
  const r = decodeFunctionResult({ abi: slipstreamQuoterV2Abi, functionName: 'quoteExactInputSingle', data }) as readonly [bigint, bigint, number, bigint];
  return { amountOut: r[0], sqrtPriceX96After: r[1], ticksCrossed: Number(r[2]), gasEstimate: r[3] };
}
export const slipstreamPoolMetaCalls = (pool: Address) => [
  { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'token0' }) },
  { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'token1' }) },
  { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'factory' }) },
  { target: pool, callData: encodeFunctionData({ abi: slipstreamPoolAbi, functionName: 'tickSpacing' }) },
];
