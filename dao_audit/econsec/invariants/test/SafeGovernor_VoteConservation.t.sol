// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// MS0 positive-invariant validation on the SAFE governor (no attack sequence).
// Property VOTE_CONSERVATION: for any single proposal, total recorded weight
// (for + against) never exceeds the token supply snapshotted for that proposal,
// and no address's weight is counted twice. Also checks that a legitimate
// single vote is NOT wrongly rejected, and that a snapshot correctly freezes
// weight against later transfers (the safe behaviour the property depends on).
import "forge-std/Test.sol";
import {SafeX100Governor, IPastVotes} from "src/contracts/fixed/SafeX100Governor.sol";
import {CheckpointToken} from "src/contracts/fixed/CheckpointToken.sol";

contract SafeGovernor_VoteConservation is Test {
    SafeX100Governor gov;
    CheckpointToken token;
    address[] voters;

    function setUp() public {
        token = new CheckpointToken("VOTE", "VOTE");
        gov = new SafeX100Governor(IPastVotes(address(token)), 1 days);
        for (uint160 i = 1; i <= 5; i++) {
            voters.push(address(i));
            token.mint(address(i), 100e18 * i);
        }
        vm.roll(block.number + 1); // make the mint checkpoints "past"
    }

    function _propose() internal returns (uint256 id) {
        vm.prank(voters[0]);
        id = gov.propose(address(0xDEAD), 0, "");
        vm.roll(block.number + 1); // snapshotBlock = proposeBlock-1 already in the past
    }

    // VOTE_CONSERVATION: recorded for+against <= snapshot supply, across all voters.
    function test_total_weight_within_snapshot_supply() public {
        uint256 id = _propose();
        for (uint256 i = 0; i < voters.length; i++) {
            vm.prank(voters[i]);
            gov.castVote(id, i % 2 == 0);
        }
        (uint256 forV, uint256 againstV, , , , , ) = gov.proposalInfo(id);
        assertLe(forV + againstV, token.totalSupply(), "recorded weight exceeds snapshot supply");
    }

    // No double counting: a second vote by the same voter reverts.
    function test_double_vote_rejected() public {
        uint256 id = _propose();
        vm.prank(voters[1]);
        gov.castVote(id, true);
        vm.prank(voters[1]);
        vm.expectRevert(bytes("already voted"));
        gov.castVote(id, true);
    }

    // No-false-reject: a legitimate single vote succeeds and records the snapshot weight.
    function test_legitimate_vote_not_rejected() public {
        uint256 id = _propose();
        uint256 w = token.getPastVotes(voters[2], block.number - 2);
        vm.prank(voters[2]);
        gov.castVote(id, true);
        (uint256 forV, , , , , , ) = gov.proposalInfo(id);
        assertEq(forV, w);
        assertGt(w, 0);
    }

    // The property depends on snapshots freezing weight: moving tokens AFTER the
    // snapshot must not change the recordable weight (safe behaviour).
    function test_snapshot_freezes_weight() public {
        uint256 id = _propose();
        uint256 before = token.getPastVotes(voters[3], block.number - 2);
        uint256 bal = token.balanceOf(voters[3]);
        vm.prank(voters[3]);
        token.transfer(voters[4], bal);
        vm.prank(voters[3]);
        gov.castVote(id, true);
        (uint256 forV, , , , , , ) = gov.proposalInfo(id);
        assertEq(forV, before, "snapshot weight should be frozen at proposal time");
    }
}
