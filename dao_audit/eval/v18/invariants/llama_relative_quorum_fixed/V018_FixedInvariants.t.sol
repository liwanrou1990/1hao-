// SPDX-License-Identifier: MIT
pragma solidity 0.8.19;

// V0.18 defensive invariant check on the FIXED LlamaRelativeQuorum
// (llamaxyz/llama @ c053b865). Local isolated EVM only. Asserts the public fix:
// validateActionCreation (which resets the quorum snapshot) can only be called
// by the strategy's LlamaCore. The vulnerable version is not exercised.
import "forge-std/Test.sol";
import {LlamaRelativeQuorum} from "src/strategies/LlamaRelativeQuorum.sol";
import {ActionInfo} from "src/lib/Structs.sol";
import {ILlamaStrategy} from "src/interfaces/ILlamaStrategy.sol";

contract V018_FixedInvariants is Test {
    LlamaRelativeQuorum strategy;
    address stranger = address(0xBEEF);

    function setUp() public {
        // logic contract; initializers disabled, so llamaCore stays unset (address(0))
        strategy = new LlamaRelativeQuorum();
    }

    // B-invariant: an arbitrary caller cannot overwrite the per-action
    // approval/disapproval supply snapshot that defines the quorum.
    function test_B_validateActionCreation_rejects_non_core() public {
        ActionInfo memory info = ActionInfo({
            id: 1, creator: stranger, creatorRole: 1, strategy: ILlamaStrategy(address(strategy)),
            target: address(0), value: 0, data: ""
        });
        assertEq(address(strategy.llamaCore()), address(0));
        vm.prank(stranger);
        vm.expectRevert(LlamaRelativeQuorum.OnlyLlamaCore.selector);
        strategy.validateActionCreation(info);
    }
}
