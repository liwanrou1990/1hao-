// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {SafeX100Treasury} from "../contracts/fixed/SafeX100Treasury.sol";

/// REGRESSION — [FIX-4]. A full drain is impossible; only capped, allowlisted
/// payouts to allowlisted recipients succeed.
contract TreasuryFixedTest is Test {
    MockERC20 usdc;
    SafeX100Treasury treasury;
    address governor = address(this); // this test acts as the governor
    address guardian = makeAddr("guardian");
    address attacker = makeAddr("attacker");
    address payroll = makeAddr("payroll");

    function setUp() public {
        usdc = new MockERC20("USD Coin", "USDC");
        treasury = new SafeX100Treasury(guardian); // msg.sender (this) is governor
        usdc.mint(address(treasury), 5_000_000e18);
    }

    function test_arbitrary_drain_to_token_reverts() public {
        // Attempt the vuln path: arbitrary execute calling usdc.transfer(attacker, all).
        bytes memory data = abi.encodeWithSignature("transfer(address,uint256)", attacker, 5_000_000e18);
        vm.expectRevert(bytes("target not allowed"));
        treasury.execute(address(usdc), 0, data);
        assertEq(usdc.balanceOf(attacker), 0, "EVIDENCE: drain via execute blocked");
    }

    function test_paytoken_is_capped_and_allowlisted() public {
        // Full drain via payToken to attacker: recipient not allowlisted -> revert.
        vm.expectRevert(bytes("recipient not allowed"));
        treasury.payToken(address(usdc), attacker, 5_000_000e18);

        // Legit payout: allowlist payroll, set a modest cap.
        treasury.setRecipientAllowed(payroll, true);
        treasury.setTokenEpochCap(address(usdc), 10_000e18);

        // Over-cap payout still reverts.
        vm.expectRevert(bytes("token epoch cap"));
        treasury.payToken(address(usdc), payroll, 5_000_000e18);

        // Within cap succeeds.
        treasury.payToken(address(usdc), payroll, 9_000e18);
        assertEq(usdc.balanceOf(payroll), 9_000e18, "capped payout works");
        assertEq(usdc.balanceOf(address(treasury)), 5_000_000e18 - 9_000e18, "EVIDENCE: >99.8% of treasury remains safe");
    }

    function test_guardian_can_pause() public {
        treasury.setRecipientAllowed(payroll, true);
        treasury.setTokenEpochCap(address(usdc), 10_000e18);
        vm.prank(guardian);
        treasury.pause(true);
        vm.expectRevert(bytes("paused"));
        treasury.payToken(address(usdc), payroll, 1_000e18);
    }
}
