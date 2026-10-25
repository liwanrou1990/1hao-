import { encodeFunctionData, decodeFunctionResult, getAddress, type Address, type Hex } from 'viem';
import { ADDR, EXPECTED_TOKENS, WATCH_TOKENS } from './addresses.js';
import { erc20Abi, aavePoolAbi, aaveAddressesProviderAbi, uniswapQuoterV2Abi, slipstreamQuoterV2Abi, aerodromeFactoryAbi, slipstreamFactoryAbi } from './abis.js';
import { multicall } from './multicall.js';
import type { ChainClient } from './client.js';
import type { TokenInfo } from '../dex/types.js';

export interface VerifyResult { ok: boolean; checks: Array<{ name: string; ok: boolean; detail: string }>; tokens: Record<string, TokenInfo>; }

/**
 * On-chain verification of every seeded address. Nothing is used unless it passes:
 *  - chainId == 8453
 *  - every contract has bytecode
 *  - token symbol/decimals match expectation (rejects look-alike "fake" tokens)
 *  - Uniswap QuoterV2.factory() == Uniswap factory; Slipstream Quoter.factory() == CL factory
 *  - Aave AddressesProvider.getPool() == Pool and Pool.ADDRESSES_PROVIDER() == provider
 *  - Aave USDC reserve exists and flash loans are enabled (configuration bit 63)
 */
