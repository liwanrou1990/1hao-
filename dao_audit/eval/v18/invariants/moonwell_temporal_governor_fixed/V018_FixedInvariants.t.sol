// SPDX-License-Identifier: MIT
pragma solidity 0.8.19;

// V0.18 defensive invariant checks on the FIXED version of TemporalGovernor
// (moonwell-contracts-v2 @ 4580996b). Local isolated EVM only. The checks assert
// that the public fix (`whenPaused` on fastTrackProposalExecution) holds; the
// vulnerable version is not exercised.
import "@forge-std/Test.sol";
import {TemporalGovernor} from "@protocol/core/Governance/TemporalGovernor.sol";
import {ITemporalGovernor} from "@protocol/core/Governance/ITemporalGovernor.sol";

contract V018_FixedInvariants is Test {
    TemporalGovernor gov;
    address owner = address(this);
    address stranger = address(0xBEEF);

    function setUp() public {
        ITemporalGovernor.TrustedSender[] memory senders = new ITemporalGovernor.TrustedSender[](0);
        gov = new TemporalGovernor(address(0xD00D), 1 days, 30 days, senders);
    }

    // C-invariant 1: the emergency path is owner-only.
    function test_C_fastTrack_rejects_non_owner() public {
        vm.prank(stranger);
        vm.expectRevert(bytes("Ownable: caller is not the owner"));
        gov.fastTrackProposalExecution(hex"00");
    }

    // C-invariant 2 (the fix): even the owner cannot bypass the timelock queue
    // unless the governor is paused.
    function test_C_fastTrack_rejects_when_not_paused() public {
        assertFalse(gov.paused());
        vm.expectRevert(bytes("Pausable: not paused"));
        gov.fastTrackProposalExecution(hex"00");
    }
}
