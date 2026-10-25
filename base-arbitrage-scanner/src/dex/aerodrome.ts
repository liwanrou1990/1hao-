import { encodeFunctionData, decodeFunctionResult, type Address, type Hex } from 'viem';
import { aerodromeFactoryAbi, aerodromePoolAbi } from '../chain/abis.js';
import { ADDR } from '../chain/addresses.js';
import type { PoolRef, TokenInfo } from './types.js';

export function aeroGetPoolCalls(tokens: TokenInfo[]) {
  const calls: Array<{ target: Address; callData: Hex; meta: { a: TokenInfo; b: TokenInfo; stable: boolean } }> = [];
  for (let i = 0; i < tokens.length; i++) for (let j = i + 1; j < tokens.length; j++) for (const stable of [false, true])
    calls.push({ target: ADDR.AERODROME_POOL_FACTORY, callData: encodeFunctionData({ abi: aerodromeFactoryAbi, functionName: 'getPool', args: [tokens[i].address, tokens[j].address, stable] }), meta: { a: tokens[i], b: tokens[j], stable } });
  return calls;
}
export const aeroFeeCall = (pool: Address, stable: boolean) => ({ target: ADDR.AERODROME_POOL_FACTORY, callData: encodeFunctionData({ abi: aerodromeFactoryAbi, functionName: 'getFee', args: [pool, stable] }) });
export const aeroIsPoolCall = (pool: Address) => ({ target: ADDR.AERODROME_POOL_FACTORY, callData: encodeFunctionData({ abi: aerodromeFactoryAbi, functionName: 'isPool', args: [pool] }) });
export const decodeFeeBps = (d: Hex) => Number(decodeFunctionResult({ abi: aerodromeFactoryAbi, functionName: 'getFee', data: d }) as bigint);
export const decodeBool = (d: Hex) => decodeFunctionResult({ abi: aerodromeFactoryAbi, functionName: 'isPool', data: d }) as boolean;

export function aeroStateCalls(pool: Address, stable: boolean) {
  return [
    { target: pool, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'getReserves' }) },
    aeroFeeCall(pool, stable),
  ];
}
export function aeroDecodeState(reserves: Hex, fee: Hex) {
  const r = decodeFunctionResult({ abi: aerodromePoolAbi, functionName: 'getReserves', data: reserves }) as readonly [bigint, bigint, bigint];
  return { reserve0: r[0], reserve1: r[1], feePpm: decodeFeeBps(fee) * 100 };
}
export function aeroQuoteCall(pool: PoolRef, tokenIn: Address, amountIn: bigint) {
  return { target: pool.address, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'getAmountOut', args: [amountIn, tokenIn] }) };
}
export const aeroDecodeQuote = (d: Hex) => decodeFunctionResult({ abi: aerodromePoolAbi, functionName: 'getAmountOut', data: d }) as bigint;
export const aeroPoolMetaCalls = (pool: Address) => [
  { target: pool, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'token0' }) },
  { target: pool, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'token1' }) },
  { target: pool, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'factory' }) },
  { target: pool, callData: encodeFunctionData({ abi: aerodromePoolAbi, functionName: 'stable' }) },
];
