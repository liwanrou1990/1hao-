// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// G0 feasibility fixtures (authorized, local-isolated-EVM only).
//  * ReferenceVault: thin wrapper over the PUBLIC OpenZeppelin ERC4626 v4.9.6
//    reference implementation. Used for POSITIVE verification.
//  * BuggyTeachingVault: SELF-WRITTEN teaching vault with ONE intentional,
//    openly documented accounting defect. Used only to check whether the tools
//    can show a property FAILS. Its defect is known and documented here; it is
//    NOT an unknown-vulnerability discovery. DO NOT DEPLOY.

import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20, IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

contract TestToken is ERC20 {
    constructor() ERC20("Asset", "AST") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

/// PUBLIC REFERENCE: unmodified OZ ERC4626 behaviour.
contract ReferenceVault is ERC4626 {
    constructor(IERC20 asset_) ERC20("refVault", "rVLT") ERC4626(asset_) {}
}

/// SELF-WRITTEN TEACHING FIXTURE — intentional defect, documented in the open.
///
/// DEFECT (accounting inconsistency, documented teaching bug): the
/// shares->assets conversion OVER-CREDITS redeemers by a fixed 1%, instead of
/// the fair, round-DOWN conversion ERC-4626 mandates for assets paid to users.
/// Effect: a deposit-then-redeem round trip pays out MORE assets than were
/// deposited (purchasing power created from nothing, funded by other holders).
/// This is a self-authored teaching bug with a known cause, NOT a discovered
/// vulnerability. DO NOT DEPLOY.
contract BuggyTeachingVault is ERC4626 {
    constructor(IERC20 asset_) ERC20("buggyVault", "bVLT") ERC4626(asset_) {}

    function _convertToAssets(uint256 shares, Math.Rounding rounding)
        internal
        view
        override
        returns (uint256)
    {
        uint256 fair = super._convertToAssets(shares, rounding);
        return fair + fair / 100; // BUG: +1% over-credit on every redemption
    }
}
