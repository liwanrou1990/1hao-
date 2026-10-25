// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";

// Local EVM simulation of a REAL DEX swap on a fork of live Base state, to
// verify the executable quote by the actual token balance change. Read-only
// against mainnet (fork is local); no transaction is broadcast. Gated by the
// RUN_FORK env flag so the normal offline suite skips it.

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function decimals() external view returns (uint8);
}

interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}

interface IQuoterV2 {
    struct QuoteExactInputSingleParams {
        address tokenIn; address tokenOut; uint256 amountIn; uint24 fee; uint160 sqrtPriceLimitX96;
    }
    function quoteExactInputSingle(QuoteExactInputSingleParams calldata)
        external returns (uint256 amountOut, uint160, uint32, uint256);
}

contract DexSwapForkTest is Test {
    // Base mainnet addresses
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481; // SwapRouter02
    address constant QUOTER = 0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a; // QuoterV2

    function test_fork_swap_matches_quote_and_moves_balance() public {
        if (!vm.envOr("RUN_FORK", false)) {
            vm.skip(true);
            return;
        }
        string memory rpc = vm.envOr("FORK_BASE_RPC", string("https://mainnet.base.org"));
        vm.createSelectFork(rpc);

        uint256 amountIn = 1e18; // 1 WETH
        uint24 fee = 500;

        // executable quote
        (uint256 quoted,,,) = IQuoterV2(QUOTER).quoteExactInputSingle(
            IQuoterV2.QuoteExactInputSingleParams(WETH, USDC, amountIn, fee, 0));
        assertGt(quoted, 0, "quoter returned 0");

        // fund + approve + swap (real pool math on forked state)
        deal(WETH, address(this), amountIn);
        uint256 usdcBefore = IERC20(USDC).balanceOf(address(this));
        IERC20(WETH).approve(ROUTER, amountIn);
        uint256 received = ISwapRouter02(ROUTER).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams(WETH, USDC, fee, address(this), amountIn, 0, 0));

        uint256 usdcAfter = IERC20(USDC).balanceOf(address(this));
        // EVIDENCE: real balance delta equals the swap output
        assertEq(usdcAfter - usdcBefore, received, "balance delta != swap return");
        assertGt(received, 0, "received 0 USDC");
        // executable quote matches actual fill within 1%
        uint256 diff = received > quoted ? received - quoted : quoted - received;
        assertLt(diff * 10000 / quoted, 100, "fill deviates >1% from quote");
        emit log_named_uint("quoted_usdc_6dp", quoted);
        emit log_named_uint("filled_usdc_6dp", received);
    }
}
