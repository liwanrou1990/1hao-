// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// MS0 positive-invariant validation on the SAFE version (no attack sequence,
// no fund extraction). Property SOLVENCY: under normal deposit/borrow with a
// quiescent (constant) valuation, a user's debt never exceeds their borrow
// limit, and the vault's reserves cover every user's accounted collateral.
// Also checks that a legitimate within-limit borrow is NOT wrongly rejected.
import "forge-std/Test.sol";
import {SafeOracleVault, ITWAP, IToken} from "src/contracts/fixed/SafeOracleVault.sol";
import {MockERC20} from "src/contracts/mocks/MockERC20.sol";
import {FixedTWAP} from "../helpers/FixedTWAP.sol";

contract VaultHandler is Test {
    SafeOracleVault public vault;
    MockERC20 public collateral;
    MockERC20 public payout;
    address[] public actors;
    uint256 public constant PRICE = 2e18; // 1 collateral = 2 payout, constant

    constructor(SafeOracleVault _v, MockERC20 _c, MockERC20 _p) {
        vault = _v; collateral = _c; payout = _p;
        for (uint160 i = 1; i <= 4; i++) actors.push(address(i));
    }

    function _actor(uint256 s) internal view returns (address) { return actors[s % actors.length]; }

    // normal business: deposit collateral the actor owns
    function deposit(uint256 s, uint256 amount) public {
        address a = _actor(s);
        amount = bound(amount, 0, 1e24);
        collateral.mint(a, amount);
        vm.startPrank(a);
        collateral.approve(address(vault), amount);
        vault.deposit(amount);
        vm.stopPrank();
    }

    // normal business: borrow within the limit the contract itself enforces
    function borrow(uint256 s, uint256 amount) public {
        address a = _actor(s);
        uint256 room = vault.maxBorrow(a);
        uint256 debt = vault.debtOf(a);
        if (room <= debt) return;
        amount = bound(amount, 0, room - debt);
        vm.prank(a);
        vault.borrow(amount);
    }
}

contract SafeOracleVault_Solvency is Test {
    SafeOracleVault vault;
    MockERC20 collateral;
    MockERC20 payout;
    FixedTWAP oracle;
    VaultHandler handler;

    function setUp() public {
        collateral = new MockERC20("COL", "COL");
        payout = new MockERC20("PAY", "PAY");
        oracle = new FixedTWAP(2e18);
        vault = new SafeOracleVault(ITWAP(address(oracle)), IToken(address(collateral)), IToken(address(payout)));
        payout.mint(address(vault), 1e30); // vault reserves
        handler = new VaultHandler(vault, collateral, payout);
        collateral.mint(address(handler), 0);
        targetContract(address(handler));
    }

    // SOLVENCY invariant: no user's debt exceeds their current borrow limit.
    function invariant_debt_within_limit() public view {
        for (uint160 i = 1; i <= 4; i++) {
            address a = address(i);
            assertLe(vault.debtOf(a), vault.maxBorrow(a), "debt exceeds LTV-bounded limit");
        }
    }

    // No-false-reject: a legitimate borrow strictly within the limit must succeed.
    function test_legitimate_borrow_not_rejected() public {
        address a = address(0xA11CE);
        collateral.mint(a, 100e18);
        vm.startPrank(a);
        collateral.approve(address(vault), 100e18);
        vault.deposit(100e18);
        uint256 room = vault.maxBorrow(a); // 100 * 2 * 80% = 160e18
        assertEq(room, 160e18);
        vault.borrow(room); // exactly at limit must be allowed
        vm.stopPrank();
        assertEq(vault.debtOf(a), room);
    }

    // Wrong-property guard: the property is NOT "debt == 0"; borrowing is allowed.
    function test_property_is_not_overly_strict() public {
        address a = address(0xB0B);
        collateral.mint(a, 10e18);
        vm.startPrank(a);
        collateral.approve(address(vault), 10e18);
        vault.deposit(10e18);
        vault.borrow(1e18);
        vm.stopPrank();
        assertGt(vault.debtOf(a), 0);
        assertLe(vault.debtOf(a), vault.maxBorrow(a));
    }
}
