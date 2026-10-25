// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {MockERC20} from "./MockERC20.sol";

/// @title MockDEXPair
/// @notice Constant-product (x*y=k) AMM pair for the LOCAL TEST CHAIN only.
///         Exposes an instantaneous spot price (the manipulable surface) AND a
///         Uniswap-V2-style time-weighted cumulative price (used by the fixed,
///         manipulation-resistant oracle). Simulation only; no mainnet liquidity.
contract MockDEXPair {
    MockERC20 public immutable token0;
    MockERC20 public immutable token1;

    uint112 private reserve0;
    uint112 private reserve1;
    uint32 public blockTimestampLast;

    // Time-weighted cumulative prices (1e18 fixed point * seconds).
    uint256 public price0CumulativeLast;
    uint256 public price1CumulativeLast;

    event Swap(address indexed sender, bool zeroForOne, uint256 amountIn, uint256 amountOut);
    event Sync(uint112 reserve0, uint112 reserve1);

    constructor(MockERC20 _token0, MockERC20 _token1) {
        token0 = _token0;
        token1 = _token1;
        blockTimestampLast = uint32(block.timestamp);
    }

    function getReserves() external view returns (uint112 r0, uint112 r1) {
        return (reserve0, reserve1);
    }

    function addLiquidity(uint256 amount0, uint256 amount1) external {
        token0.transferFrom(msg.sender, address(this), amount0);
        token1.transferFrom(msg.sender, address(this), amount1);
        _sync();
    }

    function swap(bool zeroForOne, uint256 amountIn) external returns (uint256 amountOut) {
        (uint112 r0, uint112 r1) = (reserve0, reserve1);
        uint256 amountInWithFee = amountIn * 997;
        if (zeroForOne) {
            token0.transferFrom(msg.sender, address(this), amountIn);
            amountOut = (amountInWithFee * r1) / (uint256(r0) * 1000 + amountInWithFee);
            token1.transfer(msg.sender, amountOut);
        } else {
            token1.transferFrom(msg.sender, address(this), amountIn);
            amountOut = (amountInWithFee * r0) / (uint256(r1) * 1000 + amountInWithFee);
            token0.transfer(msg.sender, amountOut);
        }
        _sync();
        emit Swap(msg.sender, zeroForOne, amountIn, amountOut);
    }

    /// @notice Naive instantaneous spot price of token0 in token1, 1e18.
    ///         The surface a SAFE integration must NOT read directly.
    function spotPrice0() external view returns (uint256) {
        require(reserve0 > 0, "no liquidity");
        return (uint256(reserve1) * 1e18) / uint256(reserve0);
    }

    /// @notice Cumulative prices extrapolated to the current block (V2 pattern).
    function currentCumulativePrices()
        external
        view
        returns (uint256 cum0, uint256 cum1, uint32 ts)
    {
        ts = uint32(block.timestamp);
        cum0 = price0CumulativeLast;
        cum1 = price1CumulativeLast;
        uint32 dt = ts - blockTimestampLast;
        if (dt > 0 && reserve0 != 0 && reserve1 != 0) {
            cum0 += ((uint256(reserve1) * 1e18) / reserve0) * dt;
            cum1 += ((uint256(reserve0) * 1e18) / reserve1) * dt;
        }
    }

    function _sync() internal {
        uint32 ts = uint32(block.timestamp);
        uint32 dt = ts - blockTimestampLast;
        if (dt > 0 && reserve0 != 0 && reserve1 != 0) {
            // Accumulate the price that held over the elapsed window (old reserves).
            price0CumulativeLast += ((uint256(reserve1) * 1e18) / reserve0) * dt;
            price1CumulativeLast += ((uint256(reserve0) * 1e18) / reserve1) * dt;
        }
        reserve0 = uint112(token0.balanceOf(address(this)));
        reserve1 = uint112(token1.balanceOf(address(this)));
        blockTimestampLast = ts;
        emit Sync(reserve0, reserve1);
    }
}
