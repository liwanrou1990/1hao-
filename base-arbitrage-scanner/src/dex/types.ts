import type { Address } from 'viem';

export type DexKind = 'UNISWAP_V3' | 'AERODROME_V2' | 'AERODROME_CL';

export interface PoolRef {
  dex: DexKind;
  address: Address;
  token0: Address;
  token1: Address;
  symbol0: string;
  symbol1: string;
  decimals0: number;
  decimals1: number;
  /** swap fee in parts-per-million (Uniswap 500 = 0.05%). For Aerodrome CL this is the current dynamic fee(). */
  feePpm: number;
  tickSpacing?: number;   // CL pools
  stable?: boolean;       // Aerodrome v2
  label: string;          // e.g. "UNISWAP_V3 WETH/USDC 0.05%"
}

export interface PoolState {
  pool: Address;
  block: bigint;
  // concentrated liquidity pools
  sqrtPriceX96?: bigint;
  tick?: number;
  liquidity?: bigint;
  // constant-product / stable pools
  reserve0?: bigint;
  reserve1?: bigint;
  feePpm: number;
  /** approximate TVL in USD derived from reserves/liquidity and reference prices (for filtering only) */
  tvlUsd?: number;
}

export interface Quote {
  pool: Address;
  tokenIn: Address;
  tokenOut: Address;
  amountIn: bigint;
  amountOut: bigint;
  ok: boolean;
  error?: string;
  /** price impact in bps: (midPriceBefore - executionPrice)/midPriceBefore, computed from the pool's own state */
  priceImpactBps?: number;
  sqrtPriceX96After?: bigint;
  gasEstimate?: bigint;
  feePaid?: bigint;         // tokenIn units consumed by the pool fee (amountIn * feePpm / 1e6)
}

export interface FlashLoanInfo {
  asset: Address;
  enabled: boolean;
  premiumBps: number;          // FLASHLOAN_PREMIUM_TOTAL in bps (Aave stores bps directly, e.g. 5 = 0.05%)
  availableLiquidity: bigint;  // asset units sitting in the aToken
  aToken?: Address;
  block: bigint;
}

export interface GasInfo {
  block: bigint;
  baseFeePerGas: bigint;       // wei
  maxPriorityFeePerGas: bigint;// wei (estimate)
  l1FeeWeiForTypicalTx: bigint;// OP-stack L1 data fee estimate for the arb tx calldata
  ethUsdc: number;             // USDC per ETH, from the deepest WETH/USDC pool at this block
}

export interface TokenInfo { symbol: string; address: Address; decimals: number; }

/** A market data source: on-chain (viem) or offline synthetic fixture. All prices are per-block. */
export interface MarketSource {
  readonly kind: 'ONCHAIN' | 'OFFLINE_FIXTURE';
  tokens(): Record<string, TokenInfo>;
  latestBlock(): Promise<bigint>;
  blockTimestamp(block: bigint): Promise<number>;
  discoverPools(): Promise<PoolRef[]>;
  readStates(pools: PoolRef[], block: bigint): Promise<Map<Address, PoolState>>;
  /** Batch exact-input quotes evaluated at a block. Each entry is independent (state is NOT chained). */
  quoteExactInputBatch(reqs: Array<{ pool: PoolRef; tokenIn: Address; amountIn: bigint }>, block: bigint): Promise<Quote[]>;
  flashLoanInfo(asset: Address, block: bigint): Promise<FlashLoanInfo>;
  gasInfo(block: bigint, calldataBytes: number): Promise<GasInfo>;
}
