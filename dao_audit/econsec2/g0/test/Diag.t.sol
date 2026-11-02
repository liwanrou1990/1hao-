// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {TestToken, ReferenceVault, BuggyTeachingVault} from "src/Vaults.sol";

// Diagnostic only: print concrete round-trip numbers for both vaults so we can
// see whether the teaching bug actually creates a detectable round-trip gain.
contract Diag is Test {
    function test_diag_roundtrip() public {
        TestToken asset = new TestToken();
        ReferenceVault ref = new ReferenceVault(IERC20(address(asset)));
        BuggyTeachingVault bug = new BuggyTeachingVault(IERC20(address(asset)));
        address s = address(0x5EED); address a = address(0xA11CE);
        uint256 amt = 7e18;
        for (uint256 k = 0; k < 2; k++) {
            address v = k == 0 ? address(ref) : address(bug);
            asset.mint(s, 1000e18); asset.mint(a, amt);
            vm.startPrank(s); asset.approve(v, type(uint256).max); ReferenceVault(v).deposit(1000e18, s); vm.stopPrank();
            vm.startPrank(a); asset.approve(v, type(uint256).max);
            uint256 shares = ReferenceVault(v).deposit(amt, a);
            uint256 out = ReferenceVault(v).redeem(shares, a, a);
            vm.stopPrank();
            emit log_named_string("vault", k == 0 ? "reference" : "buggy");
            emit log_named_uint("deposit", amt);
            emit log_named_uint("shares", shares);
            emit log_named_uint("assetsOut", out);
            emit log_named_int("gain", int256(out) - int256(amt));
        }
    }
}
