// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CheckpointToken} from "../contracts/fixed/CheckpointToken.sol";
import {SafeX100Governor, IPastVotes} from "../contracts/fixed/SafeX100Governor.sol";

interface IFlashBorrower2 { function onFlashLoan(address token, uint256 amount) external; }

contract CPLender {
    CheckpointToken public token;
    constructor(CheckpointToken t) { token = t; }
    function flashLoan(uint256 amount) external {
        uint256 b = token.balanceOf(address(this));
        require(b >= amount, "liquidity");
        token.transfer(msg.sender, amount);
        IFlashBorrower2(msg.sender).onFlashLoan(address(token), amount);
        require(token.balanceOf(address(this)) >= b, "not repaid");
    }
}

contract FlashVoteBotFixed is IFlashBorrower2 {
    CPLender lender; CheckpointToken token; SafeX100Governor gov; uint256 pid;
    constructor(CPLender l, CheckpointToken t, SafeX100Governor g) { lender=l; token=t; gov=g; }
    function attack(uint256 _pid, uint256 amt) external { pid=_pid; lender.flashLoan(amt); }
    function onFlashLoan(address t, uint256 amt) external {
        gov.castVote(pid, true);
        CheckpointToken(t).transfer(address(lender), amt);
    }
}

/// REGRESSION — [FIX-2]. Flash-loaned balance carries ZERO voting weight,
/// because weight is read from a past-block snapshot.
contract FlashVoteFixedTest is Test {
    CheckpointToken token;
    CPLender lender;
    SafeX100Governor gov;
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new CheckpointToken("X100", "X100");
        gov = new SafeX100Governor(IPastVotes(address(token)), 2 days);
        lender = new CPLender(token);
        token.mint(address(lender), 500_000e18);
        token.mint(address(0xdead), 500_000e18);
        vm.roll(block.number + 5); // make checkpoints historical
    }

    function test_flash_loan_vote_has_zero_weight() public {
        uint256 id = gov.propose(address(0), 0, "");
        vm.roll(block.number + 1); // advance past the snapshot block

        FlashVoteBotFixed bot = new FlashVoteBotFixed(lender, token, gov);
        vm.prank(attacker);
        bot.attack(id, 500_000e18);

        (uint256 forVotes,,,,,,) = gov.proposalInfo(id);
        assertEq(forVotes, 0, "EVIDENCE: flash-loaned votes have zero weight");
    }
}
