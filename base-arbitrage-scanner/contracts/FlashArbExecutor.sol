// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * FlashArbExecutor — PHASE 1: deployed ONLY on a local mainnet fork and invoked ONLY via eth_call.
 * It borrows `amount` of `asset` from Aave V3 (flashLoanSimple), executes a closed-loop path of swaps directly
 * against Uniswap V3 / Slipstream (callback-style) and Aerodrome v2 (transfer-then-swap) pools, repays
 * principal + premium and reports the profit. If the path cannot repay, it reverts with Unprofitable(received, owed)
 * so the simulator can record the exact shortfall. No owner, no withdrawals: this contract is not for mainnet use.
 */
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
}
interface IAavePool {
    function flashLoanSimple(address receiverAddress, address asset, uint256 amount, bytes calldata params, uint16 referralCode) external;
}
interface IUniV3Pool {
    function swap(address recipient, bool zeroForOne, int256 amountSpecified, uint160 sqrtPriceLimitX96, bytes calldata data) external returns (int256 amount0, int256 amount1);
    function token0() external view returns (address);
}
interface IAeroPool {
    function getAmountOut(uint256 amountIn, address tokenIn) external view returns (uint256);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
    function token0() external view returns (address);
}

contract FlashArbExecutor {
    struct Leg { address pool; uint8 kind; address tokenIn; address tokenOut; } // kind 0 = UniV3-style CL pool, 1 = Aerodrome v2 pool

    uint160 internal constant MIN_SQRT_RATIO = 4295128739;
    uint160 internal constant MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342;

    address public immutable aavePool;
    uint256 private _lastOut;
    uint256 private _lastPremium;
    address private _expectedPool;

    error Unprofitable(uint256 received, uint256 owed);
    error BadCallback(address caller, address expected);
    error PathMustEndInAsset(address last, address asset);
    error TransferFailed(address token);

    constructor(address _aavePool) { aavePool = _aavePool; }

    /// @notice Simulation entrypoint. Call with eth_call. Returns final asset amount, premium charged and signed profit.
    function simulate(address asset, uint256 amount, Leg[] calldata legs) external returns (uint256 amountOut, uint256 premium, int256 profit) {
        IAavePool(aavePool).flashLoanSimple(address(this), asset, amount, abi.encode(legs), 0);
        amountOut = _lastOut; premium = _lastPremium;
        profit = int256(amountOut) - int256(amount + premium);
    }

    /// @dev Aave V3 IFlashLoanSimpleReceiver
    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params) external returns (bool) {
        require(msg.sender == aavePool, "caller not aave pool");
        require(initiator == address(this), "bad initiator");
        Leg[] memory legs = abi.decode(params, (Leg[]));
        require(legs.length > 0, "empty path");
        if (legs[legs.length - 1].tokenOut != asset) revert PathMustEndInAsset(legs[legs.length - 1].tokenOut, asset);
        uint256 cur = amount;
        for (uint256 i = 0; i < legs.length; i++) cur = _swap(legs[i], cur);
        uint256 owed = amount + premium;
        if (cur < owed) revert Unprofitable(cur, owed);
        _lastOut = cur; _lastPremium = premium;
        _approve(asset, aavePool, owed);
        return true;
    }

    function _swap(Leg memory leg, uint256 amountIn) internal returns (uint256 out) {
        uint256 before = IERC20(leg.tokenOut).balanceOf(address(this));
        if (leg.kind == 0) {
            bool zeroForOne = leg.tokenIn == IUniV3Pool(leg.pool).token0();
            _expectedPool = leg.pool;
            IUniV3Pool(leg.pool).swap(address(this), zeroForOne, int256(amountIn), zeroForOne ? MIN_SQRT_RATIO + 1 : MAX_SQRT_RATIO - 1, abi.encode(leg.tokenIn));
            _expectedPool = address(0);
        } else {
            bool in0 = leg.tokenIn == IAeroPool(leg.pool).token0();
            uint256 amountOut = IAeroPool(leg.pool).getAmountOut(amountIn, leg.tokenIn);
            _transfer(leg.tokenIn, leg.pool, amountIn);
            IAeroPool(leg.pool).swap(in0 ? 0 : amountOut, in0 ? amountOut : 0, address(this), new bytes(0));
        }
        out = IERC20(leg.tokenOut).balanceOf(address(this)) - before;
    }

    /// @dev Uniswap V3 / Slipstream swap callback: pay the pool exactly what it asks for.
    function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external {
        if (msg.sender != _expectedPool) revert BadCallback(msg.sender, _expectedPool);
        address tokenIn = abi.decode(data, (address));
        uint256 pay = amount0Delta > 0 ? uint256(amount0Delta) : uint256(amount1Delta);
        _transfer(tokenIn, msg.sender, pay);
    }

    function _transfer(address token, address to, uint256 value) internal {
        (bool ok, bytes memory d) = token.call(abi.encodeWithSelector(0xa9059cbb, to, value));
        if (!ok || (d.length != 0 && !abi.decode(d, (bool)))) revert TransferFailed(token);
    }
    function _approve(address token, address spender, uint256 value) internal {
        (bool ok, bytes memory d) = token.call(abi.encodeWithSelector(0x095ea7b3, spender, value));
        if (!ok || (d.length != 0 && !abi.decode(d, (bool)))) revert TransferFailed(token);
    }
}
