// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CheckpointToken} from "../contracts/fixed/CheckpointToken.sol";
import {SafeX100Governor, IPastVotes} from "../contracts/fixed/SafeX100Governor.sol";
import {SafeX100Treasury} from "../contracts/fixed/SafeX100Treasury.sol";

/// REGRESSION — [FIX-3]. Execution is blocked until the timelock elapses.
contract TimelockFixedTest is Test {
    CheckpointToken token;
    SafeX100Governor gov;
    SafeX100Treasury treasury;
    address whale = makeAddr("whale");
    address guardian = makeAddr("guardian");
    uint256 constant DELAY = 2 days;

    function setUp() public {
        token = new CheckpointToken("X100", "X100");
        gov = new SafeX100Governor(IPastVotes(address(token)), DELAY);
        treasury = new SafeX100Treasury(guardian);
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));
        token.mint(whale, 300_000e18);
        token.mint(address(0xdead), 700_000e18);
        vm.roll(block.number + 2);
    }

    function test_execute_blocked_until_timelock() public {
        uint256 id = gov.propose(address(0xBEEF), 0, "");
        vm.roll(block.number + 1);
        vm.prank(whale);
        gov.castVote(id, true);

        // Close voting.
        vm.warp(block.timestamp + 3 days + 1);
        gov.queue(id);

        // Immediately after queue: timelock not elapsed -> revert.
        vm.expectRevert(bytes("timelock"));
        gov.execute(id);

        // A guardian still has the full DELAY window to react (e.g. pause).
        (, , , , uint64 eta, bool queued, ) = gov.proposalInfo(id);
        assertTrue(queued, "queued");
        assertGe(eta, block.timestamp + DELAY - 2, "EVIDENCE: >= 2-day reaction window exists");
    }
}
