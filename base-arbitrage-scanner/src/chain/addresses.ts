import type { Address } from 'viem';

/**
 * Canonical Base mainnet (chainId 8453) deployment addresses.
 * These are SEEDS, not trusted facts: `verify.ts` must confirm each one on-chain at startup
 * (bytecode present, factory()/ADDRESSES_PROVIDER links, token metadata) before any pool is used.
 * Every address can be overridden via env (e.g. ADDR_UNISWAP_V3_FACTORY=0x...) if a protocol redeploys.
 * Source of the seeds: protocol deployment docs (Uniswap "Deployments", Aerodrome "Contracts", Aave "Addresses") as published.
 */
function env(name: string, def: Address): Address {
  const v = process.env[name];
  return (v && /^0x[0-9a-fA-F]{40}$/.test(v) ? v : def) as Address;
}

export const ADDR = {
  // Tokens
  WETH: env('ADDR_WETH', '0x4200000000000000000000000000000000000006'),
  USDC: env('ADDR_USDC', '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'),
  USDT: env('ADDR_USDT', '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2'),
  cbBTC: env('ADDR_CBBTC', '0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf'),
  // Uniswap V3
  UNISWAP_V3_FACTORY: env('ADDR_UNISWAP_V3_FACTORY', '0x33128a8fC17869897dcE68Ed026d694621f6FDfD'),
  UNISWAP_V3_QUOTER_V2: env('ADDR_UNISWAP_V3_QUOTER_V2', '0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a'),
  // Aerodrome classic AMM (v2-style)
  AERODROME_POOL_FACTORY: env('ADDR_AERODROME_POOL_FACTORY', '0x420DD381b31aEf6683db6B902084cB0FFECe40Da'),
  AERODROME_ROUTER: env('ADDR_AERODROME_ROUTER', '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43'),
  // Aerodrome Slipstream (concentrated liquidity)
  SLIPSTREAM_CL_FACTORY: env('ADDR_SLIPSTREAM_CL_FACTORY', '0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A'),
  SLIPSTREAM_QUOTER_V2: env('ADDR_SLIPSTREAM_QUOTER_V2', '0x254cF9E1E6e233aa1AC962CB9B05b2cfeAaE15b0'),
  // Aave V3
  AAVE_POOL_ADDRESSES_PROVIDER: env('ADDR_AAVE_POOL_ADDRESSES_PROVIDER', '0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D'),
  AAVE_POOL: env('ADDR_AAVE_POOL', '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5'),
  // Infra
  MULTICALL3: env('ADDR_MULTICALL3', '0xcA11bde05977b3631167028862bE2a173976CA11'),
  OP_GAS_PRICE_ORACLE: env('ADDR_OP_GAS_PRICE_ORACLE', '0x420000000000000000000000000000000000000F'),
} as const;

export interface TokenMeta { symbol: string; decimals: number; address: Address; }
export const EXPECTED_TOKENS: Record<string, { symbol: string; decimals: number }> = {
  WETH: { symbol: 'WETH', decimals: 18 },
  USDC: { symbol: 'USDC', decimals: 6 },
  USDT: { symbol: 'USDT', decimals: 6 },
  cbBTC: { symbol: 'cbBTC', decimals: 8 },
};
export const WATCH_TOKENS = ['WETH', 'USDC', 'USDT', 'cbBTC'] as const;
export const UNISWAP_V3_FEE_TIERS = [100, 500, 3000, 10000] as const;  // ppm (hundredths of a bip)
export const SLIPSTREAM_TICK_SPACINGS_FALLBACK = [1, 50, 100, 200, 2000] as const; // used only if factory.tickSpacings() fails
