// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// G0 property verification (authorized, local isolated EVM). The SAME property
// logic runs against the PUBLIC reference vault and against the self-written
// teaching vault. We record whatever forge reports; nothing is hardcoded to
// pass or fail. Property sources are cited inline.
import "forge-std/Test.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {TestToken, ReferenceVault, BuggyTeachingVault} from "src/Vaults.sol";

// ---- shared property harness, parameterized over a deployed vault ----
abstract contract VaultProperties is Test {
    TestToken asset;
    ERC4626 vault;
    address alice = address(0xA11CE);
    address seeder = address(0x5EED);

    function _deploy() internal virtual;

    function setUp() public {
        asset = new TestToken();
        _deploy();
        // normal business: seed the vault with a prior depositor (realistic non-empty state)
        asset.mint(seeder, 1_000e18);
        vm.startPrank(seeder);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1_000e18, seeder);
        vm.stopPrank();
    }

    // P2 round-trip no-gain (ERC-4626 security requirement + conservation of
    // purchasing power): a user who deposits `amount` and immediately redeems
    // all received shares MUST NOT get back more than `amount`. Also exercises
    // legitimate deposit/withdraw behaviour (normal business, no attack).
    function test_P2_roundtrip_no_gain(uint256 amount) public {
        amount = bound(amount, 1e6, 100e18);
        asset.mint(alice, amount);
        vm.startPrank(alice);
        asset.approve(address(vault), type(uint256).max);
        uint256 shares = vault.deposit(amount, alice);
        uint256 assetsOut = vault.redeem(shares, alice, alice);
        vm.stopPrank();
        assertLe(assetsOut, amount, "P2: redeem returned more assets than deposited");
    }

    // P2b no-false-reject: a legitimate normal deposit succeeds and mints shares.
    function test_P2b_legitimate_deposit_not_rejected() public {
        asset.mint(alice, 10e18);
        vm.startPrank(alice);
        asset.approve(address(vault), type(uint256).max);
        uint256 shares = vault.deposit(10e18, alice);
        vm.stopPrank();
        assertGt(shares, 0, "legitimate deposit minted 0 shares");
    }

    // P3 conversion/rounding boundary (ERC-4626 rounding rules: assets paid to
    // users round DOWN): converting assets->shares->assets must not exceed the
    // original assets. (Does NOT assume totalAssets == token balance.)
    function test_P3_rounding_favours_vault(uint256 a) public view {
        a = bound(a, 1e6, 100e18);
        uint256 shares = vault.convertToShares(a);
        uint256 back = vault.convertToAssets(shares);
        assertLe(back, a, "P3: convertToAssets(convertToShares(a)) > a (rounding favours user)");
    }

    // P3b monotonicity: more assets in never converts to fewer shares.
    function test_P3b_convert_monotonic(uint256 a1, uint256 d) public view {
        a1 = bound(a1, 1e6, 50e18);
        d = bound(d, 0, 50e18);
        assertLe(vault.convertToShares(a1), vault.convertToShares(a1 + d), "P3b: convertToShares not monotonic");
    }
}

// ---- P1 share-accounting consistency, as a stateful invariant ----
contract VaultActor is Test {
    ERC4626 public vault;
    TestToken public asset;
    address[] public holders;
    constructor(ERC4626 _v, TestToken _a) {
        vault = _v; asset = _a;
        for (uint160 i = 1; i <= 3; i++) holders.push(address(0x1000 + i));
    }
    function deposit(uint256 s, uint256 amt) public {
        address h = holders[s % holders.length];
        amt = bound(amt, 0, 1e21);
        asset.mint(h, amt);
        vm.startPrank(h);
        asset.approve(address(vault), amt);
        vault.deposit(amt, h);
        vm.stopPrank();
    }
    function redeem(uint256 s, uint256 shr) public {
        address h = holders[s % holders.length];
        uint256 bal = vault.balanceOf(h);
        if (bal == 0) return;
        shr = bound(shr, 0, bal);
        vm.prank(h);
        vault.redeem(shr, h, h);
    }
    function holderAt(uint256 i) external view returns (address) { return holders[i]; }
    function nHolders() external view returns (uint256) { return holders.length; }
}

abstract contract ShareAccounting is Test {
    TestToken asset;
    ERC4626 vault;
    VaultActor actor;
    function _deploy() internal virtual;
    function setUp() public {
        asset = new TestToken();
        _deploy();
        actor = new VaultActor(vault, asset);
        targetContract(address(actor));
    }
    // P1: sum of share balances over all holders equals totalSupply of shares.
    // Source: ERC-20 supply definition (ERC-4626 shares are ERC-20). Does not
    // reference the asset token balance at all.
    function invariant_P1_shares_sum_equals_supply() public view {
        uint256 sum;
        for (uint256 i = 0; i < actor.nHolders(); i++) sum += vault.balanceOf(actor.holderAt(i));
        assertEq(sum, vault.totalSupply(), "P1: sum of share balances != totalSupply");
    }
}

// ---- concrete suites: reference (expect hold) vs buggy (expect violation) ----
contract Ref_Properties is VaultProperties {
    function _deploy() internal override { vault = ERC4626(address(new ReferenceVault(IERC20(address(asset))))); }
}
contract Buggy_Properties is VaultProperties {
    function _deploy() internal override { vault = ERC4626(address(new BuggyTeachingVault(IERC20(address(asset))))); }
}
contract Ref_ShareAccounting is ShareAccounting {
    function _deploy() internal override { vault = ERC4626(address(new ReferenceVault(IERC20(address(asset))))); }
}
contract Buggy_ShareAccounting is ShareAccounting {
    function _deploy() internal override { vault = ERC4626(address(new BuggyTeachingVault(IERC20(address(asset))))); }
}
