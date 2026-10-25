// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CheckpointToken} from "../contracts/fixed/CheckpointToken.sol";
import {SafeX100Governor, IPastVotes} from "../contracts/fixed/SafeX100Governor.sol";

/// REGRESSION — [FIX-1]. The seat-hijack exploit must now revert.
contract SeatHijackFixedTest is Test {
    CheckpointToken token;
    SafeX100Governor gov;
    address foundation = makeAddr("foundation");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new CheckpointToken("X100", "X100");
        gov = new SafeX100Governor(IPastVotes(address(token)), 2 days);
        address[] memory m = new address[](1);
        m[0] = foundation;
        gov.initializeCouncil(m);
    }

    function test_reinit_reverts_and_attacker_not_seated() public {
        address[] memory evil = new address[](1);
        evil[0] = attacker;
        vm.prank(attacker);
        vm.expectRevert(bytes("not owner"));
        gov.initializeCouncil(evil);

        // Even the owner cannot re-initialize.
        vm.expectRevert(bytes("already initialized"));
        gov.initializeCouncil(evil);

        assertFalse(gov.isCouncil(attacker), "attacker never seated");
    }
}
