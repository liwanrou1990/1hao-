// SPDX-License-Identifier: MIT
pragma solidity 0.7.6;
pragma abicoder v2;

// V0.18 defensive invariant checks on the FIXED Nomad Replica
// (nomad-xyz/monorepo @ 0e02cc1f). Local isolated EVM only. Asserts the public
// fix: initialising with the zero root does not pre-approve it, and the zero /
// default root is never an acceptable root. The vulnerable version is not run.
import "forge-std/Test.sol";
import {Replica} from "src/Replica.sol";

contract V018_FixedInvariants is Test {
    Replica replica;
    address updater = address(0x1234);

    function setUp() public {
        replica = new Replica(1000);
    }

    // A-invariant (the fix): zero committed root is NOT pre-approved at init.
    function test_A_zero_root_not_preapproved() public {
        replica.initialize(2000, updater, bytes32(0), 1 hours);
        assertEq(replica.confirmAt(bytes32(0)), 0);
    }

    // D-invariant (the fix): the default/zero root can never authorise message processing.
    function test_D_zero_root_never_acceptable() public {
        replica.initialize(2000, updater, bytes32(0), 1 hours);
        assertFalse(replica.acceptableRoot(bytes32(0)));
    }

    // Sanity: a genuine non-zero trusted root is still pre-approved (fix does not break intended behaviour).
    function test_nonzero_root_preapproved() public {
        bytes32 root = keccak256("trusted");
        replica.initialize(2000, updater, root, 1 hours);
        assertEq(replica.confirmAt(root), 1);
        assertTrue(replica.acceptableRoot(root));
    }

    // Initializer guard: initialize cannot be called twice.
    function test_A_initialize_is_one_shot() public {
        replica.initialize(2000, updater, bytes32(0), 1 hours);
        vm.expectRevert(bytes("Initializable: contract is already initialized"));
        replica.initialize(2000, updater, bytes32(0), 1 hours);
    }
}
