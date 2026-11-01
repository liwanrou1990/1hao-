// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// MS0 positive-invariant validation on the SAFE treasury (no attack sequence,
// no fund extraction). Property TREASURY_ACCESS: a privileged outflow is
// additionally bounded by state policy (allowlisted recipient + per-epoch cap),
// and only the governor can move funds. Also checks that a legitimate payout
// (allowlisted recipient, within cap, by governor) is NOT wrongly rejected.
import "forge-std/Test.sol";
import {SafeX100Treasury} from "src/contracts/fixed/SafeX100Treasury.sol";
import {MockERC20} from "src/contracts/mocks/MockERC20.sol";

contract SafeTreasury_Access is Test {
    SafeX100Treasury treasury;
    MockERC20 token;
    address governor = address(this);
    address guardian = address(0x6);
    address recipient = address(0x7);
    uint256 constant CAP = 1_000e18;

    function setUp() public {
        treasury = new SafeX100Treasury(guardian); // deployer is governor
        token = new MockERC20("T", "T");
        token.mint(address(treasury), 10_000e18);
        treasury.setRecipientAllowed(recipient, true);
        treasury.setTokenEpochCap(address(token), CAP);
    }

    // No-false-reject: a legitimate payout within the cap, to an allowlisted
    // recipient, by the governor, succeeds.
    function test_legitimate_payout_not_rejected() public {
        treasury.payToken(address(token), recipient, 400e18);
        assertEq(token.balanceOf(recipient), 400e18);
    }

    // TREASURY_ACCESS bound: outflow to a non-allowlisted recipient is refused.
    function test_non_allowlisted_recipient_refused() public {
        vm.expectRevert(bytes("recipient not allowed"));
        treasury.payToken(address(token), address(0xBAD), 1e18);
    }

    // TREASURY_ACCESS bound: the per-epoch cap limits total outflow; a single
    // proposal cannot drain the asset. (normal calls, no attack)
    function test_epoch_cap_bounds_outflow() public {
        treasury.payToken(address(token), recipient, 600e18);
        vm.expectRevert(bytes("token epoch cap"));
        treasury.payToken(address(token), recipient, 500e18); // 600+500 > 1000 cap
        // within-cap remainder still allowed (no false reject of legitimate use)
        treasury.payToken(address(token), recipient, 400e18);
        assertEq(token.balanceOf(recipient), 1000e18);
        assertLe(token.balanceOf(recipient), CAP);
    }

    // Permission effectiveness: a non-governor cannot move funds.
    function test_non_governor_refused() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert(bytes("not governor"));
        treasury.payToken(address(token), recipient, 1e18);
    }

    // Guardian circuit-breaker: when paused, outflow is refused (bound holds),
    // and resumes after unpause (not permanently over-strict).
    function test_pause_bounds_then_resumes() public {
        vm.prank(guardian);
        treasury.pause(true);
        vm.expectRevert(bytes("paused"));
        treasury.payToken(address(token), recipient, 100e18);
        vm.prank(guardian);
        treasury.pause(false);
        treasury.payToken(address(token), recipient, 100e18);
        assertEq(token.balanceOf(recipient), 100e18);
    }
}
