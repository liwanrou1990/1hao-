// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {CheckpointToken} from "../contracts/fixed/CheckpointToken.sol";
import {SafeX100Governor, IPastVotes} from "../contracts/fixed/SafeX100Governor.sol";
import {SafeX100Treasury} from "../contracts/fixed/SafeX100Treasury.sol";

interface IFlashBorrower2 { function onFlashLoan(address token, uint256 amount) external; }

/// Flash lender for the checkpointed governance token.
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

contract FixedFlashBot is IFlashBorrower2 {
    CPLender lender; CheckpointToken token; SafeX100Governor gov; uint256 pid;
    constructor(CPLender l, CheckpointToken t, SafeX100Governor g) { lender=l; token=t; gov=g; }
    function voteWithLoan(uint256 _pid, uint256 amount) external { pid=_pid; lender.flashLoan(amount); }
    function onFlashLoan(address t, uint256 amount) external {
        gov.castVote(pid, true);
        CheckpointToken(t).transfer(address(lender), amount);
    }
}

/// VAULT FUND-FLOW — REMEDIATED. The SAME attack is run against the fixed
/// contracts and must be BLOCKED: no unauthorized USDC leaves the treasury.
/// On-chain assertions prove the vault balance is preserved at 5,000,000.
contract FundFlowX100FixedTest is Test {
    uint256 constant VAULT_START = 5_000_000e18;
    uint256 constant GOV_SUPPLY  = 1_000_000e18;
    uint256 constant LENDER_POOL = 600_000e18;
    uint256 constant DELAY = 2 days;

    MockERC20 usdc;
    CheckpointToken gt;
    CPLender lender;
    SafeX100Governor gov;
    SafeX100Treasury treasury;

    address attacker = makeAddr("attacker");
    address receiver = makeAddr("receiver");
    address whale = makeAddr("whale");
    address guardian = makeAddr("guardian");

    function setUp() public {
        usdc = new MockERC20("USD Coin (SIMULATED)", "USDC");
        gt = new CheckpointToken("X100 Governance", "X100");
        gov = new SafeX100Governor(IPastVotes(address(gt)), DELAY);
        treasury = new SafeX100Treasury(guardian);
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));

        usdc.mint(address(treasury), VAULT_START);

        lender = new CPLender(gt);
        gt.mint(address(lender), LENDER_POOL);
        gt.mint(whale, 300_000e18);                 // legit holder (>20% quorum)
        gt.mint(address(0xdead), GOV_SUPPLY - LENDER_POOL - 300_000e18);
        vm.roll(block.number + 5);                  // make checkpoints historical
    }

    function _vault() internal view returns (uint256) { return usdc.balanceOf(address(treasury)); }
    function _conserved() internal view returns (bool) {
        uint256 sum = usdc.balanceOf(address(treasury)) + usdc.balanceOf(attacker)
            + usdc.balanceOf(receiver) + usdc.balanceOf(address(gov)) + usdc.balanceOf(address(this));
        return sum == usdc.totalSupply() && usdc.totalSupply() == VAULT_START;
    }

    /// [FIX-2] Flash-loaned votes carry zero weight -> quorum cannot be reached.
    function test_01_fixed_flashvote_has_zero_weight() public {
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        uint256 id = gov.propose(address(usdc), 0, drain);
        vm.roll(block.number + 1);
        FixedFlashBot bot = new FixedFlashBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, LENDER_POOL);
        (uint256 forVotes,,,,,,) = gov.proposalInfo(id);
        console2.log("[FIX-2] forVotes from flash loan:", forVotes / 1e18);
        assertEq(forVotes, 0, "FIX-2: flash-loaned votes = 0 weight");
        // Cannot queue without quorum.
        vm.warp(block.timestamp + 3 days + 1);
        vm.expectRevert(bytes("no quorum"));
        gov.queue(id);
        assertEq(_vault(), VAULT_START, "vault untouched");
    }

    /// [FIX-3] Even a legitimately passed proposal cannot execute before timelock.
    function test_02_fixed_timelock_blocks_immediate_execution() public {
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        uint256 id = gov.propose(address(usdc), 0, drain);
        vm.roll(block.number + 1);
        vm.prank(whale);
        gov.castVote(id, true);                      // legit quorum
        vm.warp(block.timestamp + 3 days + 1);
        gov.queue(id);
        vm.expectRevert(bytes("timelock"));
        gov.execute(id);                             // blocked: eta not reached
        assertEq(_vault(), VAULT_START, "FIX-3: vault intact during timelock window");
    }

    /// [FIX-4] Treasury refuses arbitrary drains even from the governor.
    function test_03_fixed_treasury_blocks_arbitrary_drain() public {
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        // Arbitrary execute to a non-allowlisted target (the USDC token) reverts.
        vm.prank(address(gov));
        vm.expectRevert(bytes("target not allowed"));
        treasury.execute(address(usdc), 0, drain);
        // Dedicated payout to a non-allowlisted recipient reverts.
        vm.prank(address(gov));
        vm.expectRevert(bytes("recipient not allowed"));
        treasury.payToken(address(usdc), attacker, VAULT_START);
        assertEq(_vault(), VAULT_START, "FIX-4: vault fully preserved");
    }

    /// Combined: the full kill chain is blocked; funds never leave the vault.
    function test_04_fixed_combined_attack_blocked() public {
        uint256 vault0 = _vault();
        assertEq(vault0, VAULT_START, "init vault = 5,000,000");
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        vm.prank(attacker);
        uint256 id = gov.propose(address(usdc), 0, drain);
        vm.roll(block.number + 1);
        FixedFlashBot bot = new FixedFlashBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, LENDER_POOL);           // zero weight
        vm.warp(block.timestamp + 3 days + 1);
        vm.expectRevert(bytes("no quorum"));         // chain breaks here
        gov.queue(id);

        assertEq(_vault(), VAULT_START, "ASSERT final vault still = 5,000,000");
        assertEq(usdc.balanceOf(receiver), 0, "ASSERT receiver still 0");
        assertTrue(_conserved(), "ASSERT conservation; no funds moved");
        console2.log("[FIXED] vault preserved:", _vault() / 1e18);
        console2.log("[FIXED] receiver:", usdc.balanceOf(receiver) / 1e18);
    }
}
