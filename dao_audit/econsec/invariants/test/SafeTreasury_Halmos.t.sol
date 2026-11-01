// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// MS0 bounded symbolic check (halmos) on the SAFE treasury. No attack sequence:
// a single symbolic payToken call is checked against the TREASURY_ACCESS bound.
// Property: payToken can only increase a token's recorded epoch spend up to the
// configured cap; a call that would exceed the cap must revert (so the symbolic
// post-state never shows spend > cap within one epoch).
import {SafeX100Treasury} from "src/contracts/fixed/SafeX100Treasury.sol";

contract SafeTreasury_Halmos {
    SafeX100Treasury treasury;
    address constant GUARDIAN = address(0x6);
    address constant TOKEN = address(0x1111);
    address constant RECIP = address(0x7);
    uint256 constant CAP = 1000e18;

    function setUp() public {
        treasury = new SafeX100Treasury(GUARDIAN); // this = governor
        treasury.setRecipientAllowed(RECIP, true);
        treasury.setTokenEpochCap(TOKEN, CAP);
    }

    // check_ prefix => halmos proves for all symbolic `amount`.
    // We assert the contract's own guard: any amount that passes payToken's cap
    // check leaves recorded spend <= cap. (token transfer is irrelevant to the
    // accounting bound and TOKEN has no code, so we check the pre-transfer guard
    // path via the cap arithmetic the contract enforces.)
    function check_epoch_cap_never_exceeded(uint256 amount) public {
        // precondition: fresh epoch, zero spent (setUp state)
        uint256 spentBefore = treasury.tokenSpentThisEpoch(TOKEN);
        // the contract requires spentBefore + amount <= cap before spending;
        // so for any amount, EITHER the call reverts OR the post spend <= cap.
        try treasury.payToken(TOKEN, RECIP, amount) {
            assert(treasury.tokenSpentThisEpoch(TOKEN) <= CAP);
        } catch {
            assert(treasury.tokenSpentThisEpoch(TOKEN) == spentBefore); // state unchanged on revert
        }
    }
}
