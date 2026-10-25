import { parseAbi } from 'viem';

export const erc20Abi = parseAbi([
  'function symbol() view returns (string)',
  'function name() view returns (string)',
  'function decimals() view returns (uint8)',
  'function totalSupply() view returns (uint256)',
  'function balanceOf(address) view returns (uint256)',
]);

export const uniswapV3FactoryAbi = parseAbi([
  'function getPool(address tokenA, address tokenB, uint24 fee) view returns (address)',
  'function owner() view returns (address)',
  'function feeAmountTickSpacing(uint24 fee) view returns (int24)',
]);

export const uniswapV3PoolAbi = parseAbi([
  'function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality, uint16 observationCardinalityNext, uint8 feeProtocol, bool unlocked)',
  'function liquidity() view returns (uint128)',
  'function fee() view returns (uint24)',
  'function tickSpacing() view returns (int24)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
  'function factory() view returns (address)',
]);

// Slipstream CLPool: slot0 has no feeProtocol field; fee() may be dynamic.
export const slipstreamPoolAbi = parseAbi([
  'function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality, uint16 observationCardinalityNext, bool unlocked)',
  'function liquidity() view returns (uint128)',
  'function fee() view returns (uint24)',
  'function unstakedFee() view returns (uint24)',
  'function tickSpacing() view returns (int24)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
  'function factory() view returns (address)',
]);

export const slipstreamFactoryAbi = parseAbi([
  'function getPool(address tokenA, address tokenB, int24 tickSpacing) view returns (address)',
  'function tickSpacings() view returns (int24[])',
  'function tickSpacingToFee(int24 tickSpacing) view returns (uint24)',
  'function allPoolsLength() view returns (uint256)',
]);

export const uniswapQuoterV2Abi = parseAbi([
  'function factory() view returns (address)',
  'struct QuoteExactInputSingleParams { address tokenIn; address tokenOut; uint256 amountIn; uint24 fee; uint160 sqrtPriceLimitX96; }',
  'function quoteExactInputSingle(QuoteExactInputSingleParams params) returns (uint256 amountOut, uint160 sqrtPriceX96After, uint32 initializedTicksCrossed, uint256 gasEstimate)',
]);

export const slipstreamQuoterV2Abi = parseAbi([
  'function factory() view returns (address)',
  'struct QuoteExactInputSingleParams { address tokenIn; address tokenOut; uint256 amountIn; int24 tickSpacing; uint160 sqrtPriceLimitX96; }',
  'function quoteExactInputSingle(QuoteExactInputSingleParams params) returns (uint256 amountOut, uint160 sqrtPriceX96After, uint32 initializedTicksCrossed, uint256 gasEstimate)',
]);

export const aerodromeFactoryAbi = parseAbi([
  'function getPool(address tokenA, address tokenB, bool stable) view returns (address)',
  'function getFee(address pool, bool stable) view returns (uint256)',
  'function isPool(address pool) view returns (bool)',
  'function allPoolsLength() view returns (uint256)',
]);

export const aerodromePoolAbi = parseAbi([
  'function getReserves() view returns (uint256 _reserve0, uint256 _reserve1, uint256 _blockTimestampLast)',
  'function getAmountOut(uint256 amountIn, address tokenIn) view returns (uint256)',
  'function stable() view returns (bool)',
  'function token0() view returns (address)',
  'function token1() view returns (address)',
  'function factory() view returns (address)',
  'function metadata() view returns (uint256 dec0, uint256 dec1, uint256 r0, uint256 r1, bool st, address t0, address t1)',
]);

export const aavePoolAbi = parseAbi([
  'function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)',
  'function FLASHLOAN_PREMIUM_TO_PROTOCOL() view returns (uint128)',
  'function ADDRESSES_PROVIDER() view returns (address)',
  'function getReserveData(address asset) view returns ((uint256 configuration, uint128 liquidityIndex, uint128 currentLiquidityRate, uint128 variableBorrowIndex, uint128 currentVariableBorrowRate, uint128 currentStableBorrowRate, uint40 lastUpdateTimestamp, uint16 id, address aTokenAddress, address stableDebtTokenAddress, address variableDebtTokenAddress, address interestRateStrategyAddress, uint128 accruedToTreasury, uint128 unbacked, uint128 isolationModeTotalDebt))',
  'function getConfiguration(address asset) view returns ((uint256 data))',
  'function getReservesList() view returns (address[])',
]);

export const aaveAddressesProviderAbi = parseAbi([
  'function getPool() view returns (address)',
  'function getPoolDataProvider() view returns (address)',
]);

export const multicall3Abi = parseAbi([
  'struct Call3 { address target; bool allowFailure; bytes callData; }',
  'struct Result { bool success; bytes returnData; }',
  'function aggregate3(Call3[] calls) payable returns (Result[] returnData)',
  'function getBlockNumber() view returns (uint256)',
]);

export const opGasPriceOracleAbi = parseAbi([
  'function getL1Fee(bytes data) view returns (uint256)',
  'function l1BaseFee() view returns (uint256)',
  'function baseFeeScalar() view returns (uint32)',
  'function blobBaseFeeScalar() view returns (uint32)',
]);
