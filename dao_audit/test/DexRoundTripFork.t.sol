// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";

// FULL two-leg atomic round trip simulated on a fork of live Base state:
// start with USDC -> buy WETH (pool A) -> sell WETH back to USDC (pool B) ->
// verify the FINAL USDC balance via balanceOf and report the net. This proves
// the COMPLETE transaction (both legs), not a single swap. Read-only fork; no
// transaction is broadcast; gated by RUN_FORK so the offline suite skips it.
//
// Router verified: Uniswap V3 SwapRouter02 on Base (0x2626...) — confirmed
// executable in DexSwapFork (fill matched QuoterV2 to the unit). Cross-DEX
// legs require the counterpart router verified from official docs; this test
// uses two distinct Uniswap pools (fee 500 and fee 3000) to validate the full
// round-trip + balanceOf mechanism without guessing an unverified address.

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}

contract DexRoundTripForkTest is Test {
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481; // Uni V3 SwapRouter02 (Base)

    function _swap(address tin, address tout, uint24 fee, uint256 amtIn) internal returns (uint256) {
        IERC20(tin).approve(ROUTER, amtIn);
        return ISwapRouter02(ROUTER).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams(tin, tout, fee, address(this), amtIn, 0, 0));
    }

    function test_full_roundtrip_balance_verified() public {
        if (!vm.envOr("RUN_FORK", false)) { vm.skip(true); return; }
        vm.createSelectFork(vm.envOr("FORK_BASE_RPC", string("https://mainnet.base.org")));

        uint256 startUsdc = vm.envOr("RT_USDC", uint256(2000)) * 1e6; // default $2000
        uint24 feeBuy = uint24(vm.envOr("RT_FEE_BUY", uint256(500)));
        uint24 feeSell = uint24(vm.envOr("RT_FEE_SELL", uint256(3000)));

        deal(USDC, address(this), startUsdc);
        uint256 usdc0 = IERC20(USDC).balanceOf(address(this));

        // Leg 1: USDC -> WETH (pool A)
        uint256 wethOut = _swap(USDC, WETH, feeBuy, startUsdc);
        assertGt(wethOut, 0, "leg1 produced 0 WETH");
        // Leg 2: WETH -> USDC (pool B)  [full round trip]
        uint256 usdcBack = _swap(WETH, USDC, feeSell, wethOut);
        assertGt(usdcBack, 0, "leg2 produced 0 USDC");

        uint256 usdc1 = IERC20(USDC).balanceOf(address(this));
        // EVIDENCE: final balanceOf reflects BOTH legs
        assertEq(usdc1, usdc0 - startUsdc + usdcBack, "balanceOf != round-trip accounting");

        int256 netUsdc6 = int256(usdcBack) - int256(startUsdc);  // gross round-trip (6dp)
        emit log_named_uint("start_usdc_6dp", startUsdc);
        emit log_named_uint("weth_mid_18dp", wethOut);
        emit log_named_uint("end_usdc_back_6dp", usdcBack);
        emit log_named_int("roundtrip_gross_usdc_6dp", netUsdc6);
        // A same-asset round trip through fee'd pools must lose money (no free arb)
        assertLt(usdcBack, startUsdc, "unexpected: round trip profitable before costs");
    }
}
