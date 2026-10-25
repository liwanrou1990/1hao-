import { encodeFunctionData, decodeFunctionResult, type Address, type Hex } from 'viem';
import { multicall3Abi } from './abis.js';
import { ADDR } from './addresses.js';
import type { ChainClient } from './client.js';

export interface Call { target: Address; callData: Hex; }
export interface CallResult { success: boolean; returnData: Hex; }

/** Execute calls through Multicall3.aggregate3 at a given block (allowFailure=true), chunked. */
export async function multicall(cc: ChainClient, calls: Call[], block: bigint | 'latest', chunk = 60): Promise<CallResult[]> {
  const out: CallResult[] = [];
  for (let i = 0; i < calls.length; i += chunk) {
    const slice = calls.slice(i, i + chunk);
    const data = encodeFunctionData({ abi: multicall3Abi, functionName: 'aggregate3', args: [slice.map(c => ({ target: c.target, allowFailure: true, callData: c.callData }))] });
    const res = await cc.call('multicall3.aggregate3', () => cc.client.call({ to: ADDR.MULTICALL3, data, ...(block === 'latest' ? {} : { blockNumber: block }) }));
    if (!res.data) throw new Error('multicall returned empty data');
    const decoded = decodeFunctionResult({ abi: multicall3Abi, functionName: 'aggregate3', data: res.data }) as readonly { success: boolean; returnData: Hex }[];
    out.push(...decoded.map(d => ({ success: d.success, returnData: d.returnData })));
  }
  return out;
}
