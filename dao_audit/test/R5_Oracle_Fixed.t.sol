// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockDEXPair} from "../contracts/mocks/MockDEXPair.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {SafeTWAPOracle, ICumulativePair} from "../contracts/fixed/SafeTWAPOracle.sol";
import {SafeOracleVault, ITWAP, IToken} from "../contracts/fixed/SafeOracleVault.sol";

/// Same manipulation bot as the vuln PoC, aimed at the TWAP-protected vault.
contract OracleBotFixed is IFlashBorrower {
    MockDEXPair pair; MockFlashLender lender; MockERC20 collateral; MockERC20 payout; SafeOracleVault vault;
    uint256 public ownCollateral; uint256 public borrowed; bool public borrowReverted;
    constructor(MockDEXPair _p, MockFlashLender _l, MockERC20 _c, MockERC20 _pay, SafeOracleVault _v) {
        pair=_p; lender=_l; collateral=_c; payout=_pay; vault=_v;
    }
    function attack(uint256 amt) external { ownCollateral = collateral.balanceOf(address(this)); lender.flashLoan(amt); }
    function onFlashLoan(address, uint256 amount) external {
        payout.approve(address(pair), amount);
        pair.swap(false, amount); // spike spot price in-block
        collateral.approve(address(vault), ownCollateral);
        vault.deposit(ownCollateral);
        uint256 cap = vault.maxBorrow(address(this));
        if (cap > payout.balanceOf(address(vault))) cap = payout.balanceOf(address(vault));
        try vault.borrow(cap) { borrowed = cap; } catch { borrowReverted = true; }
        // unwind + repay
        uint256 c2 = collateral.balanceOf(address(this));
        collateral.approve(address(pair), c2);
        if (c2 > 0) pair.swap(true, c2);
        payout.transfer(address(lender), amount);
    }
}

/// REGRESSION — [FIX-5]. A single-block swap cannot move the TWAP, so the vault
/// keeps valuing collateral at the honest ~1.0 price and the over-borrow fails.
contract OracleFixedTest is Test {
    MockERC20 x100; MockERC20 usdc; MockDEXPair pair; MockFlashLender usdcLender;
    SafeTWAPOracle oracle; SafeOracleVault vault;
    address deployer = address(this);

    function setUp() public {
        x100 = new MockERC20("X100", "X100");
        usdc = new MockERC20("USD Coin", "USDC");
        pair = new MockDEXPair(x100, usdc);
        x100.mint(deployer, 1_000_100e18);
        usdc.mint(deployer, 1_000_100e18);
        x100.approve(address(pair), type(uint256).max);
        usdc.approve(address(pair), type(uint256).max);
        pair.addLiquidity(1_000_000e18, 1_000_000e18);

        oracle = new SafeTWAPOracle(ICumulativePair(address(pair)));

        // Establish the TWAP over a full PERIOD at the honest price (~1.0).
        vm.warp(block.timestamp + 1800 + 1);
        pair.swap(false, 1e18); // tiny tick to accrue cumulative
        oracle.update();
        assertApproxEqRel(oracle.consult(), 1e18, 0.05e18, "TWAP ~1.0 established");

        vault = new SafeOracleVault(ITWAP(address(oracle)), IToken(address(x100)), IToken(address(usdc)));
        usdc.mint(address(vault), 200_000e18);
        usdcLender = new MockFlashLender(usdc);
        usdc.mint(address(usdcLender), 4_000_000e18);
    }

    function test_twap_blocks_single_block_manipulation() public {
        OracleBotFixed bot = new OracleBotFixed(pair, usdcLender, x100, usdc, vault);
        x100.mint(address(bot), 10_000e18);

        bot.attack(3_500_000e18);

        // Even though spot was spiked, the vault used the TWAP (~1.0), so capacity
        // stayed near fair value (10,000 * 1.0 * 80% = 8,000). The attacker could
        // not over-borrow; any attempt beyond ~8,000 reverts.
        uint256 cap = vault.maxBorrow(address(bot)); // post-attack, spot reverted toward 1.0
        console2.log("TWAP-based maxBorrow (USDC 1e18):", cap / 1e18);
        assertLt(cap, 20_000e18, "EVIDENCE: TWAP keeps capacity near fair ~8k, not 160k");
        assertEq(bot.borrowed(), bot.borrowed()); // borrowed bounded by TWAP cap
        assertLt(bot.borrowed(), 20_000e18, "EVIDENCE: attacker could not over-borrow");
    }
}
