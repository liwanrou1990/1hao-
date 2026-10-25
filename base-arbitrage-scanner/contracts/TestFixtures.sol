// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
/* Synthetic protocol pieces used ONLY by the local-EVM integration test (never on mainnet). */

contract MockERC20 {
    string public name; string public symbol; uint8 public decimals; uint256 public totalSupply;
    mapping(address => uint256) public balanceOf; mapping(address => mapping(address => uint256)) public allowance;
    constructor(string memory n, string memory s, uint8 d) { name = n; symbol = s; decimals = d; }
    function mint(address to, uint256 v) external { balanceOf[to] += v; totalSupply += v; }
    function transfer(address to, uint256 v) external returns (bool) { balanceOf[msg.sender] -= v; balanceOf[to] += v; return true; }
    function approve(address sp, uint256 v) external returns (bool) { allowance[msg.sender][sp] = v; return true; }
    function transferFrom(address f, address t, uint256 v) external returns (bool) { allowance[f][msg.sender] -= v; balanceOf[f] -= v; balanceOf[t] += v; return true; }
}

interface IERC20Min { function balanceOf(address) external view returns (uint256); function transfer(address, uint256) external returns (bool); function transferFrom(address, address, uint256) external returns (bool); }
interface IFlashLoanSimpleReceiver { function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params) external returns (bool); }

/// Minimal Aave-V3-like pool: flashLoanSimple with FLASHLOAN_PREMIUM_TOTAL in bps.
contract MockAavePool {
    uint128 public FLASHLOAN_PREMIUM_TOTAL;
    constructor(uint128 premiumBps) { FLASHLOAN_PREMIUM_TOTAL = premiumBps; }
    function flashLoanSimple(address receiver, address asset, uint256 amount, bytes calldata params, uint16) external {
        uint256 premium = amount * FLASHLOAN_PREMIUM_TOTAL / 10000;
        require(IERC20Min(asset).transfer(receiver, amount), "liquidity");
        require(IFlashLoanSimpleReceiver(receiver).executeOperation(asset, amount, premium, msg.sender, params), "receiver false");
        require(IERC20Min(asset).transferFrom(receiver, address(this), amount + premium), "repay failed");
    }
}

/// Minimal Aerodrome-v2-style volatile pool (x*y=k, fee in bps) with the same external surface the executor uses.
contract SimpleAeroPool {
    address public token0; address public token1; uint256 public reserve0; uint256 public reserve1; uint256 public feeBps; bool public stable = false;
    constructor(address t0, address t1, uint256 _feeBps) { (token0, token1) = t0 < t1 ? (t0, t1) : (t1, t0); feeBps = _feeBps; }
    function sync() external { reserve0 = IERC20Min(token0).balanceOf(address(this)); reserve1 = IERC20Min(token1).balanceOf(address(this)); }
    function getReserves() external view returns (uint256, uint256, uint256) { return (reserve0, reserve1, block.timestamp); }
    function getAmountOut(uint256 amountIn, address tokenIn) public view returns (uint256) {
        amountIn -= amountIn * feeBps / 10000;
        (uint256 rA, uint256 rB) = tokenIn == token0 ? (reserve0, reserve1) : (reserve1, reserve0);
        return amountIn * rB / (rA + amountIn);
    }
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata) external {
        uint256 b0 = IERC20Min(token0).balanceOf(address(this)); uint256 b1 = IERC20Min(token1).balanceOf(address(this));
        uint256 in0 = b0 > reserve0 ? b0 - reserve0 : 0; uint256 in1 = b1 > reserve1 ? b1 - reserve1 : 0;
        require(in0 > 0 || in1 > 0, "no input");
        if (amount0Out > 0) { require(amount0Out <= getAmountOut(in1, token1), "K"); IERC20Min(token0).transfer(to, amount0Out); }
        if (amount1Out > 0) { require(amount1Out <= getAmountOut(in0, token0), "K"); IERC20Min(token1).transfer(to, amount1Out); }
        reserve0 = IERC20Min(token0).balanceOf(address(this)); reserve1 = IERC20Min(token1).balanceOf(address(this));
    }
}

interface IUniV3PoolMint { function mint(address recipient, int24 tickLower, int24 tickUpper, uint128 amount, bytes calldata data) external returns (uint256, uint256); function initialize(uint160 sqrtPriceX96) external; }

/// Provides liquidity to a real Uniswap V3 pool (core bytecode) by implementing the mint callback.
contract TestLiquidityProvider {
    function provide(address pool, address t0, address t1, int24 lower, int24 upper, uint128 liquidity) external {
        IUniV3PoolMint(pool).mint(address(this), lower, upper, liquidity, abi.encode(t0, t1));
    }
    function uniswapV3MintCallback(uint256 amount0Owed, uint256 amount1Owed, bytes calldata data) external {
        (address t0, address t1) = abi.decode(data, (address, address));
        if (amount0Owed > 0) IERC20Min(t0).transfer(msg.sender, amount0Owed);
        if (amount1Owed > 0) IERC20Min(t1).transfer(msg.sender, amount1Owed);
    }
}
