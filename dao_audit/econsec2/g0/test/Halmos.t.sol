// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {TestToken, ReferenceVault, BuggyTeachingVault} from "src/Vaults.sol";

// G0 bounded symbolic check (halmos) of property P3 on both vaults.
// P3: convertToAssets(convertToShares(a)) <= a  (rounding must favour the vault).
// halmos explores symbolic `a`; result recorded as-is (PASS / counterexample /
// INCONCLUSIVE). No hardcoded answer.
contract HalmosRef is Test {
    ReferenceVault vault;
    TestToken asset;
    function setUp() public {
        asset = new TestToken();
        vault = new ReferenceVault(IERC20(address(asset)));
        asset.mint(address(this), 1000e18);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1000e18, address(this));
    }
    function check_P3_rounding_favours_vault(uint256 a) public view {
        vm.assume(a <= 1e24);
        uint256 back = vault.convertToAssets(vault.convertToShares(a));
        assert(back <= a);
    }
}

contract HalmosBuggy is Test {
    BuggyTeachingVault vault;
    TestToken asset;
    function setUp() public {
        asset = new TestToken();
        vault = new BuggyTeachingVault(IERC20(address(asset)));
        asset.mint(address(this), 1000e18);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1000e18, address(this));
    }
    function check_P3_rounding_favours_vault(uint256 a) public view {
        vm.assume(a <= 1e24);
        uint256 back = vault.convertToAssets(vault.convertToShares(a));
        assert(back <= a);
    }
}
