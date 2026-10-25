import { encodeFunctionData, decodeFunctionResult, type Address, type Hex } from 'viem';
import { uniswapV3FactoryAbi, uniswapV3PoolAbi, uniswapQuoterV2Abi } from '../chain/abis.js';
import { ADDR, UNISWAP_V3_FEE_TIERS } from '../chain/addresses.js';
import type { PoolRef, TokenInfo } from './types.js';

export function uniV3GetPoolCalls(tokens: TokenInfo[]) {
  const calls: Array<{ target: Address; callData: Hex; meta: { a: TokenInfo; b: TokenInfo; fee: number } }> = [];
  for (let i = 0; i < tokens.length; i++) for (let j = i + 1; j < tokens.length; j++) for (const fee of UNISWAP_V3_FEE_TIERS)
    calls.push({ target: ADDR.UNISWAP_V3_FACTORY, callData: encodeFunctionData({ abi: uniswapV3FactoryAbi, functionName: 'getPool', args: [tokens[i].address, tokens[j].address, fee] }), meta: { a: tokens[i], b: tokens[j], fee } });
  return calls;
}
export function decodeAddress(data: Hex): Address { return decodeFunctionResult({ abi: uniswapV3FactoryAbi, functionName: 'getPool', data }) as Address; }

export function uniV3StateCalls(pool: Address) {
  return [
    { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'slot0' }) },
    { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'liquidity' }) },
    { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'fee' }) },
  ];
}
export function uniV3DecodeState(slot0: Hex, liq: Hex, fee: Hex) {
  const s = decodeFunctionResult({ abi: uniswapV3PoolAbi, functionName: 'slot0', data: slot0 }) as readonly [bigint, number, number, number, number, number, boolean];
  return { sqrtPriceX96: s[0], tick: Number(s[1]), liquidity: decodeFunctionResult({ abi: uniswapV3PoolAbi, functionName: 'liquidity', data: liq }) as bigint, feePpm: Number(decodeFunctionResult({ abi: uniswapV3PoolAbi, functionName: 'fee', data: fee }) as number) };
}

export function uniV3QuoteCall(pool: PoolRef, tokenIn: Address, amountIn: bigint) {
  const tokenOut = tokenIn.toLowerCase() === pool.token0.toLowerCase() ? pool.token1 : pool.token0;
  return { target: ADDR.UNISWAP_V3_QUOTER_V2, callData: encodeFunctionData({ abi: uniswapQuoterV2Abi, functionName: 'quoteExactInputSingle', args: [{ tokenIn, tokenOut, amountIn, fee: pool.feePpm, sqrtPriceLimitX96: 0n }] }) };
}
export function uniV3DecodeQuote(data: Hex) {
  const r = decodeFunctionResult({ abi: uniswapQuoterV2Abi, functionName: 'quoteExactInputSingle', data }) as readonly [bigint, bigint, number, bigint];
  return { amountOut: r[0], sqrtPriceX96After: r[1], ticksCrossed: Number(r[2]), gasEstimate: r[3] };
}
export const uniV3PoolMetaCalls = (pool: Address) => [
  { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'token0' }) },
  { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'token1' }) },
  { target: pool, callData: encodeFunctionData({ abi: uniswapV3PoolAbi, functionName: 'factory' }) },
];