export async function verifyDeployments(cc: ChainClient): Promise<VerifyResult> {
  const checks: VerifyResult['checks'] = [];
  const add = (name: string, ok: boolean, detail: string) => checks.push({ name, ok, detail });
  const chainId = await cc.call('eth_chainId', () => cc.client.getChainId());
  add('chainId', chainId === 8453, `got ${chainId}`);

  const contracts: Array<[string, Address]> = [
    ['UNISWAP_V3_FACTORY', ADDR.UNISWAP_V3_FACTORY], ['UNISWAP_V3_QUOTER_V2', ADDR.UNISWAP_V3_QUOTER_V2],
    ['AERODROME_POOL_FACTORY', ADDR.AERODROME_POOL_FACTORY], ['SLIPSTREAM_CL_FACTORY', ADDR.SLIPSTREAM_CL_FACTORY], ['SLIPSTREAM_QUOTER_V2', ADDR.SLIPSTREAM_QUOTER_V2],
    ['AAVE_POOL', ADDR.AAVE_POOL], ['AAVE_POOL_ADDRESSES_PROVIDER', ADDR.AAVE_POOL_ADDRESSES_PROVIDER], ['MULTICALL3', ADDR.MULTICALL3], ['OP_GAS_PRICE_ORACLE', ADDR.OP_GAS_PRICE_ORACLE],
    ...WATCH_TOKENS.map(t => [t, ADDR[t]] as [string, Address]),
  ];
  for (const [name, a] of contracts) {
    const code = await cc.call('eth_getCode', () => cc.client.getCode({ address: a }));
    add(`code:${name}`, !!code && code !== '0x', `${a} bytecode ${code ? (code.length - 2) / 2 : 0} bytes`);
  }

  // token metadata
  const tokens: Record<string, TokenInfo> = {};
  const tcalls = WATCH_TOKENS.flatMap(t => [
    { target: ADDR[t], callData: encodeFunctionData({ abi: erc20Abi, functionName: 'symbol' }) },
    { target: ADDR[t], callData: encodeFunctionData({ abi: erc20Abi, functionName: 'decimals' }) },
  ]);
  const tres = await multicall(cc, tcalls, 'latest');
  WATCH_TOKENS.forEach((t, i) => {
    const s = tres[i * 2], d = tres[i * 2 + 1];
    let symbol = '?', decimals = -1;
    try { symbol = decodeFunctionResult({ abi: erc20Abi, functionName: 'symbol', data: s.returnData }) as string; decimals = Number(decodeFunctionResult({ abi: erc20Abi, functionName: 'decimals', data: d.returnData })); } catch { /* keep defaults */ }
    const exp = EXPECTED_TOKENS[t];
    const ok = s.success && d.success && symbol === exp.symbol && decimals === exp.decimals;
    add(`token:${t}`, ok, `${ADDR[t]} symbol=${symbol} decimals=${decimals} expected ${exp.symbol}/${exp.decimals}`);
    if (ok) tokens[t] = { symbol, address: getAddress(ADDR[t]), decimals };
  });

  // factory links
  const linkCalls = [
    { target: ADDR.UNISWAP_V3_QUOTER_V2, callData: encodeFunctionData({ abi: uniswapQuoterV2Abi, functionName: 'factory' }) },
    { target: ADDR.SLIPSTREAM_QUOTER_V2, callData: encodeFunctionData({ abi: slipstreamQuoterV2Abi, functionName: 'factory' }) },
    { target: ADDR.AAVE_POOL_ADDRESSES_PROVIDER, callData: encodeFunctionData({ abi: aaveAddressesProviderAbi, functionName: 'getPool' }) },
    { target: ADDR.AAVE_POOL, callData: encodeFunctionData({ abi: aavePoolAbi, functionName: 'ADDRESSES_PROVIDER' }) },
    { target: ADDR.AAVE_POOL, callData: encodeFunctionData({ abi: aavePoolAbi, functionName: 'FLASHLOAN_PREMIUM_TOTAL' }) },
    { target: ADDR.AAVE_POOL, callData: encodeFunctionData({ abi: aavePoolAbi, functionName: 'getReserveData', args: [ADDR.USDC] }) },
    { target: ADDR.AERODROME_POOL_FACTORY, callData: encodeFunctionData({ abi: aerodromeFactoryAbi, functionName: 'allPoolsLength' }) },
    { target: ADDR.SLIPSTREAM_CL_FACTORY, callData: encodeFunctionData({ abi: slipstreamFactoryAbi, functionName: 'tickSpacings' }) },
  ];
  const lr = await multicall(cc, linkCalls, 'latest');
  const dec = <T,>(i: number, abi: any, fn: string): T | undefined => { try { return lr[i].success ? decodeFunctionResult({ abi, functionName: fn, data: lr[i].returnData }) as T : undefined; } catch { return undefined; } };
  const uf = dec<Address>(0, uniswapQuoterV2Abi, 'factory');
  add('link:uniswapQuoter.factory', !!uf && uf.toLowerCase() === ADDR.UNISWAP_V3_FACTORY.toLowerCase(), `quoter.factory()=${uf}`);
  const sf = dec<Address>(1, slipstreamQuoterV2Abi, 'factory');
  add('link:slipstreamQuoter.factory', !!sf && sf.toLowerCase() === ADDR.SLIPSTREAM_CL_FACTORY.toLowerCase(), `quoter.factory()=${sf}`);
  const ap = dec<Address>(2, aaveAddressesProviderAbi, 'getPool');
  add('link:aaveProvider.getPool', !!ap && ap.toLowerCase() === ADDR.AAVE_POOL.toLowerCase(), `provider.getPool()=${ap}`);
  const pp = dec<Address>(3, aavePoolAbi, 'ADDRESSES_PROVIDER');
  add('link:aavePool.ADDRESSES_PROVIDER', !!pp && pp.toLowerCase() === ADDR.AAVE_POOL_ADDRESSES_PROVIDER.toLowerCase(), `pool.ADDRESSES_PROVIDER()=${pp}`);
  const prem = dec<bigint>(4, aavePoolAbi, 'FLASHLOAN_PREMIUM_TOTAL');
  add('aave:FLASHLOAN_PREMIUM_TOTAL', prem !== undefined, `premium=${prem} bps`);
  const rd = dec<any>(5, aavePoolAbi, 'getReserveData');
  const flEnabled = rd ? ((BigInt(rd.configuration) >> 63n) & 1n) === 1n : false;
  add('aave:USDC reserve', !!rd && rd.aTokenAddress !== '0x0000000000000000000000000000000000000000', rd ? `aToken=${rd.aTokenAddress} flashLoanEnabled=${flEnabled}` : 'no reserve data');
  add('aave:USDC flashloan enabled', flEnabled, `configuration bit63=${flEnabled}`);
  const apl = dec<bigint>(6, aerodromeFactoryAbi, 'allPoolsLength');
  add('aerodrome:factory.allPoolsLength', apl !== undefined && apl > 0n, `${apl}`);
  const ts = dec<readonly number[]>(7, slipstreamFactoryAbi, 'tickSpacings');
  add('slipstream:factory.tickSpacings', !!ts && ts.length > 0, `${ts?.join(',')}`);

  return { ok: checks.every(c => c.ok), checks, tokens };
}

export function assertHex(h: Hex | undefined, what: string): Hex { if (!h || h === '0x') throw new Error(`empty result for ${what}`); return h; }
